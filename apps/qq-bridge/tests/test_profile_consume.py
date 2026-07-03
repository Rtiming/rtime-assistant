# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""T2 keystone: QQBridgeConfig consumes the git profile layer (design §2 consumption).

Proves the four-layer precedence ``env > store > profile > default`` reaches the
bridge config, and that the plain env path is unchanged when RTIME_PROFILE is unset.

Skips cleanly if admin-core / the profile loader are unavailable.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_HAS_PROFILE_STACK = (
    importlib.util.find_spec("rtime_admin_core") is not None
    and importlib.util.find_spec("rtime_config.profile") is not None
)
pytestmark = pytest.mark.skipif(
    not _HAS_PROFILE_STACK,
    reason="rtime-admin-core / rtime-config profile loader not importable",
)


def _write_profile(root: Path, *, model: str = "ds") -> str:
    (root / "_base" / "prompts").mkdir(parents=True)
    (root / "_base" / "prompts" / "qq-system.md").write_text("base\n", encoding="utf-8")
    (root / "_base" / "qq.yaml").write_text(
        "schema_version: 1\nprofile:\n  id: _base-qq\nmodel:\n  default: kimi\n",
        encoding="utf-8",
    )
    pdir = root / "demo"
    (pdir / "prompts").mkdir(parents=True)
    (pdir / "prompts" / "system.md").write_text("学生会提示词\n", encoding="utf-8")
    (pdir / "profile.yaml").write_text(
        "schema_version: 1\n"
        "profile:\n  id: demo\n  extends: _base/qq\n"
        "identity:\n  name: demo\n  system_prompt_file: prompts/system.md\n"
        f"model:\n  default: {model}\n"
        "permissions:\n  read_only: true\n"
        "channels:\n  qq:\n    private_access: friends_and_temporary\n"
        "    group_reply_at_sender: true\n"
        "    public_groups: ['600']\n    open_public: true\n"
        "    group_allowlist: ['600']\n",
        encoding="utf-8",
    )
    return "demo"


def _cfg(root, monkeypatch):
    from qq_bridge.config import QQBridgeConfig

    # ensure no stray env from the outer shell
    for k in (
        "QQ_READ_ONLY",
        "QQ_SYSTEM_PROMPT",
        "DEFAULT_MODEL",
        "QQ_PUBLIC_GROUPS",
        "QQ_PRIVATE_ACCESS",
        "QQ_GROUP_REPLY_AT_SENDER",
    ):
        monkeypatch.delenv(k, raising=False)
    pid = _write_profile(Path(root))
    return QQBridgeConfig.from_profile(pid, profiles_root=str(root))


def test_profile_value_reaches_config(tmp_path, monkeypatch):
    """profile (below store, above default) supplies read_only / model / prompt / groups."""
    cfg = _cfg(tmp_path / "profiles", monkeypatch)
    assert cfg.read_only is True  # profile > default(False)
    assert cfg.model == "ds"  # profile > default("")
    assert cfg.system_prompt.strip() == "学生会提示词"  # file content projected
    assert cfg.public_groups == frozenset({"600"})
    assert cfg.private_access == "friends_and_temporary"
    assert cfg.group_reply_at_sender is True
    assert cfg.open_public is True  # profile channels.qq.open_public -> qq.open_public


def test_default_wins_where_profile_silent(tmp_path, monkeypatch):
    """A field neither env nor profile sets falls through to the schema default."""
    cfg = _cfg(tmp_path / "profiles", monkeypatch)
    # debounce_max_messages is not in the profile -> schema default (20).
    assert cfg.debounce_max_messages == 20
    assert cfg.group_invite_policy == "reject"  # from _base/qq default


def test_env_wins_over_profile_for_normal_fields(tmp_path, monkeypatch):
    """env (top layer) overrides the profile value for a NORMAL (non-restriction) field.

    NOTE: this is the generic last-wins rule. Security RESTRICTION fields (read_only)
    are the DELIBERATE exception — see test_env_read_only_zero_* which assert env=0
    CANNOT downgrade a profile's read_only (fail-closed union).
    """
    from qq_bridge.config import QQBridgeConfig

    for k in ("QQ_READ_ONLY", "QQ_SYSTEM_PROMPT", "DEFAULT_MODEL"):
        monkeypatch.delenv(k, raising=False)
    root = tmp_path / "profiles"
    pid = _write_profile(root)
    monkeypatch.setenv("DEFAULT_MODEL", "opus")  # profile says ds
    cfg = QQBridgeConfig.from_profile(pid, profiles_root=str(root))
    assert cfg.model == "opus"  # env > profile (normal field: last-wins)


def test_env_zero_does_not_downgrade_profile_read_only(tmp_path, monkeypatch):
    """RESTRICTION exception: QQ_READ_ONLY=0 CANNOT turn off a profile's read_only:true."""
    from qq_bridge.config import QQBridgeConfig

    for k in ("QQ_READ_ONLY", "QQ_SYSTEM_PROMPT", "DEFAULT_MODEL"):
        monkeypatch.delenv(k, raising=False)
    root = tmp_path / "profiles"
    pid = _write_profile(root)  # profile has read_only: true
    monkeypatch.setenv("QQ_READ_ONLY", "0")  # compose default — must be a no-op
    cfg = QQBridgeConfig.from_profile(pid, profiles_root=str(root))
    assert cfg.read_only is True  # fail-closed union: env=0 cannot downgrade


