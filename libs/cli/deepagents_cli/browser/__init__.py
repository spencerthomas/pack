"""Pack's native browser surface — Obscura integration.

Obscura (https://github.com/h4ckf0r0day/obscura) is a Rust-built
headless browser that exposes the Chrome DevTools Protocol on a
WebSocket endpoint. Pack integrates it as the default browser tool
because:

- Single ~30 MB binary, no Chromium download required.
- Stealth mode + tracker blocking baked in (matters for agents that
  scrape).
- Drop-in CDP compatibility — Playwright/Puppeteer connect cleanly.
- Apache-2.0, redistributable.

This package wraps Obscura's lifecycle (subprocess start, port
allocation, graceful shutdown) and exposes a small set of LangChain
``BaseTool`` instances the agent can call. Agents that don't need a
browser don't pay any cost — the subprocess only starts on first
tool use.

Usage:

::

    from deepagents_cli.browser import make_obscura_tools

    tools = make_obscura_tools()
    agent, _ = create_cli_agent(model=..., tools=tools, ...)

The Playwright Python runtime is an optional dependency
(``deepagents-cli[browser]``); the module imports it lazily so the
import surface stays clean for non-browser agents.
"""

from __future__ import annotations

from deepagents_cli.browser.obscura import ObscuraBrowser, ObscuraConfig
from deepagents_cli.browser.tools import make_obscura_tools

__all__ = [
    "ObscuraBrowser",
    "ObscuraConfig",
    "make_obscura_tools",
]
