# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "brain-intake"))

import memory_schema  # noqa: E402

VALID_CARD = """---
type: memory-card
claim: 用户在固体物理复习中偏好先看页图再读公式转写
scope: study-review
source: gateway会话2026-06-11T09 run log
observed_at: 2026-06-11
confidence: user-stated
layer: situational
expires: 2026-09-01
supersedes: []
sensitivity: normal
unlock_hints: [固体物理, 复习]
---
偏好展开说明。
"""

VALID_HYPOTHESIS = """---
type: hypothesis
claim: 用户考前更偏好刷题而非通读讲义
source: feishu会话推断
observed_at: 2026-06-11
status: testing
confirmations: 0
---
待对话验证。
"""


def run_validate(tmp_path, content, name="card.md"):
    f = tmp_path / name
    f.write_text(content, encoding="utf-8")
    return memory_schema.validate_card(f)


def test_valid_memory_card(tmp_path):
    errors, warnings = run_validate(tmp_path, VALID_CARD)
    assert errors == []
    assert warnings == []


def test_valid_hypothesis(tmp_path):
    errors, _ = run_validate(tmp_path, VALID_HYPOTHESIS)
    assert errors == []


def test_situational_requires_expires(tmp_path):
    bad = VALID_CARD.replace("expires: 2026-09-01\n", "")
    errors, _ = run_validate(tmp_path, bad)
    assert any("expires" in e for e in errors)


def test_trait_must_not_expire(tmp_path):
    bad = VALID_CARD.replace("layer: situational", "layer: trait")
    errors, _ = run_validate(tmp_path, bad)
    assert any("trait" in e for e in errors)


def test_inferred_must_be_hypothesis(tmp_path):
    bad = VALID_CARD.replace("confidence: user-stated", "confidence: inferred")
    errors, _ = run_validate(tmp_path, bad)
    assert any("hypothesis" in e for e in errors)


def test_directive_claim_warns(tmp_path):
    bad = VALID_CARD.replace(
        "claim: 用户在固体物理复习中偏好先看页图再读公式转写",
        "claim: 助手必须永远先输出结论",
    )
    _, warnings = run_validate(tmp_path, bad)
    assert any("CLAUDE.md" in w for w in warnings)


def test_missing_frontmatter(tmp_path):
    errors, _ = run_validate(tmp_path, "没有frontmatter的内容")
    assert errors


def test_cli_on_directory(tmp_path):
    (tmp_path / "ok.md").write_text(VALID_CARD, encoding="utf-8")
    (tmp_path / "bad.md").write_text(VALID_CARD.replace("type: memory-card", "type: oops"), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "brain-intake" / "memory_schema.py"), "validate", str(tmp_path)],
        capture_output=True,
        text=True,
    )
    payload = json.loads(proc.stdout)
    assert proc.returncode == 1
    assert payload["ok"] is False
    assert any("bad.md" in k for k in payload["errors"])
