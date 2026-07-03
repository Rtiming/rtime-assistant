# web-chat — browser Q&A entry (T5a/T5b)

The "third channel" on `rtime-chat-runtime`, alongside the QQ and Feishu bridges.
A thin **stdlib** HTTP app (no FastAPI — that is reserved for `apps/admin-api`):
a single static `index.html` plus a JSON-over-SSE chat endpoint. Every turn runs
through the shared runtime path (ToolPolicy / SessionStore / run_log / the unified
CLI model runner) — **never** a direct model / LiteLLM call.

Design: `docs/design/mainline-profiles-and-entries-2026-07.zh-CN.md` §5 (and §7 T5a/T5b).

## Endpoints (design §5.2)

| Method | Path            | Purpose |
|--------|-----------------|---------|
| GET    | `/`             | static `index.html` (fetch()+ReadableStream client) |
| GET    | `/api/profiles` | web-enabled profiles `{id, name, description, read_only}` + `default` |
| POST   | `/api/chat`     | `{profile, message, session_id?}` → SSE stream |
| GET    | `/healthz`      | liveness `{ok, service}` |

SSE frames match `apps/assistant-gateway` (the proven web-ready protocol):
`{"type":"status"|"delta"|"done"|"error", ...}`, one JSON object per `data:` frame.
Browser `EventSource` is GET-only, so the page POSTs and reads the stream via
`fetch()`+`ReadableStream` (backend unchanged). Frame write/parse are the shared
`rtime_chat_runtime.sse` helpers.

## Run

```bash
# local dev (no claude CLI => echo mode, protocol still exercisable)
WEB_CHAT_BIND=127.0.0.1 WEB_CHAT_PORT=8788 python -m web_chat

# tests (zero network beyond loopback; model runner is faked)
python -m pytest apps/web-chat/tests -q
```

Config: `web_chat/config.py` (`WebChatConfig.from_env()`). Env reference:
`apps/web-chat/.env.example`.

## Profiles

`web_chat/profiles.py` uses the real git profile loader. It scans
`RTIME_PROFILES_ROOT` (default `/etc/rtime/profiles`), compiles profiles through
`rtime_config.profile.load_profile()`, and lists only profiles that declare
`channels.web`. `GET /api/profiles` exposes only public fields; the server keeps
the selected profile's system prompt, read-only flag, renderer, and MCP config as
backend behavior.

`RTIME_WEB_CHAT_PROFILES` remains an ad-hoc override for instances without the
profiles tree (inline JSON array or path). A malformed override fails fast.
`studentunion` resolves to the same read-only hard door and 8781 scoped library
gateway as the QQ channel.

## Deploy

`compose --profile web` service `web-chat`, image layered `FROM` the feishu image
(`apps/web-chat/deploy/Dockerfile.m2`), default bind `127.0.0.1:8788`. Per design
§5.3 the container does **not** mount `/mnt/brain` — it is a gateway-only consumer;
library access arrives via the selected profile's `channels.web.mcp_servers`
(or the process default `WEB_CHAT_MCP_CONFIG` for override-only instances).

```bash
docker compose -f compose.prod.yml --profile web up -d --build
```

## Not in this round

- Auth: actor is fixed `web:anonymous`, but threaded through the whole run path
  (session key, run_log hash) so auth slots into `WebChatHandler._resolve_actor`.
- Client-disconnect child-kill (assistant-gateway's FIN watcher) — TODO.
- Production enablement on orangepi is optional and still needs an explicit
  `docker compose --profile web` rollout/health validation.
