# rtime-library-gateway

A thin orchestration MCP that puts **one central permission gate** + a
**metadata-only audit** in front of the existing rtime read CLIs (brain-library,
brain-docpack, brain-citation, hub, context, profile, review, automation,
runtime) and the three narrow `deploy/bin` settings writers. It reimplements no
read logic: every method subprocesses an existing tool and re-exposes it as a
`lib.*` method.

> **Status (2026-06-18): deployed as the orangepi knowledge hub (SSH stdio).**
> The gateway runs on orangepi (brain master + the single BM25 index live there);
> Claude Code + Desktop on Windows and Mac reach it over one `ssh` command
> (`✓ Connected`, `lib.search` verified). Access/registration norms, the single-index
> maintenance model, and how any agent plugs in: **`docs/brain-knowledge-hub.md`**.
>
> Top-level module map: [`docs/brain-library-module.md`](../../docs/brain-library-module.md).
> Full design/contract: [`docs/rtime-library-gateway.md`](../../docs/rtime-library-gateway.md).

## What it provides

- `gate.py` — `enforce()` permission gate: tier/enabled/client checks,
  path-escape denial, plus policy-gated personal-data exclusion and `redact_output`
  for secret-shaped tokens (both OFF in the deployed single-owner policy as of
  2026-06-19 — `excluded_top_dirs: []`, `redact_sensitive: false`; re-enablable via policy).
- `dispatch.py` — disjoint `READ_DISPATCH` / `WRITE_DISPATCH` tables (an
  import-time assertion keeps them non-overlapping) that fan out to the
  underlying CLIs by subprocess.
- `mcp_server.py` — MCP stdio JSON-RPC server exposing the `lib.*` methods.
- `cli.py` — a small command-line surface over the same dispatch.

The name crosswalk for any one tool is: module id `brain-library` → dir
`packages/brain-library` → python `brain_library` → MCP `library.*` → gateway
`lib.*`. (Searching one name misses the others — see the module index.)

## Run (stdio MCP, for local manual testing)

```bash
PYTHONPATH=packages/rtime-library-gateway/src \
  python -m rtime_library_gateway.mcp_server
```

or via the plugin wrapper
`plugins/rtime-library-gateway/scripts/rtime-library-gateway-mcp.sh`, which sets
`PYTHONPATH` and the `BRAIN_ROOT` / `RTIME_HUB_ROOT` / `RTIME_REMINDERS_PATH`
fallbacks before exec.

Read tools require the caller to pass an explicit SQLite index path for
`lib.search` / `lib.get`; the gateway has no default index. Build one with
`brain-library index build <root> --out <path>` first.

## Scoped second instance (subset read-only door)

A second, non-owner consumer never shares the owner's gateway process. Isolation
is **process-level**: an independent process + an independent policy file + an
independent loopback port — never per-client-name rules in the shared process
(the MCP `clientInfo.name` is self-reported and unauthenticated).

The policy switch is `allowed_path_prefixes` (list of brain-relative subtree
prefixes; empty = full library, the single-owner default). When non-empty, the
gate confines every read to those subtrees:

- path-taking reads (`lib.read`/`lib.stat`/`lib.tree`/`lib.list`/... any
  `PATH_LIKE_KEYS` argument): the resolved target must sit inside a prefix;
- `lib.search`/`lib.recent`: a caller `path_prefix` must equal or sit under a
  prefix (boundary prefixes are pinned with a trailing slash against LIKE
  sibling-matching); with the argument omitted, a single scope prefix is
  injected, several prefixes require the caller to pick one;
- `lib.tree`/`lib.list` get the same inject-or-require treatment for their
  `path`/`root`;
- other read methods called with no path argument (`lib.meta`, `lib.courses`,
  `lib.freshness`, the panel surfaces...) are denied — their implicit default
  root is the whole library. In-process self-describing methods
  (`lib.doctor`/`lib.policy`/`lib.status`/`lib.preview`/`lib.audit`) and the
  index-metadata-only `lib.get` stay available.
- writes are not scope-checked: a scoped policy denies them wholesale
  (`default_write: deny` + client deny globs + a closed allow list).

Shipped example: `policy/studentunion-policy.json` (subset scope, writes denied
three ways, redaction + excluded-dir hiding ON, separate audit log) paired with
`deploy/systemd/user/rtime-library-gateway-public.service` (HTTP
`127.0.0.1:8781`, `RTIME_LIBRARY_GATEWAY_POLICY` pointing at that file). Before
enabling the unit, **confirm the real prefixes** in the policy against the live
brain tree (see the `_comment` block in the policy file).

## Validation

```bash
PYTHONPATH=packages/rtime-library-gateway/src python -m pytest \
  tests/test_rtime_library_gateway_gate.py \
  tests/test_rtime_library_gateway_cli.py \
  tests/test_rtime_library_gateway_mcp.py -q
python scripts/validate-codex-plugin.py plugins/rtime-library-gateway
```

## Safety

Read and gate surfaces are read-only by default; the three write methods are the
narrow `deploy/bin` settings writers only. Per `AGENTS.md`, this package must not
move or rewrite `brain` data; it brokers access, it does not own content.
