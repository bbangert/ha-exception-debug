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
snapshot of the traceback is retained so the entry stays listable.

## Installation (HACS)

1. In HACS, add this repository as a **custom repository** (category:
   *Integration*): `https://github.com/bbangert/ha-exception-debug`.
2. Install **Exception Debug** and restart Home Assistant.
3. Add configuration (below) and restart again.

Manual install: copy `custom_components/exception_debug/` into your Home
Assistant `config/custom_components/` directory.

## Configuration

This integration is configured via YAML in `configuration.yaml`:

```yaml
exception_debug:
  level: error          # debug | info | warning | error | critical (min level to capture)
  max_entries: 50       # how many exceptions to retain
  ttl: 900              # seconds a captured exception keeps its live frames
  max_repr: 2000        # max characters returned for any single repr()
  enable_eval: false    # set true to allow eval_in_frame (arbitrary code execution)
```

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

All endpoints require authentication (`Authorization: Bearer <long-lived token>`):

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
- `eval_in_frame` runs on the event loop; a blocking snippet will block Home
  Assistant. Use it deliberately.
- To have the HACS brands check pass fully, the `exception_debug` domain must be
  added to the [home-assistant/brands](https://github.com/home-assistant/brands)
  repository. This is a separate PR and only affects the icon/brand validation.

## License

MIT — see [LICENSE](LICENSE).
