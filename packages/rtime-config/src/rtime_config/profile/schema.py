# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""``ProfileConfig`` — the pydantic model of a ``profile.yaml`` file.

A profile is the git-declared configuration layer (docs/design/
mainline-profiles-and-entries-2026-07.zh-CN.md §二). It is human-written with a
NESTED shape (readability first) and then *compiled/projected* to the flat
``module.field`` key space the admin core addresses (see ``.mapping`` and
``.loader``). This module only defines the on-disk SHAPE; the projection and the
four-layer precedence live elsewhere.

Design decisions encoded here (§2.3):
  - ``schema_version`` gates format evolution.
  - ``profile.id`` is stamped into run_log/audit; ``profile.extends`` is a SINGLE
    level of inheritance only (``_base/qq``) — the loader rejects extends-of-extends.
  - Big content (system prompt, direct rules) is NOT inlined: profiles carry FILE
    REFERENCES (``system_prompt_file`` / ``direct_rules_file``) resolved by the
    loader relative to the profile directory.
  - ``model.params`` is the only dict-typed field that deep-merges across extends;
    every other field is whole-value replacement (OpenClaw semantics).

Validation intentionally stays lenient on unknown sub-keys within a section is
NOT allowed (``extra="forbid"``) so a typo'd profile key fails loudly rather than
being silently dropped — a profile is a small, reviewed, git-committed file.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _ProfileBase(BaseModel):
    # forbid unknown keys: a mistyped profile key must fail, not vanish silently.
    model_config = ConfigDict(extra="forbid")


class ProfileMeta(_ProfileBase):
    """``profile:`` block — identity + single-level inheritance."""

    id: str = Field(description="Profile id; stamped into run_log/audit per record.")
    extends: str | None = Field(
        default=None,
        description="Single parent profile ref (e.g. '_base/qq'). None = no parent. "
        "Single-level only: the loader rejects a parent that itself extends.",
    )


class Identity(_ProfileBase):
    name: str | None = Field(default=None, description="Human-facing display name.")
    system_prompt_file: str | None = Field(
        default=None,
        description="Path (relative to the profile dir) to the system prompt file; "
        "the loader expands it to the file CONTENT when projecting to qq.system_prompt.",
    )


class ModelSection(_ProfileBase):
    default: str | None = Field(
        default=None,
        description="Default model alias (resolved via model-registry.json).",
    )
    pinned_for_non_admin: bool | None = Field(
        default=None,
        description="Lock non-admin users to the default model (explicit naming of "
        "current behaviour).",
    )
    admin_aliases: list[str] | None = Field(
        default=None, description="Model aliases an admin may select in private chat."
    )
    params: dict[str, Any] | None = Field(
        default=None,
        description="Model params (temperature, etc.). The ONLY dict field that "
        "DEEP-merges across a single extends; everything else is whole replacement.",
    )


class Permissions(_ProfileBase):
    read_only: bool | None = Field(
        default=None,
        description="Instance read-only hard door. Drives the code hard door + forced "
        "permission_mode (NOT trusted from the profile: read_only=True forces READONLY "
        "in code regardless of permission_mode).",
    )
    permission_mode: str | None = Field(
        default=None,
        description="Tool permission mode (explicit). Ignored when read_only=True.",
    )
    tool_allow_extra: list[str] | None = Field(
        default=None, description="Extra allowlist entries appended to the base policy."
    )
    tool_deny_extra: list[str] | None = Field(
        default=None,
        description="Extra deny entries (deny wins). A bare 'Bash' is rejected by the "
        "policy layer (it would kill Bash(rtime-web-fetch *)).",
    )


class Library(_ProfileBase):
    gateway_url: str | None = Field(
        default=None, description="brain library MCP gateway URL."
    )
    scope: list[str] | None = Field(
        default=None,
        description="Allowed path prefixes — the single source that generates the "
        "gateway policy + the compose read-only sub-mount list.",
    )
    redact_sensitive: bool | None = Field(default=None)
    hide_excluded_in_results: bool | None = Field(default=None)


class Plugins(_ProfileBase):
    direct_rules_file: str | None = Field(
        default=None,
        description="Path (relative to the profile dir) to the FAQ direct-rules JSON; "
        "null = disabled. The loader validates the path EXISTS (content is loaded by "
        "the bridge at runtime, so the projection carries the resolved path).",
    )
    campus_fetch: dict[str, Any] | None = Field(default=None)
    mcp_servers: dict[str, Any] | None = Field(
        default=None,
        description="MCP servers keyed by name, each with an explicit 'enabled' flag "
        "(kills the 'empty JSON = disabled' implicit semantics). Projected to "
        "qq.mcp_config as serialized JSON with enabled=false entries removed.",
    )