def _write_blocked_profile(root: Path) -> str:
    (root / "_base" / "prompts").mkdir(parents=True)
    (root / "_base" / "prompts" / "qq-system.md").write_text("base\n", encoding="utf-8")
    (root / "_base" / "qq.yaml").write_text(
        "schema_version: 1\nprofile:\n  id: _base-qq\n", encoding="utf-8"
    )
    pdir = root / "blk"
    (pdir / "prompts").mkdir(parents=True)
    (pdir / "prompts" / "system.md").write_text("x\n", encoding="utf-8")
    (pdir / "profile.yaml").write_text(
        "schema_version: 1\n"
        "profile:\n  id: blk\n  extends: _base/qq\n"
        "identity:\n  system_prompt_file: prompts/system.md\n"
        "users:\n  blocked: ['123']\n",
        encoding="utf-8",
    )
    return "blk"


def test_empty_env_does_not_unblock_profile_blocklist(tmp_path, monkeypatch):
    """RESTRICTION exception (id-set): empty QQ_BLOCKED_USERS must NOT un-block the profile.

    compose injects ``QQ_BLOCKED_USERS=${…:-}`` (empty); the generic last-wins would
    wipe a profile's blocklist to frozenset() — a fail-open (blocked users un-blocked).
    The id-set union keeps them.
    """
    from qq_bridge.config import QQBridgeConfig

    root = tmp_path / "profiles"
    pid = _write_blocked_profile(root)
    monkeypatch.setenv("QQ_BLOCKED_USERS", "")  # compose default
    cfg = QQBridgeConfig.from_profile(pid, profiles_root=str(root))
    assert cfg.blocked_users == frozenset({"123"})  # profile blocklist preserved


def test_env_blocklist_unions_with_profile(tmp_path, monkeypatch):
    """env QQ_BLOCKED_USERS ADDS to (unions with) the profile blocklist, never replaces."""
    from qq_bridge.config import QQBridgeConfig

    root = tmp_path / "profiles"
    pid = _write_blocked_profile(root)
    monkeypatch.setenv("QQ_BLOCKED_USERS", "999")
    cfg = QQBridgeConfig.from_profile(pid, profiles_root=str(root))
    assert cfg.blocked_users == frozenset({"123", "999"})  # union, not replace


def test_load_without_profile_is_from_env(monkeypatch):
    """RTIME_PROFILE unset -> QQBridgeConfig.load() == from_env() (backward compatible)."""
    from qq_bridge.config import QQBridgeConfig

    monkeypatch.delenv("RTIME_PROFILE", raising=False)
    monkeypatch.delenv("QQ_READ_ONLY", raising=False)
    cfg = QQBridgeConfig.load()
    assert cfg.read_only is False
    from_env = QQBridgeConfig.from_env()
    assert cfg.model == from_env.model
    assert cfg.read_only == from_env.read_only


def test_cutover_smoke_compose_empty_env_does_not_shadow_profile(monkeypatch):
    """Deployment smoke test (studentunion cutover regression).

    compose injects ``${QQ_X:-}`` EMPTY strings for profile-owned keys the docker.env
    no longer sets. Those empties must NOT shadow the git profile — the field must
    resolve to the profile value, not the schema default. This is the exact condition
    that silently broke the first cutover attempt (prompt/mcp/groups fell to default).
    """
    import os

    root = os.path.join(os.path.dirname(__file__), "..", "..", "..", "profiles")
    if not os.path.isdir(os.path.join(root, "studentunion")):
        import pytest

        pytest.skip("profiles/studentunion not present")
    monkeypatch.setenv("RTIME_PROFILE", "studentunion")
    monkeypatch.setenv("RTIME_PROFILES_ROOT", os.path.abspath(root))
    # simulate compose ${X:-} empties for every profile-owned key
    for k in (
        "QQ_SYSTEM_PROMPT", "QQ_MCP_CONFIG", "QQ_PUBLIC_GROUPS", "QQ_ADMIN_IDS",
        "QQ_GROUP_ALLOWLIST", "QQ_PRIVATE_ACCESS", "QQ_GROUP_REPLY_AT_SENDER",
        "QQ_DEFAULT_MODEL", "DEFAULT_MODEL",
    ):
        monkeypatch.setenv(k, "")
    from qq_bridge.config import QQBridgeConfig

    c = QQBridgeConfig.load()
    # profile values project through the empty env (NOT the schema defaults)
    assert "8781" in (c.mcp_config or ""), "mcp must come from profile, not empty env"
    assert list(c.public_groups), "public_groups须来自profile(env全空+schema默认为空,非空即证明)"
    assert c.private_access == "friends_and_temporary", "private access from profile"
    assert c.group_reply_at_sender is True, "group reply-at-sender from profile"
    assert c.read_only is True, "read_only hard door from profile"
    assert "学生会" in (c.system_prompt or ""), "system prompt from profile file"
