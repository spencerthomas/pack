# Pack browser — Obscura integration

Pack ships a native browser surface backed by
[Obscura](https://github.com/h4ckf0r0day/obscura), a Rust-built
headless browser that exposes Chrome DevTools Protocol on a WebSocket
endpoint. Pack picked Obscura over plain Chromium for three reasons:

- **Single ~30 MB binary**, no Chromium download.
- **Stealth + tracker blocking baked in** — relevant when agents do
  scraping work without operator supervision.
- **Drop-in CDP compatibility** — Playwright connects via
  `connect_over_cdp` exactly like to Chrome.

## Setup

1. Install the Obscura binary. Pre-built releases:
   <https://github.com/h4ckf0r0day/obscura/releases>

   ```bash
   # macOS Apple Silicon (asset name is "macos", not "darwin")
   curl -LO https://github.com/h4ckf0r0day/obscura/releases/latest/download/obscura-aarch64-macos.tar.gz
   tar xzf obscura-aarch64-macos.tar.gz
   sudo mv obscura /usr/local/bin/
   ```

   Or set `OBSCURA_BIN=/path/to/obscura` so the wrapper finds it
   without modifying `PATH`.

2. Install the Python optional dependency:

   ```bash
   pip install 'deepagents-cli[browser]'
   ```

   This pulls Playwright Python. **Do not** run
   `playwright install chromium` — Pack uses Obscura over CDP, no
   Chromium needed.

## Usage

```python
from deepagents_cli.agent import create_cli_agent
from deepagents_cli.browser import make_obscura_tools

browser_tools = make_obscura_tools()

agent, _ = create_cli_agent(
    model="anthropic:claude-sonnet-4-6",
    assistant_id="my-session",
    tools=browser_tools,  # added alongside Pack's defaults
    interactive=False,
)
```

The agent gets six tools:

| Tool | Purpose |
|------|---------|
| `browser_open(url, wait_until)` | Navigate to a URL |
| `browser_text()` | Rendered text of the current page |
| `browser_html()` | Raw HTML of the current page |
| `browser_evaluate(script)` | Run JS, return the result |
| `browser_screenshot(path, full_page)` | Save a PNG |
| `browser_close()` | Close the current page |

A single browser session is shared across all six tools. The first
tool call starts the Obscura subprocess; subsequent calls reuse the
connection. The session shuts down at process exit via `atexit`.

## Configuration

Override defaults with `ObscuraConfig`:

```python
from deepagents_cli.browser import ObscuraConfig, make_obscura_tools

cfg = ObscuraConfig(
    binary_path="/opt/obscura/obscura",
    port=9222,           # 0 = pick a free port
    stealth=True,
    extra_args=("--user-agent=Pack/1.0",),
)
tools = make_obscura_tools(config=cfg)
```

## Verified against Obscura v0.1.1

Live-test status of the six tools (against `https://example.com` and
`https://httpbin.org/get`):

| Tool | Status | Notes |
|------|--------|-------|
| `browser_open` | ✅ works | `goto(url)` + `Page.title()` round-trip cleanly |
| `browser_text` | ✅ works | Routes through JS `document.body.innerText` rather than `page.locator('body').inner_text()` because Obscura's CDP doesn't implement the locator-handle protocol |
| `browser_html` | ✅ works | `page.content()` returns full HTML |
| `browser_evaluate` | ✅ works | V8 inside Obscura runs scripts directly |
| `browser_screenshot` | ⚠️ **not supported on Obscura today** | Obscura's CDP doesn't implement `Page.getLayoutMetrics` or `Target.attachToBrowserTarget`, so neither Playwright's high-level path nor the raw-CDP fallback can fire. Tracked upstream; the tool returns a clear error message until Obscura adds those methods. The other tools degrade gracefully without it. |
| `browser_close` | ✅ works | Resets the page; next `browser_open` opens fresh |

The first install build of Obscura compiles V8 from source (~5 min,
one-time, cached after). Subsequent process starts are sub-second.

## Failure modes

When Obscura isn't installed or Playwright isn't importable, the
tools return a diagnostic string (e.g. `browser unavailable: obscura
binary not found...`) rather than crashing the agent. The agent can
read that and pivot — that's the same teach-at-failure pattern Pack
uses elsewhere.

## Why not the existing `claude-in-chrome` MCP?

`claude-in-chrome` is a Claude Code-specific MCP server that drives
the user's actual desktop browser. It's good for human-in-the-loop
workflows where the operator wants to see what the agent is doing.
Pack's Obscura integration is for headless agent runs (benchmarks,
scrapers, scheduled jobs) where there's no UI and you want stealth
defaults. The two compose: an interactive Pack session can mount
both.