class Users(_ProfileBase):
    admins: list[str] | None = Field(default=None)
    allowed: list[str] | None = Field(default=None)
    blocked: list[str] | None = Field(default=None)


class QQChannel(_ProfileBase):
    account_ref: str | None = Field(
        default=None,
        description="Name pointing at the NapCat state dir / secret — NEVER a credential.",
    )
    private_access: str | None = Field(
        default=None,
        description="私聊开放策略: admin_allowed=仅 admin+allowed_users; friends=好友私聊;"
        "friends_and_temporary=好友私聊+群临时会话。好友请求是否通过另行控制。",
    )
    public_groups: list[str] | None = Field(default=None)
    open_public: bool | None = Field(
        default=None,
        description="开放答疑模式:True => 任何群里任何非黑名单成员 @bot 都能提问,"
        "不再要求群在 public_groups 白名单里。black>admin>user 次序不变;仅放开群准入,"
        "私聊由 admin/allowed/private_access 单独控制;不放开 read_only/库 scope。"
        "配合 autoleave=false(bot 留在每个群)。",
    )
    group_reply_at_sender: bool | None = Field(
        default=None,
        description="群聊回复是否在文本消息开头 @ 提问者;公开答疑实例建议打开。",
    )
    group_allowlist: list[str] | None = Field(default=None)
    group_invite_policy: str | None = Field(default=None)
    autoleave: bool | None = Field(default=None)


class WebChannel(_ProfileBase):
    """``channels.web:`` block — the browser Q&A entry (design §五).

    A profile is web-ENABLED iff this block is PRESENT (``GET /api/profiles`` lists
    exactly the profiles declaring ``channels.web``). The block may be empty
    (``web: {}``) to opt in with the profile-wide defaults, or override the two
    knobs a web session needs to differ from the QQ session:

      - ``system_prompt_file``: a web-specific prompt (the page renders markdown +
        KaTeX, so the QQ "plain-text only" prompt is wrong here). Falls back to
        ``identity.system_prompt_file`` when unset.
      - ``mcp_servers``: the gateway the web session reaches (web is a gateway-only
        consumer — no /mnt/brain mount, design §5.3). Falls back to
        ``plugins.mcp_servers`` when unset.

    read_only / library scope / user access come from the shared profile core
    (``permissions`` / ``library`` / ``users``) — a web session enforces the SAME
    hard door as the QQ session (design: "与QQ渠道行为一致是验收标准").
    """

    name: str | None = Field(
        default=None,
        description="Web-facing display name for the profile dropdown. Falls back to "
        "identity.name (then the profile id).",
    )
    description: str | None = Field(
        default=None,
        description="One-line blurb shown under the name in the /api/profiles list.",
    )
    system_prompt_file: str | None = Field(
        default=None,
        description="Path (relative to the profile dir) to a WEB-specific system "
        "prompt; the loader expands it to file CONTENT. Unset => fall back to "
        "identity.system_prompt_file.",
    )
    mcp_servers: dict[str, Any] | None = Field(
        default=None,
        description="Web-session MCP servers (same shape/semantics as "
        "plugins.mcp_servers: explicit 'enabled', credential-free). Unset => fall "
        "back to plugins.mcp_servers.",
    )
    render: str | None = Field(
        default=None,
        description="Override output.render for the web channel (default markdown).",
    )


class Channels(_ProfileBase):
    # dict-shaped for extension (feishu later); qq + web typed for this round.
    qq: QQChannel | None = Field(default=None)
    web: WebChannel | None = Field(default=None)


class Output(_ProfileBase):
    render: str | None = Field(
        default=None,
        description="plain_text | rich | markdown — drives the per-channel renderer.",
    )


class ProfileConfig(_ProfileBase):
    """The whole profile.yaml document (§2.3)."""

    schema_version: int = Field(description="Profile file format version.")
    profile: ProfileMeta
    identity: Identity = Field(default_factory=Identity)
    model: ModelSection = Field(default_factory=ModelSection)
    permissions: Permissions = Field(default_factory=Permissions)
    library: Library = Field(default_factory=Library)
    plugins: Plugins = Field(default_factory=Plugins)
    users: Users = Field(default_factory=Users)
    channels: Channels = Field(default_factory=Channels)
    output: Output = Field(default_factory=Output)


SUPPORTED_SCHEMA_VERSION = 1
