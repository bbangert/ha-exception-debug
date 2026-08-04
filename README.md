# Exception Debug for Home Assistant

A live post-mortem debugger for Home Assistant exceptions — think
[`pyramid_debugtoolbar`](https://github.com/Pylons/pyramid_debugtoolbar) or the
Werkzeug interactive debugger, but for Home Assistant and queryable by an AI
agent over [MCP](https://www.home-assistant.io/integrations/mcp_server/).

Home Assistant's built-in `system_log` keeps only the *formatted text* of an
error. This integration keeps the **live exception object and its traceback
frames** in memory for a short window, so you (or an AI coding agent) can:

- list recently captured exceptions,
- walk each traceback's frames and read their **local variables**, and
- optionally **evaluate Python in the context of a captured frame** for true
  post-mortem debugging.

> ⚠️ **This is a developer/debugging tool.** Retaining tracebacks pins the
> objects that were in scope when the error happened, and the `eval_in_frame`
> capability is arbitrary code execution by design. Keep `enable_eval` off
> unless you understand the implications, and never expose your Home Assistant
> instance unauthenticated.

## How it works

A `logging.Handler` is attached directly to the **root logger** during setup.
Because Home Assistant migrates its console/file handlers behind a
`QueueHandler` (whose `prepare()` strips `exc_info`) *before* any integration
loads, a sibling root handler added afterwards still sees records with their
**live `exc_info`** intact — the same mechanism the core `system_log`
integration relies on. When a record arrives with no `exc_info` (e.g. Home
Assistant's `catch_log_exception` logs pre-formatted text), the handler falls
back to `sys.exc_info()`, which is still valid because it runs synchronously
inside the originating `except` block.

Captured exceptions are held in a bounded, TTL-aware store. Once an entry's
live window (`ttl`) elapses — or it is evicted past `max_entries` — its frames
are cleared with `traceback.clear_frames()` to release locals, while a text
snapshot of the traceback is retained so the entry stays listable. A timer
applies the TTL once a minute, so frames are released on a quiet system too and
not only when the next exception happens to arrive.

## Requirements

Home Assistant **2025.8.0** or newer.

## Installation (HACS)

1. In HACS, add this repository as a **custom repository** (category:
   *Integration*): `https://github.com/bbangert/ha-exception-debug`.
2. Install **Exception Debug** and restart Home Assistant.
3. Go to **Settings → Devices & services → Add integration** and pick
   **Exception Debug**.

Manual install: copy `custom_components/exception_debug/` into your Home
Assistant `config/custom_components/` directory, restart, then add the
integration from the UI as above.

## Configuration

Everything is configured from the UI — on first setup, and afterwards via
**Settings → Devices & services → Exception Debug → Configure**. Changing an
option reloads the integration immediately; no restart needed.

| Option | Default | Meaning |
| --- | --- | --- |
| Capture level | `error` | Minimum log level to capture (`debug` … `critical`). |
| Maximum retained exceptions | `50` | Oldest are evicted past this count. |
| Live frame retention | `900` s | How long an entry keeps inspectable frames. `0` releases them immediately. |
| Maximum repr length | `2000` | Cap on the characters returned for any single value. |
| Enable eval | off | Allows `eval_in_frame` — arbitrary code execution. |

Only one instance can be configured, since the capture hook is global.

### Migrating from YAML

Earlier versions were configured in `configuration.yaml`. That still works for
one more startup: the block is imported into a config entry automatically and a
repair issue tells you to delete it. Remove the `exception_debug:` block from
`configuration.yaml` once you have restarted — after the import, the YAML is
ignored and the UI options are authoritative.

## Using it with an AI agent (MCP)

This integration registers a Home Assistant **LLM API** named
*"Home Assistant Exception Debugger"*. To expose it to an agent:

1. Set up the core [Model Context Protocol Server](https://www.home-assistant.io/integrations/mcp_server/)
   integration.
2. In its options, tick **Home Assistant Exception Debugger** as an exposed API.
3. Point your MCP client at `https://<your-ha>/api/mcp` with a long-lived
   access token.

Tools exposed to the agent:

| Tool | Purpose |
| --- | --- |
| `list_exceptions` | Recent captured exceptions, newest first. |
| `get_traceback` | Full formatted traceback text for an id. |
| `get_frames` | Frames of an exception (file, line, function, local names). |
| `get_frame_locals` | `{name: repr}` of a frame's locals. |
| `eval_in_frame` | Evaluate Python in a frame's context *(only if `enable_eval: true`)*. |

## REST API

All endpoints require an **admin** user's token
(`Authorization: Bearer <long-lived token>`). Frame locals routinely contain
credentials that were in scope when the error happened, so authentication alone
is not a sufficient boundary — this matches the admin gate on the WebSocket
commands.

```
GET /api/exception_debug/exceptions?limit=20
GET /api/exception_debug/exceptions/{id}
GET /api/exception_debug/exceptions/{id}/frames/{frame_index}/locals
```

## WebSocket API (admin only)

```
exception_debug/list          {limit?}
exception_debug/frames         {exc_id}
exception_debug/frame_locals   {exc_id, frame}
```

## Services

- `exception_debug.clear` — drop all captured exceptions and release frames.

## Notes & limitations

- Root-logger handlers do not see loggers with `propagate = False` (rare in HA).
- Captured exceptions are held per config entry, so changing an option (which
  reloads the integration) starts a fresh buffer and discards what was captured.
- `eval_in_frame` runs on the event loop; a blocking snippet will block Home
  Assistant. Use it deliberately.
- The icon ships in-repo under `custom_components/exception_debug/brand/`, which
  satisfies the HACS brands check. Adding `exception_debug` to
  [home-assistant/brands](https://github.com/home-assistant/brands) is only
  needed to appear in the default HACS store.

## Development

```bash
python -m venv .venv && .venv/bin/pip install -r requirements_test.txt
.venv/bin/pytest --cov=custom_components.exception_debug --cov-branch --cov-report=term-missing
.venv/bin/ruff format --check custom_components tests
.venv/bin/ruff check custom_components tests
.venv/bin/mypy custom_components/exception_debug --ignore-missing-imports
```

CI runs hassfest, HACS validation, ruff, mypy, and the test suite on every push
and pull request. Tests are gated at 100% **branch** coverage and run against
both the minimum supported Home Assistant (2025.8.1) and a current release, so
the version floor advertised in `hacs.json` is actually exercised rather than
assumed.

## License

MIT — see [LICENSE](LICENSE).
