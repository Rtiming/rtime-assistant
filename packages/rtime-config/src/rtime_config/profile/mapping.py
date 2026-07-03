# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""The EXPLICIT profile -> flat ``module.field`` projection table (§2.3).

The profile is nested and human-facing; the admin core addresses config by flat
two-segment ``module.field`` paths. Rather than extend nested addressing, a
profile is *compiled/projected* through this ONE explicit table (design §2.1). The
table is data, not code, so:

  - it is auditable at a glance (which profile key drives which config path);
  - it gets a golden test (nested yaml -> expected flat keys);
  - the module a field lands in is documented HERE (single source).

Each entry is a :class:`Projection`: a dotted PROFILE path (into the parsed
``ProfileConfig``) -> a target ``module.field`` path, optionally through a
``transform`` that maps the profile value to the stored value. A profile key that
is unset (``None``) contributes NOTHING to the compiled layer (sparse projection —
so an unset profile key falls through to the schema default, and never shadows the
store).

Module choices (documented, per design "pick the real module and document"):
  - ``permissions.read_only`` -> ``qq.read_only``. read_only is the QQ hard door
    (tool_policy reads QQ_READ_ONLY); after B the real live field lives on the qq
    module, so the projection targets qq.read_only (NOT the sample channel-common
    module, which is illustrative and not wired to the running bridge).
  - ``identity.system_prompt_file`` -> ``qq.system_prompt`` as the file CONTENT.
  - ``plugins.mcp_servers`` -> ``qq.mcp_config`` as serialized JSON (enabled=false
    entries dropped; all-empty -> '{"mcpServers": {}}' to preserve QQ semantics).
  - ``plugins.direct_rules_file`` -> ``qq.direct_rules_path`` as the resolved path.
  - user lists -> qq.{admin_ids, allowed_users, blocked_users} as frozensets.

!!! CONSUMPTION-CHAIN WARNING (T1 scope boundary) !!!
The projection PRODUCES the compiled ``qq.*`` layer; T1 does NOT wire the bridge
to READ it. Today the qq-bridge loads config via ``QQBridgeConfig.from_env()``
(env only) and enforces the read-only hard door via ``read_only_enabled()`` (reads
the ``QQ_READ_ONLY`` env var directly) — NEITHER consults the store/profile layer.
Therefore **a profile's ``permissions.read_only=true`` does NOT enforce read-only
until T2 connects the consumption chain**. This is a SECURITY-relevant no-op: do
not assume a read-only profile is enforced. ``test_profile_boundary.py`` locks
this fact so the gap is visible, not silent.

T2 MUST, when it lands:
  1. make the bridge read its effective config from the ConfigStore (env > store >
     profile > default), so ``qq.read_only`` reaches ``QQBridgeConfig.read_only``;
  2. wire ``config.read_only`` into the tool-policy hard door (today the door only
     reads ``QQ_READ_ONLY``), so profile.read_only forces
     ``READONLY_PERMISSION_MODE`` (dontAsk) + the write-tool deny set (Edit/Write/
     Task/…) in app.py — the existing code hard door, not a prompt;
  3. add an end-to-end sim assertion: profile ``read_only=true`` -> the run's
     ``permission_mode == READONLY_PERMISSION_MODE`` AND the disallowed set contains
     the write tools; and delete the T1 boundary-lock test that asserts the gap.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Projection:
    """One nested-profile-path -> flat module.field mapping.

    ``profile_path`` is a dotted path into the parsed ``ProfileConfig`` (attribute
    access, e.g. ``users.allowed``). ``target`` is the ``module.field`` key. A
    ``transform`` (if given) converts the profile value into the stored value; the
    default is identity. A ``needs_file_content`` / ``needs_file_path`` flag tells
    the loader this value is a FILE REFERENCE the loader must resolve (content or
    validated path) before applying the transform.
    """

    profile_path: str
    target: str
    transform: Callable[[Any], Any] | None = None
    needs_file_content: bool = False
    needs_file_path: bool = False


def _ids_to_frozenset(v: Any) -> frozenset[str]:
    if v is None:
        return frozenset()
    return frozenset(str(x).strip() for x in v if str(x).strip())


# Ordered projection table. Order is stable for deterministic golden output.
PROJECTIONS: tuple[Projection, ...] = (
    # identity -----------------------------------------------------------------
    Projection(
        "identity.system_prompt_file",
        "qq.system_prompt",
        needs_file_content=True,
    ),
    # model --------------------------------------------------------------------
    Projection("model.default", "qq.model"),
    # permissions --------------------------------------------------------------
    Projection("permissions.read_only", "qq.read_only"),
    Projection("permissions.permission_mode", "qq.permission_mode"),
    # plugins ------------------------------------------------------------------
    Projection(
        "plugins.direct_rules_file",
        "qq.direct_rules_path",
        needs_file_path=True,
    ),
    Projection(
        "plugins.mcp_servers",
        "qq.mcp_config",
        transform=None,  # serialized by the loader (needs the mcp-json helper)
    ),
    # users --------------------------------------------------------------------
    Projection("users.admins", "qq.admin_ids", transform=_ids_to_frozenset),
    Projection("users.allowed", "qq.allowed_users", transform=_ids_to_frozenset),
    Projection("users.blocked", "qq.blocked_users", transform=_ids_to_frozenset),
    # channels.qq --------------------------------------------------------------
    Projection("channels.qq.private_access", "qq.private_access"),
    Projection("channels.qq.group_reply_at_sender", "qq.group_reply_at_sender"),
    Projection(
        "channels.qq.public_groups",
        "qq.public_groups",
        transform=_ids_to_frozenset,
    ),
    Projection("channels.qq.open_public", "qq.open_public"),
    Projection(
        "channels.qq.group_allowlist",
        "qq.group_allowlist",
        transform=_ids_to_frozenset,
    ),
    Projection("channels.qq.group_invite_policy", "qq.group_invite_policy"),
    Projection("channels.qq.autoleave", "qq.group_autoleave"),
)

# The mcp_servers projection needs a JSON serializer that lives in the loader
# (it knows how to drop disabled servers); mark its target so the loader handles it.
MCP_SERVERS_TARGET = "qq.mcp_config"
