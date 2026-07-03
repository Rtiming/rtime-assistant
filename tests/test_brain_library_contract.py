# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""H M0 内容合同夜巡测试(validity/consistency,只读)。

覆盖:存量宽容(缺字段=warning 不翻 ok)、非法值=error 翻 ok、superseded 悬挂、
stub 检测、路径前缀过滤、索引 drift 双向差、报告无正文、CLI 退出码。
设计: docs/design/library-lifecycle-maintenance-2026-07.zh-CN.md §四/§五/§九 M0。
"""

from __future__ import annotations

import importlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "packages" / "brain-library" / "src"
SECRET_BODY = "UNIQUE-BODY-MARKER-must-never-leak-into-reports"


def _mod(name: str):
    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))
    return importlib.import_module(name)


def _make_brain(tmp_path: Path) -> Path:
    brain = tmp_path / "brain"
    k = brain / "knowledge" / "ustc"
    k.mkdir(parents=True)
    # 合规文档:全字段
    (k / "good.md").write_text(
        "---\nstatus: active\nsource: https://example.edu/a\n"
        f"review_after: 2026-12-01\nversion: 2\n---\n# Good\n{SECRET_BODY} 这是一篇足够长的正文内容,超过stub阈值。\n",
        encoding="utf-8",
    )
    # 存量文档:无 frontmatter(只应产 warning,不翻 ok)
    (k / "legacy.md").write_text(
        f"# Legacy\n{SECRET_BODY} 存量文档没有任何frontmatter,合同必须宽容对待它。\n",
        encoding="utf-8",
    )
    # 非法 status + 坏日期 + 坏版本(三个 error)
    (k / "bad.md").write_text(
        "---\nstatus: published\nreview_after: someday\nversion: zero\n"
        "source: https://example.edu/b\n---\n# Bad\n这是一段用来越过stub阈值的填充正文,足够长足够长足够长足够长足够长足够长足够长。\n",
        encoding="utf-8",
    )
    # superseded 无 target(error)
    (k / "sup-missing.md").write_text(
        "---\nstatus: superseded\nsource: https://example.edu/c\n---\n# S\n"
        "被取代但没有指向替代文档,必须报错。这是一段用来越过stub阈值的填充正文,足够长足够长足够长足够长足够长足够长足够长。\n",
        encoding="utf-8",
    )
    # superseded 悬挂 target(error)
    (k / "sup-dangling.md").write_text(
        "---\nstatus: superseded\nsuperseded_by: knowledge/ustc/ghost.md\n"
        "source: https://example.edu/d\n---\n# S2\n悬挂指向,必须报错。这是一段用来越过stub阈值的填充正文,足够长足够长足够长足够长足够长足够长足够长。\n",
        encoding="utf-8",
    )
    # stub(warning)
    (k / "stub.md").write_text(
        "---\nstatus: active\nsource: https://example.edu/e\n---\nshort\n",
        encoding="utf-8",
    )
    # 前缀过滤用的另一子树
    other = brain / "knowledge" / "other"
    other.mkdir(parents=True)
    (other / "elsewhere.md").write_text(
        "---\nstatus: bogus-status\nsource: https://example.edu/f\n---\n# E\n"
        "另一子树里的非法status,用于验证path_prefix过滤。这是一段用来越过stub阈值的填充正文,足够长足够长足够长足够长足够长足够长足够长。\n",
        encoding="utf-8",
    )
    return brain


def test_validate_front_pure_rules():
    contract = _mod("brain_library.contract")
    codes = {c for _, c, _, _ in contract.validate_front({}, body_chars=1000)}
    assert codes == {"missing_status", "missing_source"}
    issues = contract.validate_front(
        {"status": "superseded", "source": "https://x", "review_after": "2026-13-99x"},
        body_chars=10,
    )
    codes = {c for _, c, _, _ in issues}
    assert "superseded_without_target" in codes
    assert "invalid_review_after" in codes
    assert "stub_body" in codes
    # 全对=零 issue
    assert (
        contract.validate_front(
            {"status": "active", "source": "https://x", "review_after": "2026-12-01", "version": "3"},
            body_chars=1000,
        )
        == []
    )


def test_scan_contract_report(tmp_path):
    contract = _mod("brain_library.contract")
    brain = _make_brain(tmp_path)
    report = contract.scan_contract(brain)
    assert report["files_scanned"] == 7
    assert report["files_with_frontmatter"] == 6
    by_code = report["by_code"]
    # status非枚举=warning(nonstandard_status,真库基线后定的存量宽容语义),非error
    assert by_code["nonstandard_status"]["count"] == 2  # bad.md + elsewhere.md
    assert by_code["nonstandard_status"]["severity"] == "warning"
    assert by_code["invalid_review_after"]["count"] == 1
    assert by_code["invalid_version"]["count"] == 1
    assert by_code["superseded_without_target"]["count"] == 1
    assert by_code["dangling_superseded_by"]["count"] == 1
    assert by_code["missing_status"]["count"] == 1  # legacy.md
    assert by_code["stub_body"]["count"] == 1
    assert report["issues"]["error"] == 4  # 坏日期+坏版本+sup缺target+sup悬挂
    assert report["ok"] is False  # 有 error
    # 报告绝不含正文
    assert SECRET_BODY not in json.dumps(report, ensure_ascii=False)


def test_warnings_alone_keep_ok_true(tmp_path):
    contract = _mod("brain_library.contract")
    brain = tmp_path / "brain"
    (brain / "knowledge").mkdir(parents=True)
    (brain / "knowledge" / "legacy.md").write_text(
        "# 存量\n无frontmatter的存量文档,只有warning,ok必须保持True。\n",
        encoding="utf-8",
    )
    report = contract.scan_contract(brain)
    assert report["issues"]["error"] == 0
    assert report["issues"]["warning"] > 0
    assert report["ok"] is True


def test_path_prefix_filter(tmp_path):
    contract = _mod("brain_library.contract")
    brain = _make_brain(tmp_path)
    report = contract.scan_contract(brain, path_prefix="knowledge/other")
    assert report["files_scanned"] == 1
    assert report["by_code"]["nonstandard_status"]["count"] == 1


def test_index_drift_both_directions(tmp_path):
    contract = _mod("brain_library.contract")
    brain = _make_brain(tmp_path)
    index = tmp_path / "idx.sqlite"
    conn = sqlite3.connect(index)
    conn.execute("CREATE TABLE documents (path TEXT PRIMARY KEY)")
    # 索引里有 good.md + 一个磁盘上已删除的文件;缺其余磁盘文件
    conn.execute("INSERT INTO documents VALUES ('knowledge/ustc/good.md')")
    conn.execute("INSERT INTO documents VALUES ('knowledge/ustc/deleted.md')")
    conn.commit()
    conn.close()
    drift = contract.check_index_drift(brain, index)
    assert drift["ok"] is False
    assert drift["missing_on_disk"]["count"] == 1
    assert "knowledge/ustc/deleted.md" in drift["missing_on_disk"]["sample"]
    assert drift["missing_in_index"]["count"] == 6
    # drift 计入总报告 ok
    report = contract.contract_report(brain, index=index)
    assert report["index_drift"]["ok"] is False
    assert report["ok"] is False


def test_cli_contract_subcommand(tmp_path):
    brain = _make_brain(tmp_path)
    proc = subprocess.run(
        [sys.executable, "-m", "brain_library", "contract", str(brain)],
        capture_output=True,
        text=True,
        env={"PYTHONPATH": str(SRC), "PATH": "/usr/bin:/bin"},
    )
    assert proc.returncode == 1  # 有 error → 非零(夜巡可当门用)
    data = json.loads(proc.stdout)
    assert data["files_scanned"] == 7
    assert SECRET_BODY not in proc.stdout
