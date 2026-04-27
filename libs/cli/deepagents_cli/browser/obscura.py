"""Lifecycle wrapper around the Obscura headless browser.

Obscura runs as a separate process exposing a Chrome DevTools
Protocol WebSocket. This module owns:

- Locating the binary (env var → PATH → bundled fallback).
- Starting it on a free port with the requested flags.
- Connecting a Playwright client over CDP.
- Cleaning up the subprocess on shutdown (atexit + signal handlers).

The class is **lazy**: construction is cheap; the subprocess starts
on the first ``ensure_started()`` call. That keeps Pack's import
surface fast when an agent never touches the browser.

Errors are surfaced as ``BrowserUnavailable`` with the underlying
cause attached, never silently swallowed — agents should know when
their browser tool is broken so they can pivot.
"""

from __future__ import annotations

import atexit
import logging
import os
import shutil
import socket
import subprocess
import time
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class BrowserUnavailable(RuntimeError):
    """Raised when Obscura can't start or connect.

    Wraps the underlying cause (FileNotFoundError, TimeoutError,
    OSError) so callers can choose to fall back gracefully.
    """


@dataclass
class ObscuraConfig:
    """Tunable parameters for an ``ObscuraBrowser`` instance.

    Attributes:
        binary_path: Explicit path to the ``obscura`` binary. When
            None, looks up via ``OBSCURA_BIN`` env var, then PATH.
        port: TCP port for the CDP WebSocket. ``0`` picks a free
            port. Default 0.
        host: Bind address. Defaults to ``127.0.0.1`` — Obscura
            should never be exposed publicly from a Pack run.
        stealth: Pass ``--stealth`` to enable Obscura's anti-detect
            mode. Defaults to True since most agent tasks are
            scraping-shaped.
        startup_timeout_sec: Max time to wait for the WebSocket to
            accept connections. Default 10s.
        extra_args: Free-form arguments appended to the obscura
            invocation, for power users.
    """

    binary_path: str | None = None
    port: int = 0
    host: str = "127.0.0.1"
    stealth: bool = True
    startup_timeout_sec: float = 10.0
    extra_args: tuple[str, ...] = field(default_factory=tuple)


def _resolve_binary(explicit: str | None) -> str:
    """Find the Obscura binary or raise BrowserUnavailable."""
    if explicit:
        if Path(explicit).is_file():
            return explicit
        raise BrowserUnavailable(f"binary_path does not exist: {explicit}")
    env_path = os.environ.get("OBSCURA_BIN")
    if env_path and Path(env_path).is_file():
        return env_path
    on_path = shutil.which("obscura")
    if on_path:
        return on_path
    raise BrowserUnavailable(
        "obscura binary not found. Install from "
        "https://github.com/h4ckf0r0day/obscura/releases or set "
        "OBSCURA_BIN to its location."
    )


def _free_port(host: str) -> int:
    """Ask the OS for an available TCP port on ``host``."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return sock.getsockname()[1]


def _wait_for_port(host: str, port: int, timeout: float) -> None:
    """Poll ``host:port`` until it accepts a TCP connection.

    Raises BrowserUnavailable on timeout. The polling cadence is
    intentionally tight (50ms) — Obscura starts in ~100-200ms on
    a warm cache, so we want the agent to feel snappy.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with suppress(OSError), socket.create_connection((host, port), timeout=1):
            return
        time.sleep(0.05)
    raise BrowserUnavailable(
        f"Obscura did not accept connections on {host}:{port} within "
        f"{timeout}s. Check the binary or pass --port explicitly."
    )


class ObscuraBrowser:
    """Pack-side wrapper around an Obscura subprocess + Playwright client.

    Use as a context manager when you want strict cleanup:

    ::

        with ObscuraBrowser() as browser:
            page = browser.new_page()
            page.goto("https://example.com")
            text = page.text_content("body")

    Or rely on the atexit handler when the lifecycle should match
    the calling process — that's the default for tool-list usage
    via ``make_obscura_tools``.
    """

    def __init__(self, config: ObscuraConfig | None = None) -> None:
        self.config = config or ObscuraConfig()
        self._process: subprocess.Popen[bytes] | None = None
        self._playwright: Any = None
        self._browser: Any = None
        self._port: int = 0
        self._cleanup_registered = False

    # -- Lifecycle ----------------------------------------------------

    def ensure_started(self) -> None:
        """Start the subprocess + connect Playwright if not already."""
        if self._browser is not None:
            return

        binary = _resolve_binary(self.config.binary_path)
        port = self.config.port or _free_port(self.config.host)
        # Obscura's `serve` subcommand binds to localhost by default
        # and does not accept a --host flag. Keep self.config.host
        # for the connection URL but don't pass it to the binary.
        cmd: list[str] = [binary, "serve", "--port", str(port)]
        if self.config.stealth:
            cmd.append("--stealth")
        cmd.extend(self.config.extra_args)

        logger.info("ObscuraBrowser: starting %s", " ".join(cmd))
        try:
            self._process = subprocess.Popen(  # noqa: S603  # cmd validated above
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise BrowserUnavailable(f"could not spawn obscura: {exc}") from exc

        try:
            _wait_for_port(self.config.host, port, self.config.startup_timeout_sec)
        except BrowserUnavailable:
            self.shutdown()
            raise

        self._port = port
        self._connect_playwright()

        if not self._cleanup_registered:
            atexit.register(self.shutdown)
            self._cleanup_registered = True

    def _connect_playwright(self) -> None:
        """Lazy-import playwright + connect over CDP.

        Playwright is an optional dep so the import lives here, not
        at module top. ImportError gets re-raised as
        ``BrowserUnavailable`` with install hint.
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            self.shutdown()
            raise BrowserUnavailable(
                "playwright not installed. Run `pip install "
                "deepagents-cli[browser]` or "
                "`pip install playwright`."
            ) from exc

        endpoint = f"http://{self.config.host}:{self._port}"
        try:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.connect_over_cdp(endpoint)
        except Exception as exc:  # noqa: BLE001  # surface as BrowserUnavailable
            self.shutdown()
            raise BrowserUnavailable(
                f"could not connect to Obscura at {endpoint}: {exc}"
            ) from exc

    def shutdown(self) -> None:
        """Tear down the Playwright client and subprocess.

        Idempotent — safe to call multiple times. Suppresses
        secondary errors so the original cause (if any) propagates.
        """
        with suppress(Exception):
            if self._browser is not None:
                self._browser.close()
                self._browser = None
        with suppress(Exception):
            if self._playwright is not None:
                self._playwright.stop()
                self._playwright = None
        with suppress(Exception):
            if self._process is not None and self._process.poll() is None:
                self._process.terminate()
                try:
                    self._process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                    self._process.wait(timeout=2)
                self._process = None

    # -- Context-manager sugar ----------------------------------------

    def __enter__(self) -> ObscuraBrowser:
        self.ensure_started()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.shutdown()

    # -- Page operations (delegated to Playwright) --------------------

    def new_page(self) -> Any:
        """Open a new tab; returns the Playwright Page object."""
        self.ensure_started()
        assert self._browser is not None  # narrowed by ensure_started
        # Use the default context — Obscura currently runs one
        # context per session, which matches typical agent usage.
        contexts = self._browser.contexts
        ctx = contexts[0] if contexts else self._browser.new_context()
        return ctx.new_page()

    @property
    def is_running(self) -> bool:
        return self._browser is not None


__all__ = [
    "BrowserUnavailable",
    "ObscuraBrowser",
    "ObscuraConfig",
]
