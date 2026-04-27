"""Tests for the Obscura LangChain tool wrappers."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from deepagents_cli.browser.tools import _BrowserSession, make_obscura_tools


@pytest.fixture
def fake_page() -> MagicMock:
    """A Playwright Page-shaped mock.

    ``browser_text`` invokes ``page.evaluate(...)`` to read body
    innerText (Obscura's CDP doesn't fully support the Locator
    protocol). Distinguishes evaluate calls by inspecting the
    script source: the body-text JS contains "innerText", anything
    else is a generic eval.
    """
    page = MagicMock(name="page")
    page.title.return_value = "Example"
    page.content.return_value = "<html><body>hello world</body></html>"

    def _evaluate(script: str) -> object:
        if "innerText" in script:
            return "hello world"
        return {"x": 42}

    page.evaluate.side_effect = _evaluate
    return page


@pytest.fixture
def patched_session(fake_page: MagicMock) -> Any:
    """Patch ObscuraBrowser so _BrowserSession returns a mock page."""
    with patch("deepagents_cli.browser.tools.ObscuraBrowser") as fake_cls:
        instance = MagicMock(name="browser_instance")
        instance.new_page.return_value = fake_page
        fake_cls.return_value = instance
        yield instance, fake_page


# ---------------------------------------------------------------------------
# _BrowserSession lifecycle
# ---------------------------------------------------------------------------


def test_session_lazy_creates_page_on_first_call(
    patched_session: Any,
) -> None:
    instance, fake_page = patched_session
    session = _BrowserSession()
    # Before calling page(), no page is opened
    assert session._page is None
    p = session.page()
    assert p is fake_page
    instance.new_page.assert_called_once()


def test_session_reuses_page_across_calls(patched_session: Any) -> None:
    _instance, fake_page = patched_session
    session = _BrowserSession()
    p1 = session.page()
    p2 = session.page()
    assert p1 is p2 is fake_page


def test_session_reset_drops_page(patched_session: Any) -> None:
    _instance, fake_page = patched_session
    session = _BrowserSession()
    session.page()
    session.reset_page()
    assert session._page is None
    fake_page.close.assert_called_once()


def test_session_shutdown_closes_browser(patched_session: Any) -> None:
    instance, _ = patched_session
    session = _BrowserSession()
    session.page()
    session.shutdown()
    instance.shutdown.assert_called_once()


# ---------------------------------------------------------------------------
# make_obscura_tools — public factory
# ---------------------------------------------------------------------------


def test_factory_returns_six_tools(patched_session: Any) -> None:
    tools = make_obscura_tools()
    names = {t.name for t in tools}
    assert names == {
        "browser_open",
        "browser_text",
        "browser_html",
        "browser_evaluate",
        "browser_screenshot",
        "browser_close",
    }


def test_factory_attaches_session(patched_session: Any) -> None:
    tools = make_obscura_tools()
    assert hasattr(tools, "_session")  # exposed for test cleanup


# ---------------------------------------------------------------------------
# Tool dispatch — happy paths
# ---------------------------------------------------------------------------


def test_browser_open_navigates_and_returns_title(
    patched_session: Any,
) -> None:
    _instance, fake_page = patched_session
    tools = make_obscura_tools()
    open_tool = next(t for t in tools if t.name == "browser_open")
    result = open_tool.invoke({"url": "https://example.com"})
    assert "https://example.com" in result
    assert "Example" in result  # quoted title
    fake_page.goto.assert_called_once()


def test_browser_text_returns_inner_text(patched_session: Any) -> None:
    tools = make_obscura_tools()
    text_tool = next(t for t in tools if t.name == "browser_text")
    # browser_open first to materialize the page
    next(t for t in tools if t.name == "browser_open").invoke({"url": "https://x"})
    result = text_tool.invoke({})
    assert result == "hello world"


def test_browser_html_returns_content(patched_session: Any) -> None:
    tools = make_obscura_tools()
    next(t for t in tools if t.name == "browser_open").invoke({"url": "https://x"})
    html = next(t for t in tools if t.name == "browser_html").invoke({})
    assert "<html>" in html
    assert "hello world" in html


def test_browser_evaluate_returns_stringified_result(
    patched_session: Any,
) -> None:
    tools = make_obscura_tools()
    next(t for t in tools if t.name == "browser_open").invoke({"url": "https://x"})
    result = next(t for t in tools if t.name == "browser_evaluate").invoke(
        {"script": "1 + 1"}
    )
    assert "42" in result  # fake_page returns {"x": 42}


def test_browser_screenshot_writes_file(
    patched_session: Any, tmp_path: Any
) -> None:
    _instance, fake_page = patched_session
    tools = make_obscura_tools()
    next(t for t in tools if t.name == "browser_open").invoke({"url": "https://x"})
    target = tmp_path / "shot.png"
    result = next(t for t in tools if t.name == "browser_screenshot").invoke(
        {"path": str(target)}
    )
    assert "saved screenshot" in result
    assert str(target) in result
    fake_page.screenshot.assert_called_once_with(path=str(target), full_page=False)


def test_browser_screenshot_falls_back_to_cdp_when_high_level_fails(
    patched_session: Any, tmp_path: Any
) -> None:
    """When Playwright's ``page.screenshot`` fails (Obscura's CDP gap),
    the tool retries via raw ``Page.captureScreenshot`` and writes the
    result to disk. Mirrors how the real wrapper handles real
    Obscura."""
    import base64

    _instance, fake_page = patched_session
    fake_page.screenshot.side_effect = RuntimeError("getLayoutMetrics unsupported")
    cdp_session = MagicMock(name="cdp_session")
    cdp_session.send.return_value = {"data": base64.b64encode(b"PNGDATA").decode()}
    fake_page.context.new_cdp_session.return_value = cdp_session

    tools = make_obscura_tools()
    next(t for t in tools if t.name == "browser_open").invoke({"url": "https://x"})
    target = tmp_path / "shot.png"
    result = next(t for t in tools if t.name == "browser_screenshot").invoke(
        {"path": str(target)}
    )
    assert "saved screenshot" in result
    assert "raw CDP fallback" in result
    assert target.exists()
    assert target.read_bytes() == b"PNGDATA"


def test_browser_close_resets_page(patched_session: Any) -> None:
    tools = make_obscura_tools()
    next(t for t in tools if t.name == "browser_open").invoke({"url": "https://x"})
    result = next(t for t in tools if t.name == "browser_close").invoke({})
    assert "closed" in result.lower()


# ---------------------------------------------------------------------------
# Error handling — failures surface as strings, not exceptions
# ---------------------------------------------------------------------------


def test_browser_open_failure_returns_error_string(
    patched_session: Any,
) -> None:
    _instance, fake_page = patched_session
    fake_page.goto.side_effect = RuntimeError("connection refused")
    tools = make_obscura_tools()
    result = next(t for t in tools if t.name == "browser_open").invoke(
        {"url": "https://example.com"}
    )
    assert "browser_open failed" in result
    assert "connection refused" in result


def test_browser_text_unavailable_returns_string(patched_session: Any) -> None:
    """If ObscuraBrowser raises BrowserUnavailable, tool returns a
    diagnostic string instead of crashing the agent."""
    from deepagents_cli.browser.obscura import BrowserUnavailable

    instance, _fake_page = patched_session
    instance.new_page.side_effect = BrowserUnavailable("no binary")
    tools = make_obscura_tools()
    result = next(t for t in tools if t.name == "browser_text").invoke({})
    assert "browser unavailable" in result
    assert "no binary" in result


def test_browser_evaluate_failure_returns_error_string(
    patched_session: Any,
) -> None:
    _instance, fake_page = patched_session
    fake_page.evaluate.side_effect = RuntimeError("syntax error")
    tools = make_obscura_tools()
    next(t for t in tools if t.name == "browser_open").invoke({"url": "https://x"})
    result = next(t for t in tools if t.name == "browser_evaluate").invoke(
        {"script": "{{"}
    )
    assert "browser_evaluate failed" in result
    assert "syntax error" in result
