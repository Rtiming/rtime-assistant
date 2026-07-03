# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Config-coverage allowlist — the explicit ledger of env keys not yet on a schema.

The coverage guard (``tests/test_config_coverage.py``) asserts every env key the
code READS is either ACCEPTED by a registered ``rtime-config`` field OR listed
here with a reason. It is a RATCHET: at baseline this file names every currently
unregistered key, so the test is green; as each P2 收编 batch registers a module,
that batch DELETES its keys from here and the test stays green; a NEW
``os.getenv("SOMETHING")`` with no schema field and no allowlist entry turns it
RED — you must register it or add it here with a reason.

Reason grammar: ``"<category>[:<batch>] <free text>"``. Categories (plan §2):

  bootstrap     — a process's own machinery env, never a managed config field
                  (e.g. the admin-api reading its OWN RTIME_ADMIN_API_* — it is the
                  config *authority*, not a configured module).
  deploy-path   — a path/interpreter/root the deploy layer injects (mount points,
                  run-log locations, PYTHONPATH shims); infra wiring, not a knob a
                  panel would edit.
  dev-override  — a dev/debug/standard-OS switch (XDG_*, *_DEBUG) not part of the
                  product config surface.
  derived-alias — a legacy env the schema field's loader TRANSFORMS rather than
                  reading verbatim (e.g. QQ_MAX_DOWNLOAD_MB -> max_download_bytes);
                  the field IS registered, this raw name is its input, so it is
                  covered in spirit — kept here so the raw read is not flagged.

The ``:batch`` suffix names the future 收编 batch that will register the key
(TODO-batch:feishu, TODO-batch:gateway, …), so a grep tells each batch exactly
which lines to remove. Keys with a pure category (no TODO-batch) are expected to
stay allowlisted permanently (they are genuinely not managed config).
"""

from __future__ import annotations

# env_key -> reason. KEEP SORTED (ruff-friendly, easy diffs). A stale entry
# (in this map but no longer read anywhere) is caught by the reverse sentinel
# in the guard test — remove it when you remove the last read.
ALLOWLIST: dict[str, str] = {
    # --- assistant-gateway (single gateway_config.py; whole module TODO) --------
    "GATEWAY_REVISION": "deploy-path build revision stamp injected at image build",
    # --- feishu-bridge (bot_config.py single file; whole module TODO) -----------
    "RTIME_ASSISTANT_ROOT": "deploy-path repo/app root, injected by deploy (main.py)",
    "RTIME_CHAT_RUNTIME_SRC": "deploy-path _shared_runtime.py path shim for rtime-chat-runtime",
    "RTIME_QQ_QR_REQUEST_FILE": "deploy-path cross-service QR request file path (qr_request.py)",
    "RTIME_LIBRARY_GRANTS_LEDGER": "deploy-path J6 grant 台账文件路径(grants_cli.py owner CLI)",
    # --- qq-bridge: selfheal ops tool + from_env-derived raw names -------------
    "QQ_DEBUG": "derived-alias qq log_level forces DEBUG in from_env (config field registered)",
    "QQ_MAX_DOWNLOAD_MB": "derived-alias qq max_download_bytes input in from_env (field registered)",
    # --- web-chat (config.py single file; whole module TODO) -------------------
    "RTIME_WEB_CHAT_PROFILES": "TODO-batch:web-chat profiles path (profiles.py)",
    "WEB_CHAT_DEBUG": "TODO-batch:web-chat (config.py)",
    # --- brain-visualmd (vision backend knobs; whole module TODO) --------------
    "VISUALMD_ESCALATE_BASE_MODEL": "TODO-batch:visualmd (backends/escalate.py)",
    "VISUALMD_ESCALATE_STRONG_MODEL": "TODO-batch:visualmd (backends/escalate.py)",
    "VISUALMD_FORMULA_RECOGNIZER": "TODO-batch:visualmd (backends/doc.py)",
    "VISUALMD_VISION_API_KEY": "TODO-batch:visualmd credential (vision_api.py) -> secret_field",
    "VISUALMD_VISION_BASE_URL": "TODO-batch:visualmd (vision_api.py)",
    "VISUALMD_VISION_MAX_IMAGE_PX": "TODO-batch:visualmd (vision_api.py)",
    "VISUALMD_VISION_MAX_TOKENS": "TODO-batch:visualmd (vision_api.py)",
    "VISUALMD_VISION_MODEL": "TODO-batch:visualmd (vision_api.py)",
    "VISUALMD_VISION_TIMEOUT": "TODO-batch:visualmd (vision_api.py)",
    # --- ustc-kb (crawl config; whole module TODO) -----------------------------
    # --- library-gateway: 3 fields not yet in the sample schema module ----------
    "PYTHON": "deploy-path interpreter path for the dispatch subprocess (dispatch.py)",
    # --- models domain (registry path not yet in the sample schema) ------------
    # --- jobs -------------------------------------------------------------------
    "RTIME_JOBS_DB": "TODO-batch:jobs sqlite db path (rtime_jobs/store.py)",
    # --- agent-control CLI roots (deploy-injected fact-store roots) -------------
    # --- run-log path overrides (cross-cutting; one deploy-injected path each) --
    "RTIME_ASSISTANT_RUN_LOG": "deploy-path shared run-log path, injected by deploy",
    "RTIME_AGENT_CONTROL_MCP_RUN_LOG": "deploy-path per-MCP run-log path override",
    "RTIME_AUTOMATION_MCP_RUN_LOG": "deploy-path per-MCP run-log path override",
    "RTIME_CONTEXT_MCP_RUN_LOG": "deploy-path per-MCP run-log path override",
    "RTIME_HUB_MCP_RUN_LOG": "deploy-path per-MCP run-log path override",
    "RTIME_PROFILE_MCP_RUN_LOG": "deploy-path per-MCP run-log path override",
    "RTIME_REVIEW_MCP_RUN_LOG": "deploy-path per-MCP run-log path override",
    "RTIME_RUNTIME_MCP_RUN_LOG": "deploy-path per-MCP run-log path override",
    "BRAIN_CITATION_MCP_RUN_LOG": "deploy-path per-MCP run-log path override",
    "BRAIN_DOCPACK_MCP_RUN_LOG": "deploy-path per-MCP run-log path override",
    "BRAIN_LIBRARY_MCP_RUN_LOG": "deploy-path per-MCP run-log path override",
    # --- standard OS / dev environment -----------------------------------------
    "XDG_STATE_HOME": "dev-override standard XDG base dir (embed.py)",
}
