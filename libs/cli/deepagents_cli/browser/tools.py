"""LangChain ``BaseTool`` instances exposing Obscura to the agent.

Each tool wraps a single browser primitive. Together they're the
minimum surface an agent needs to reason about a webpage:

- ``browser_open(url)`` — load a URL.
- ``browser_text()`` — get the rendered text of the current page.
- ``browser_html()`` — raw HTML when text isn't enough.
- ``browser_evaluate(script)`` — run JS, return the JSON-serializable result.
- ``browser_screenshot(path)`` — save a PNG.
- ``browser_close()`` — release the page (lifecycle hint, not strictly required).

A single ``ObscuraBrowser`` instance is shared across the tools so
they all operate on the same tab. The first tool call lazily starts
the subprocess; subsequent calls are connection-only.

Errors propagate as ``ToolException``-shaped strings so the
existing tool-result enrichment middleware can render them with the
standard ``[exit=...]`` style markers.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from deepagents_cli.browser.obscura import (
    BrowserUnavailable,
    ObscuraBrowser,
    ObscuraConfig,
)

if TYPE_CHECKING:
    from langchain.tools import BaseTool

logger = logging.getLogger(__name__)


# --- Single shared browser per tool list --------------------------------


class _BrowserSession:
    """Holds one ``ObscuraBrowser`` + the active page across tool calls.

    Single-page model on purpose: agents that need multiple tabs can
    open them via JS evaluation, but the default tool surface is
    "navigate to a thing, look at it, navigate elsewhere." Avoids the
    confusion of "which tab is browser_text reading from?"
    """

    def __init__(self, config: ObscuraConfig | None = None) -> None:
        self._browser = ObscuraBrowser(config=config)
        self._page: Any = None

    def page(self) -> Any:
        if self._page is None:
            self._page = self._browser.new_page()
        return self._page

    def reset_page(self) -> None:
        """Drop the current page so the next call opens a fresh one."""
        if self._page is not None:
            try:
                self._page.close()
            except Exception:  # noqa: BLE001
                logger.debug("page.close() raised; ignoring", exc_info=True)
            self._page = None

    def shutdown(self) -> None:
        self.reset_page()
        self._browser.shutdown()


# --- Tool argument schemas ---------------------------------------------


class _OpenArgs(BaseModel):
    url: str = Field(description="Full URL to navigate to (https://...).")
    wait_until: str = Field(
        default="load",
        description=(
            "Playwright wait state: 'load' | 'domcontentloaded' | "
            "'networkidle'. Default 'load' covers most pages."
        ),
    )


class _EvaluateArgs(BaseModel):
    script: str = Field(
        description=(
            "JavaScript to run on the current page. The expression's "
            "value is returned as a JSON-serializable Python object."
        ),
    )


class _ScreenshotArgs(BaseModel):
    path: str = Field(description="Filesystem path where the PNG is written.")
    full_page: bool = Field(
        default=False,
        description="When True, capture the full scrollable page; otherwise viewport only.",
    )


# --- Tool implementations -----------------------------------------------


def _open_url(session: _BrowserSession, url: str, wait_until: str) -> str:
    try:
        page = session.page()
        page.goto(url, wait_until=wait_until)
    except BrowserUnavailable as exc:
        return f"browser unavailable: {exc}"
    except Exception as exc:  # noqa: BLE001  # surface to agent, not crash
        return f"browser_open failed: {exc}"
    return f"opened {url} (title: {page.title()!r})"


def _read_text(session: _BrowserSession) -> str:
    """Get rendered text via JS rather than Playwright Locator API.

    Playwright's ``page.locator(...).inner_text()`` calls CDP methods
    Obscura doesn't fully implement (selectors block on
    ``Page.getLayoutMetrics`` / locator-handle protocols). JS-side
    ``document.body.innerText`` works against Obscura because it
    relies only on script evaluation, which Obscura's V8 supports.
    Falls back to ``textContent`` when ``innerText`` is unavailable
    (rare, but happens on some non-HTML doc shapes).
    """
    try:
        page = session.page()
        text = page.evaluate(
            "() => (document.body && (document.body.innerText "
            "|| document.body.textContent)) || ''",
        )
    except BrowserUnavailable as exc:
        return f"browser unavailable: {exc}"
    except Exception as exc:  # noqa: BLE001
        return f"browser_text failed: {exc}"
    return text if isinstance(text, str) else str(text)


def _read_html(session: _BrowserSession) -> str:
    try:
        return session.page().content()
    except BrowserUnavailable as exc:
        return f"browser unavailable: {exc}"
    except Exception as exc:  # noqa: BLE001
        return f"browser_html failed: {exc}"


def _evaluate(session: _BrowserSession, script: str) -> str:
    try:
        result = session.page().evaluate(script)
    except BrowserUnavailable as exc:
        return f"browser unavailable: {exc}"
    except Exception as exc:  # noqa: BLE001
        return f"browser_evaluate failed: {exc}"
    # Stringify so the tool result is always a flat string. The
    # tool-result-enrichment middleware can append derived signals.
    return str(result)


def _screenshot(
    session: _BrowserSession, path: str, full_page: bool
) -> str:
    """Save a PNG. Falls back to raw CDP when Playwright's wrapper fails.

    Playwright's high-level ``page.screenshot()`` calls
    ``Page.getLayoutMetrics`` which Obscura's CDP doesn't yet
    implement. The CDP ``Page.captureScreenshot`` method *is*
    supported, so we try that directly via ``CDPSession.send`` when
    the high-level call fails. This trade-off keeps the public API
    consistent across browser backends while still working against
    Obscura today.
    """
    import base64

    try:
        page = session.page()
    except BrowserUnavailable as exc:
        return f"browser unavailable: {exc}"

    try:
        page.screenshot(path=path, full_page=full_page)
        return f"saved screenshot to {path}"
    except Exception as primary_exc:  # noqa: BLE001
        # Try the raw CDP path before giving up
        try:
            cdp = page.context.new_cdp_session(page)
            result = cdp.send(
                "Page.captureScreenshot",
                {"format": "png"} | (
                    {"captureBeyondViewport": True} if full_page else {}
                ),
            )
            data = result.get("data") if isinstance(result, dict) else None
            if isinstance(data, str):
                with open(path, "wb") as f:  # noqa: PTH123  # binary write
                    f.write(base64.b64decode(data))
                return f"saved screenshot to {path} (via raw CDP fallback)"
        except Exception as fallback_exc:  # noqa: BLE001
            return (
                f"browser_screenshot failed: {primary_exc} "
                f"(CDP fallback also failed: {fallback_exc})"
            )
        return f"browser_screenshot failed: {primary_exc}"


def _close(session: _BrowserSession) -> str:
    session.reset_page()
    return "page closed"


# --- Public factory -----------------------------------------------------


class _BrowserToolsList(list):  # noqa: FURB189  # subclass so we can attach session
    """List subclass that carries its backing session as an attribute.

    Plain ``list`` instances don't accept arbitrary attributes; tests
    and alternative-lifetime callers want explicit handle to the
    session for cleanup, so wrap with this thin subclass.
    """

    _session: _BrowserSession


def make_obscura_tools(
    config: ObscuraConfig | None = None,
) -> list[BaseTool]:
    """Build the LangChain tool list backed by a single Obscura session.

    Caller is responsible for the session's lifetime: the tools share
    a ``_BrowserSession`` that registers an atexit cleanup, so it
    will tear down with the calling process. For test isolation pass
    a custom ``ObscuraConfig`` and shut the session down explicitly
    via the returned list's ``_session`` attribute.
    """
    session = _BrowserSession(config=config)

    tools: _BrowserToolsList = _BrowserToolsList()
    tools.extend([
        StructuredTool.from_function(
            name="browser_open",
            description=(
                "Navigate the headless browser to a URL. Returns the "
                "page title on success. Use this before browser_text "
                "or browser_evaluate."
            ),
            args_schema=_OpenArgs,
            func=lambda url, wait_until="load": _open_url(session, url, wait_until),
        ),
        StructuredTool.from_function(
            name="browser_text",
            description=(
                "Get the rendered text content of the current page. "
                "Strips HTML; returns what a human would see."
            ),
            func=lambda: _read_text(session),
        ),
        StructuredTool.from_function(
            name="browser_html",
            description=(
                "Get the raw HTML of the current page. Use only when "
                "you need structure that browser_text strips out."
            ),
            func=lambda: _read_html(session),
        ),
        StructuredTool.from_function(
            name="browser_evaluate",
            description=(
                "Run JavaScript on the current page and return the "
                "expression's value. The script runs in the page "
                "context and has access to document, window, etc. "
                "Use for structured extraction (querySelector, etc.)."
            ),
            args_schema=_EvaluateArgs,
            func=lambda script: _evaluate(session, script),
        ),
        StructuredTool.from_function(
            name="browser_screenshot",
            description=(
                "Save a PNG of the current page to disk. Set "
                "full_page=true to capture the full scrollable page."
            ),
            args_schema=_ScreenshotArgs,
            func=lambda path, full_page=False: _screenshot(session, path, full_page),
        ),
        StructuredTool.from_function(
            name="browser_close",
            description=(
                "Close the current page. The next browser_open will "
                "create a fresh page. Optional — the session also "
                "cleans up at process exit."
            ),
            func=lambda: _close(session),
        ),
    ])
    # Attach the session so callers (tests, alternative lifetimes)
    # can shut it down deterministically.
    tools._session = session
    return tools


__all__ = ["make_obscura_tools"]
