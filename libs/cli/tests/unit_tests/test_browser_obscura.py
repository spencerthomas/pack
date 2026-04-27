"""Tests for the Obscura native-browser integration."""

from __future__ import annotations

import os
import socket
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from deepagents_cli.browser.obscura import (
    BrowserUnavailable,
    ObscuraBrowser,
    ObscuraConfig,
    _free_port,
    _resolve_binary,
    _wait_for_port,
)


# ---------------------------------------------------------------------------
# _resolve_binary
# ---------------------------------------------------------------------------


def test_resolve_binary_explicit_existing(tmp_path: Path) -> None:
    fake = tmp_path / "obscura"
    fake.write_text("#!/bin/sh\n")
    assert _resolve_binary(str(fake)) == str(fake)


def test_resolve_binary_explicit_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(BrowserUnavailable, match="does not exist"):
        _resolve_binary(str(tmp_path / "nope"))


def test_resolve_binary_env_var(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = tmp_path / "obscura"
    fake.write_text("")
    monkeypatch.setenv("OBSCURA_BIN", str(fake))
    assert _resolve_binary(None) == str(fake)


def test_resolve_binary_falls_back_to_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OBSCURA_BIN", raising=False)
    with patch("shutil.which", return_value="/usr/bin/obscura"):
        assert _resolve_binary(None) == "/usr/bin/obscura"


def test_resolve_binary_not_found_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OBSCURA_BIN", raising=False)
    with patch("shutil.which", return_value=None):
        with pytest.raises(BrowserUnavailable, match="not found"):
            _resolve_binary(None)


# ---------------------------------------------------------------------------
# _free_port + _wait_for_port
# ---------------------------------------------------------------------------


def test_free_port_returns_usable_port() -> None:
    port = _free_port("127.0.0.1")
    assert 1024 < port < 65536


def test_wait_for_port_succeeds_when_open() -> None:
    """Open a real listening socket and confirm _wait_for_port returns."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    host, port = sock.getsockname()
    try:
        _wait_for_port(host, port, timeout=1.0)  # should not raise
    finally:
        sock.close()


def test_wait_for_port_raises_on_timeout() -> None:
    # Port that nothing is listening on. Find one then close it.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    with pytest.raises(BrowserUnavailable, match="did not accept"):
        _wait_for_port("127.0.0.1", port, timeout=0.2)


# ---------------------------------------------------------------------------
# ObscuraBrowser lifecycle (heavily mocked — no real binary)
# ---------------------------------------------------------------------------


@contextmanager
def _patched_obscura(tmp_path: Path) -> Any:
    """Yield a context where ObscuraBrowser thinks it can run.

    Fakes the binary, the subprocess, the port-wait, and the
    Playwright client. Lets us exercise the lifecycle without any
    real network or process.
    """
    fake_bin = tmp_path / "obscura"
    fake_bin.write_text("")

    fake_browser = MagicMock(name="playwright_browser")
    fake_pw = MagicMock(name="playwright_handle")
    fake_pw.chromium.connect_over_cdp.return_value = fake_browser

    fake_proc = MagicMock(name="subprocess.Popen instance")
    fake_proc.poll.return_value = None  # still running

    with (
        patch("deepagents_cli.browser.obscura._resolve_binary", return_value=str(fake_bin)),
        patch("deepagents_cli.browser.obscura._wait_for_port"),
        patch("deepagents_cli.browser.obscura.subprocess.Popen", return_value=fake_proc),
        patch.dict("sys.modules"),
    ):
        # Stub the playwright import so _connect_playwright finds it
        fake_module = MagicMock()
        fake_module.sync_playwright.return_value.start.return_value = fake_pw
        with patch.dict("sys.modules", {"playwright.sync_api": fake_module}):
            yield {
                "process": fake_proc,
                "playwright": fake_pw,
                "browser_obj": fake_browser,
            }


def test_ensure_started_spawns_subprocess_and_connects(tmp_path: Path) -> None:
    with _patched_obscura(tmp_path) as fakes:
        b = ObscuraBrowser()
        b.ensure_started()
        assert b.is_running is True
        # Subprocess was spawned with the right shape
        from deepagents_cli.browser import obscura as obs_mod

        assert obs_mod.subprocess.Popen.called  # type: ignore[attr-defined]
        call_args = obs_mod.subprocess.Popen.call_args[0][0]  # type: ignore[attr-defined]
        assert "serve" in call_args
        assert "--stealth" in call_args
        # Playwright connected
        assert fakes["playwright"].chromium.connect_over_cdp.called


def test_ensure_started_is_idempotent(tmp_path: Path) -> None:
    with _patched_obscura(tmp_path):
        b = ObscuraBrowser()
        b.ensure_started()
        b.ensure_started()  # second call should no-op
        from deepagents_cli.browser import obscura as obs_mod

        # Popen called exactly once
        assert obs_mod.subprocess.Popen.call_count == 1  # type: ignore[attr-defined]


def test_stealth_flag_off(tmp_path: Path) -> None:
    with _patched_obscura(tmp_path):
        b = ObscuraBrowser(config=ObscuraConfig(stealth=False))
        b.ensure_started()
        from deepagents_cli.browser import obscura as obs_mod

        cmd = obs_mod.subprocess.Popen.call_args[0][0]  # type: ignore[attr-defined]
        assert "--stealth" not in cmd


def test_extra_args_passed_through(tmp_path: Path) -> None:
    with _patched_obscura(tmp_path):
        cfg = ObscuraConfig(extra_args=("--verbose", "--user-agent=Pack/1.0"))
        b = ObscuraBrowser(config=cfg)
        b.ensure_started()
        from deepagents_cli.browser import obscura as obs_mod

        cmd = obs_mod.subprocess.Popen.call_args[0][0]  # type: ignore[attr-defined]
        assert "--verbose" in cmd
        assert "--user-agent=Pack/1.0" in cmd


def test_shutdown_terminates_process_and_closes_browser(tmp_path: Path) -> None:
    with _patched_obscura(tmp_path) as fakes:
        b = ObscuraBrowser()
        b.ensure_started()
        b.shutdown()
        fakes["browser_obj"].close.assert_called_once()
        fakes["playwright"].stop.assert_called_once()
        fakes["process"].terminate.assert_called_once()


def test_shutdown_is_idempotent(tmp_path: Path) -> None:
    with _patched_obscura(tmp_path):
        b = ObscuraBrowser()
        b.ensure_started()
        b.shutdown()
        b.shutdown()  # second call should not raise
        assert b.is_running is False


def test_context_manager_starts_and_shuts_down(tmp_path: Path) -> None:
    with _patched_obscura(tmp_path) as fakes:
        with ObscuraBrowser() as b:
            assert b.is_running is True
        # On exit, browser closed
        fakes["browser_obj"].close.assert_called_once()


def test_new_page_returns_playwright_page(tmp_path: Path) -> None:
    with _patched_obscura(tmp_path) as fakes:
        b = ObscuraBrowser()
        # default_context shape: contexts -> [ctx]; ctx.new_page -> page
        ctx = MagicMock(name="context")
        ctx.new_page.return_value = MagicMock(name="page")
        fakes["browser_obj"].contexts = [ctx]
        page = b.new_page()
        assert page is ctx.new_page.return_value


def test_unavailable_when_binary_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OBSCURA_BIN", raising=False)
    with patch("shutil.which", return_value=None):
        b = ObscuraBrowser()
        with pytest.raises(BrowserUnavailable):
            b.ensure_started()


def test_unavailable_when_playwright_missing(tmp_path: Path) -> None:
    fake_bin = tmp_path / "obscura"
    fake_bin.write_text("")
    fake_proc = MagicMock()
    fake_proc.poll.return_value = None
    with (
        patch("deepagents_cli.browser.obscura._resolve_binary", return_value=str(fake_bin)),
        patch("deepagents_cli.browser.obscura._wait_for_port"),
        patch("deepagents_cli.browser.obscura.subprocess.Popen", return_value=fake_proc),
        # Force ImportError by NOT stubbing playwright.sync_api in sys.modules
        patch.dict("sys.modules", {"playwright.sync_api": None}),
    ):
        b = ObscuraBrowser()
        with pytest.raises(BrowserUnavailable, match="playwright"):
            b.ensure_started()
