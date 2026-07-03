# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Profile schema + loader + mapping tests (T1 acceptance matrix, design §二).

Covered:
  - mapping golden: a nested profile.yaml -> the expected flat module.field keys;
  - single-level extends only (extends-of-extends -> error);
  - whole-value replacement semantics (child list replaces parent list; only
    model.params deep-merges);
  - file-ref resolution (system_prompt_file -> content; direct_rules_file ->
    validated existing path; missing file -> error);
  - x-secret rejection: a profile key mapping to a secret field -> load failure;
  - registry validation of the compiled layer (env-independent);
  - schema_version gate + unknown-key rejection.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from rtime_config.profile import (
    ProfileConfig,
    ProfileError,
    ProfileSecretError,
    load_profile,
)

# --- fixtures -------------------------------------------------------------------


def _write(root: Path, rel: str, content: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


@pytest.fixture
def base_qq(tmp_path: Path) -> Path:
    """A _base/qq.yaml parent + its prompt file. Returns the profiles root."""
    root = tmp_path / "profiles"
    _write(root, "_base/prompts/qq-system.md", "base system prompt\n")
    _write(
        root,
        "_base/qq.yaml",
        """
schema_version: 1
profile:
  id: _base-qq
identity:
  name: base
  system_prompt_file: prompts/qq-system.md
model:
  default: kimi
  params: {temperature: 0.2, top_p: 0.9}
permissions:
  read_only: false
users:
  admins: []
  allowed: ["base-user"]
channels:
  qq:
    group_invite_policy: reject
    autoleave: true
output:
  render: plain_text
""",
    )
    return root


def _demo(root: Path, body: str, *, with_prompt=True, with_rules=True) -> Path:
    d = root / "demo"
    if with_prompt:
        _write(root, "demo/prompts/system.md", "demo system prompt\n")
    if with_rules:
        _write(root, "demo/direct-rules.json", '{"rules": []}\n')
    _write(root, "demo/profile.yaml", body)
    return d


# --- mapping golden -------------------------------------------------------------


GOLDEN_PROFILE = """
schema_version: 1
profile:
  id: demo
  extends: _base/qq
identity:
  name: demo 答疑
  system_prompt_file: prompts/system.md
model:
  default: ds
permissions:
  read_only: true
plugins:
  direct_rules_file: direct-rules.json
  mcp_servers:
    rtime-library-gateway: {type: http, url: "http://127.0.0.1:8781/mcp", enabled: true}
    off-one: {type: http, url: "http://x", enabled: false}
users:
  admins: ["111"]
  allowed: ["1", "2"]
  blocked: ["9"]
channels:
  qq:
    private_access: friends_and_temporary
    group_reply_at_sender: true
    public_groups: ["987"]
    open_public: true
    group_allowlist: ["987"]
    group_invite_policy: allow
    autoleave: false
output:
  render: plain_text
"""


def test_mapping_golden(base_qq: Path, qq_registry, validate_state_fn):
    demo = _demo(base_qq, GOLDEN_PROFILE)
    cp = load_profile(demo, registry=qq_registry, validate=validate_state_fn)

    expected = {
        "qq.system_prompt": "demo system prompt\n",
        "qq.model": "ds",
        "qq.read_only": True,
        "qq.direct_rules_path": str((demo / "direct-rules.json").resolve()),
        "qq.mcp_config": (
            '{"mcpServers": {"rtime-library-gateway": '
            '{"type": "http", "url": "http://127.0.0.1:8781/mcp"}}}'
        ),
        "qq.admin_ids": frozenset({"111"}),
        "qq.allowed_users": frozenset({"1", "2"}),
        "qq.blocked_users": frozenset({"9"}),
        "qq.private_access": "friends_and_temporary",
        "qq.group_reply_at_sender": True,
        "qq.public_groups": frozenset({"987"}),
        "qq.open_public": True,
        "qq.group_allowlist": frozenset({"987"}),
        "qq.group_invite_policy": "allow",
        "qq.group_autoleave": False,
    }
    assert cp.layer == expected
    assert cp.profile_id == "demo"
    assert cp.parent_id == "_base-qq"


def test_mapping_projects_only_to_known_qq_fields(base_qq: Path, qq_registry):
    demo = _demo(base_qq, GOLDEN_PROFILE)
    cp = load_profile(demo, registry=qq_registry)
    props = qq_registry.get_schema("qq")["properties"]
    for path in cp.layer:
        module, fld = path.split(".", 1)
        assert module == "qq"
        assert fld in props, f"projected to unknown field {path}"


# --- extends single-level -------------------------------------------------------


def test_extends_single_level_only(base_qq: Path, qq_registry):
    # make _base/qq.yaml itself extend something -> loading a child must error.
    _write(
        base_qq,
        "_base/qq.yaml",
        """
schema_version: 1
profile:
  id: _base-qq
  extends: _base/grandparent
""",
    )
    demo = _demo(
        base_qq,
        """
schema_version: 1
profile:
  id: demo
  extends: _base/qq
""",
        with_prompt=False,
        with_rules=False,
    )
    with pytest.raises(ProfileError, match="single-level"):
        load_profile(demo, registry=qq_registry)


def test_extends_missing_parent_errors(tmp_path: Path, qq_registry):
    root = tmp_path / "profiles"
    demo = _demo(
        root,
        """
schema_version: 1
profile:
  id: demo
  extends: _base/nope
""",
        with_prompt=False,
        with_rules=False,
    )
    with pytest.raises(ProfileError, match="not found"):
        load_profile(demo, registry=qq_registry)


# --- whole-value replacement + model.params deep-merge --------------------------


def test_list_is_whole_value_replaced(base_qq: Path, qq_registry):
    # base allowed = ["base-user"]; child sets its own -> replaces, not merges.
    demo = _demo(
        base_qq,
        """
schema_version: 1
profile:
  id: demo
  extends: _base/qq
identity:
  system_prompt_file: prompts/system.md
users:
  allowed: ["child-only"]
""",
        with_rules=False,
    )
    cp = load_profile(demo, registry=qq_registry)
    assert cp.layer["qq.allowed_users"] == frozenset({"child-only"})


def test_model_params_deep_merge(base_qq: Path, qq_registry):
    # base params = {temperature:0.2, top_p:0.9}; child overrides temperature only.
    demo = _demo(
        base_qq,
        """
schema_version: 1
profile:
  id: demo
  extends: _base/qq
identity:
  system_prompt_file: prompts/system.md
model:
  params: {temperature: 0.7}
""",
        with_rules=False,
    )
    cp = load_profile(demo, registry=qq_registry)
    assert cp.config.model.params == {"temperature": 0.7, "top_p": 0.9}


# --- file refs ------------------------------------------------------------------


def test_missing_system_prompt_file_errors(base_qq: Path, qq_registry):
    demo = _demo(
        base_qq,
        """
schema_version: 1
profile:
  id: demo
  extends: _base/qq
identity:
  system_prompt_file: prompts/does-not-exist.md
""",
        with_prompt=False,
        with_rules=False,
    )
    with pytest.raises(ProfileError, match="referenced file not found"):
        load_profile(demo, registry=qq_registry)


def test_missing_direct_rules_file_errors(base_qq: Path, qq_registry):
    demo = _demo(
        base_qq,
        """
schema_version: 1
profile:
  id: demo
  extends: _base/qq
identity:
  system_prompt_file: prompts/system.md
plugins:
  direct_rules_file: nope.json
""",
        with_rules=False,
    )
    with pytest.raises(ProfileError, match="referenced file not found"):
        load_profile(demo, registry=qq_registry)


def test_direct_rules_path_is_resolved_not_content(base_qq: Path, qq_registry):
    demo = _demo(
        base_qq,
        """
schema_version: 1
profile:
  id: demo
  extends: _base/qq
identity:
  system_prompt_file: prompts/system.md
plugins:
  direct_rules_file: direct-rules.json
""",
    )
    cp = load_profile(demo, registry=qq_registry)
    assert cp.layer["qq.direct_rules_path"] == str(
        (demo / "direct-rules.json").resolve()
    )


# --- x-secret rejection ---------------------------------------------------------


def test_secret_field_in_profile_rejected(base_qq: Path, qq_registry):
    # add a projection-like key by targeting a secret field directly through the
    # loader's reject step: build a layer that maps to qq.access_token (secret).
    # The mapping table never targets a secret, so simulate a hostile projection
    # by validating a layer that includes a secret path.
    from rtime_config.profile.loader import _reject_secrets

    with pytest.raises(ProfileSecretError, match="secret"):
        _reject_secrets({"qq.access_token": "sk-leak"}, qq_registry)


def test_valid_profile_has_no_secret_keys(base_qq: Path, qq_registry):
    demo = _demo(base_qq, GOLDEN_PROFILE)
    cp = load_profile(demo, registry=qq_registry)
    props = qq_registry.get_schema("qq")["properties"]
    for path in cp.layer:
        fld = path.split(".", 1)[1]
        assert not props[fld].get("x-secret"), f"{path} is a secret!"


# --- registry validation of compiled output -------------------------------------


def test_compiled_layer_validated_against_registry(
    base_qq: Path, qq_registry, validate_state_fn
):
    # a valid golden profile passes validation.
    demo = _demo(base_qq, GOLDEN_PROFILE)
    cp = load_profile(demo, registry=qq_registry, validate=validate_state_fn)
    assert cp.layer["qq.read_only"] is True


# --- schema gates ---------------------------------------------------------------


def test_unsupported_schema_version_errors(base_qq: Path, qq_registry):
    demo = _demo(
        base_qq,
        """
schema_version: 99
profile:
  id: demo
  extends: _base/qq
identity:
  system_prompt_file: prompts/system.md
""",
        with_rules=False,
    )
    with pytest.raises(ProfileError, match="schema_version"):
        load_profile(demo, registry=qq_registry)


def test_unknown_top_level_key_rejected(tmp_path: Path, qq_registry):
    root = tmp_path / "profiles"
    demo = _demo(
        root,
        """
schema_version: 1
profile:
  id: demo
bogus_section: {x: 1}
""",
        with_prompt=False,
        with_rules=False,
    )
    with pytest.raises(ProfileError):
        load_profile(demo, registry=qq_registry)


# --- adversarial-review regressions (2026-07) -----------------------------------


def test_load_profile_requires_registry(base_qq: Path):
    """Defect #1: registry is REQUIRED — a None registry must RAISE, never
    silently return a layer that skipped the x-secret door (fail-open)."""
    demo = _demo(base_qq, GOLDEN_PROFILE)
    with pytest.raises(ProfileError, match="requires a registry"):
        load_profile(demo, registry=None)


def test_reject_secrets_fails_closed_on_unregistered_module(base_qq: Path):
    """Defect #2: if the module carrying a compiled key is not in the registry,
    the secret door cannot classify it and must FAIL CLOSED (raise), never assume
    'not secret'."""
    from rtime_admin_core import default_registry
    from rtime_config.profile.loader import _reject_secrets

    reg_without_qq = default_registry()  # no qq module
    with pytest.raises(ProfileSecretError, match="not in registry"):
        _reject_secrets({"qq.access_token": "sk-leak"}, reg_without_qq)


def test_is_secret_path_raises_on_unregistered_module():
    """Defect #2 (unit): is_secret_path raises SecretClassificationError, not False."""
    from rtime_admin_core import default_registry
    from rtime_config.profile._meta import (
        SecretClassificationError,
        is_secret_path,
    )

    reg = default_registry()  # no qq
    with pytest.raises(SecretClassificationError):
        is_secret_path(reg, "qq.access_token")


@pytest.mark.parametrize(
    "inline",
    [
        'rtime-library-gateway: {type: http, url: "http://x", token: "sk-abc"}',
        'g: {type: http, url: "http://x", headers: {Authorization: "Bearer sk"}}',
        'g: {type: http, url: "http://x", env: {API_KEY: "sk-1"}}',
        'g: {type: stdio, command: "x", password: "hunter2"}',
    ],
)
def test_mcp_servers_inline_credentials_rejected(base_qq: Path, qq_registry, inline):
    """Defect #3: mcp_servers must not inline credential-looking values in git;
    token/key/secret/password/authorization/headers-auth -> load rejected."""
    demo = _demo(
        base_qq,
        f"""
schema_version: 1
profile:
  id: demo
  extends: _base/qq
identity:
  system_prompt_file: prompts/system.md
plugins:
  mcp_servers:
    {inline}
""",
        with_rules=False,
    )
    with pytest.raises(ProfileSecretError, match="credential"):
        load_profile(demo, registry=qq_registry)


def test_mcp_servers_disabled_inline_credential_not_scanned(base_qq: Path, qq_registry):
    """A DISABLED server is dropped before scanning (contributes nothing), so its
    inline token does not trip the credential gate — it never reaches mcp_config."""
    demo = _demo(
        base_qq,
        """
schema_version: 1
profile:
  id: demo
  extends: _base/qq
identity:
  system_prompt_file: prompts/system.md
plugins:
  mcp_servers:
    off-one: {type: http, url: "http://x", token: "sk-abc", enabled: false}
""",
        with_rules=False,
    )
    cp = load_profile(demo, registry=qq_registry)
    assert cp.layer["qq.mcp_config"] == '{"mcpServers": {}}'


def test_mcp_servers_reference_url_allowed(base_qq: Path, qq_registry):
    """A plain url/type with no credential keys is fine (reference, not secret)."""
    demo = _demo(
        base_qq,
        """
schema_version: 1
profile:
  id: demo
  extends: _base/qq
identity:
  system_prompt_file: prompts/system.md
plugins:
  mcp_servers:
    rtime-library-gateway: {type: http, url: "http://127.0.0.1:8781/mcp", enabled: true}
""",
        with_rules=False,
    )
    cp = load_profile(demo, registry=qq_registry)
    assert "rtime-library-gateway" in cp.layer["qq.mcp_config"]
    assert "token" not in cp.layer["qq.mcp_config"]


# --- schema model spot checks ---------------------------------------------------


def test_profile_config_minimal():
    cfg = ProfileConfig.model_validate({"schema_version": 1, "profile": {"id": "x"}})
    assert cfg.profile.id == "x"
    assert cfg.profile.extends is None
    assert cfg.permissions.read_only is None  # sparse: unset by default
    assert cfg.channels.web is None  # web channel absent by default (not web-enabled)


def test_web_channel_present_and_typed():
    """A channels.web block is parsed into a typed WebChannel (T5b web-enabled flag)."""
    cfg = ProfileConfig.model_validate(
        {
            "schema_version": 1,
            "profile": {"id": "w"},
            "channels": {
                "web": {
                    "name": "网页助手",
                    "description": "浏览器问答",
                    "system_prompt_file": "prompts/web.md",
                    "render": "markdown",
                    "mcp_servers": {"g": {"type": "http", "url": "u", "enabled": True}},
                }
            },
        }
    )
    web = cfg.channels.web
    assert web is not None
    assert web.name == "网页助手"
    assert web.render == "markdown"
    assert web.system_prompt_file == "prompts/web.md"
    assert web.mcp_servers["g"]["enabled"] is True


def test_web_channel_empty_block_is_web_enabled():
    """``web: {}`` opts in with profile-wide defaults (present-ness is the flag)."""
    cfg = ProfileConfig.model_validate(
        {"schema_version": 1, "profile": {"id": "w"}, "channels": {"web": {}}}
    )
    assert cfg.channels.web is not None
    assert cfg.channels.web.name is None  # falls back to identity.name at project time


def test_web_channel_rejects_unknown_key():
    """extra='forbid' guards a typo'd web key (fails loud, not silently dropped)."""
    with pytest.raises(Exception):  # pydantic ValidationError
        ProfileConfig.model_validate(
            {
                "schema_version": 1,
                "profile": {"id": "w"},
                "channels": {"web": {"systemPrompt": "typo"}},
            }
        )


# --- end-to-end: loader -> store profile layer + reload -------------------------


def test_loader_feeds_store_and_reload(base_qq: Path, qq_registry, validate_state_fn):
    """Compile a profile, inject it into a ConfigStore, then reload a second one:
    the whole T1 chain (loader projection -> four-layer read -> atomic reload)."""
    from rtime_admin_core import (
        ConfigStore,
        InMemoryHistory,
        MemoryBackend,
    )

    demo = _demo(base_qq, GOLDEN_PROFILE)
    cp = load_profile(demo, registry=qq_registry, validate=validate_state_fn)

    store = ConfigStore(
        qq_registry,
        MemoryBackend(),
        InMemoryHistory(),
        env={},
        secret_salt="fixed",
        profile_layer=cp.layer,
    )
    # profile layer wins over defaults
    assert store.get("qq.read_only") is True
    assert store.provenance("qq.read_only") == "profile"
    # a store override survives a reload
    store.set("qq.model", "kimi", ts="t1", snapshot_id="s1")

    # reload with read_only flipped off -> hot/restart split, store.model survives.
    new_layer = dict(cp.layer)
    new_layer["qq.read_only"] = False
    res = store.reload_profile(new_layer, ts="t2", snapshot_id="s2")
    assert store.get("qq.read_only") is False
    assert store.get("qq.model") == "kimi"  # store override survived
    assert store.provenance("qq.model") == "store"
    # read_only is restart-level (design §2.10), so it lands in restart_required.
    assert "qq.read_only" in res.restart_required


# --- the shipped _base skeleton loads (guards profiles/ in git) -----------------


def test_shipped_base_qq_parses():
    repo_root = Path(__file__).resolve().parents[3]
    base = repo_root / "profiles" / "_base" / "qq.yaml"
    if not base.is_file():
        pytest.skip("profiles/_base/qq.yaml not present")
    import yaml
    from rtime_config.profile.schema import ProfileConfig as PC

    data = yaml.safe_load(base.read_text(encoding="utf-8"))
    PC.model_validate(data)  # must be a valid ProfileConfig


@pytest.mark.parametrize("pid", ["owner", "studentunion"])
def test_shipped_profiles_declare_web_channel(pid, qq_registry, validate_state_fn):
    """The shipped owner/studentunion profiles compile AND declare channels.web (T5b).

    Guards that the git profiles the web-chat dropdown lists stay web-enabled and
    keep compiling against the real registry (secret door + validation).
    """
    repo_root = Path(__file__).resolve().parents[3]
    profiles_root = repo_root / "profiles"
    if not (profiles_root / pid / "profile.yaml").is_file():
        pytest.skip(f"profiles/{pid} not present")
    cp = load_profile(
        profiles_root / pid,
        registry=qq_registry,
        profiles_root=profiles_root,
        validate=validate_state_fn,
    )
    assert cp.config.channels.web is not None  # web-enabled
    # the web channel points its prompt at a real, resolvable file.
    web = cp.config.channels.web
    ref = web.system_prompt_file or cp.config.identity.system_prompt_file
    assert ref and (profiles_root / pid / ref).is_file()
