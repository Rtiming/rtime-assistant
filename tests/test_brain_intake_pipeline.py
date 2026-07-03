# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = REPO_ROOT / "scripts" / "brain-intake"
sys.path.insert(0, str(SCRIPT_DIR))

import intake_common as ic  # noqa: E402
import m1_registry  # noqa: E402
import m3_frontmatter  # noqa: E402
import m4_link  # noqa: E402
import intake_ticket  # noqa: E402
import m4_zotero_bibtex_reset_plan as m4_bibtex  # noqa: E402
import m4_zotero_linked_file_webapi as m4_webapi  # noqa: E402


def make_roots(tmp_path):
    brain = tmp_path / "brain"
    vault = tmp_path / "vault"
    (brain / "_indexes").mkdir(parents=True)
    (brain / "_meta").mkdir(parents=True)
    (brain / "knowledge" / "courses" / "thermal-statistical-physics" / "lectures").mkdir(parents=True)
    (brain / "knowledge" / "courses" / "thermal-statistical-physics" / "md" / "lectures").mkdir(parents=True)
    (brain / "knowledge" / "interests").mkdir(parents=True)
    (vault / "课程" / "热力学与统计物理资料" / "PDF" / "讲义").mkdir(parents=True)
    (vault / "课程" / "热力学与统计物理资料" / "讲义").mkdir(parents=True)
    return brain, vault


def make_zotero_run04_db(path: Path, brain_path: str, sha256: str) -> Path:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE collections(collectionID INTEGER PRIMARY KEY, key TEXT, collectionName TEXT);
        CREATE TABLE collectionItems(collectionID INTEGER, itemID INTEGER);
        CREATE TABLE items(itemID INTEGER PRIMARY KEY, key TEXT);
        CREATE TABLE fields(fieldID INTEGER PRIMARY KEY, fieldName TEXT);
        CREATE TABLE itemData(itemID INTEGER, fieldID INTEGER, valueID INTEGER);
        CREATE TABLE itemDataValues(valueID INTEGER PRIMARY KEY, value TEXT);
        CREATE TABLE itemNotes(parentItemID INTEGER, note TEXT);
        CREATE TABLE itemAttachments(itemID INTEGER, parentItemID INTEGER, path TEXT);
        CREATE TABLE users(userID INTEGER, name TEXT);
        CREATE TABLE libraries(libraryID INTEGER, type TEXT, editable INTEGER, filesEditable INTEGER, version INTEGER);
        """
    )
    conn.execute("INSERT INTO users VALUES (19256448, 'lrtime')")
    conn.execute("INSERT INTO libraries VALUES (1, 'user', 1, 1, 360)")
    conn.execute("INSERT INTO collections VALUES (1, 'RUN04COL', ?)", ("run-04导入",))
    conn.execute("INSERT INTO items VALUES (10, 'PARENT01')")
    conn.execute("INSERT INTO collectionItems VALUES (1, 10)")
    conn.execute("INSERT INTO fields VALUES (1, 'citationKey')")
    conn.execute("INSERT INTO itemDataValues VALUES (1, 'test2026')")
    conn.execute("INSERT INTO itemData VALUES (10, 1, 1)")
    conn.execute(
        "INSERT INTO itemNotes VALUES (10, ?)",
        (f"<p>brain_path: {brain_path}<br/>sha256: {sha256}</p>",),
    )
    conn.commit()
    conn.close()
    return path


def zotero_profile_ok():
    return {
        "expected_brain_root": "/tmp/brain",
        "profiles_root": "/tmp/zotero-profiles",
        "profiles": [
            {
                "prefs": "/tmp/zotero-profiles/test.default/prefs.js",
                "baseAttachmentPath": "/tmp/brain",
                "betterBibTeX_baseAttachmentPath": "/tmp/brain",
                "base_matches_brain_root": True,
                "betterBibTeX_base_matches_brain_root": True,
            }
        ],
        "matching_profile_count": 1,
        "ok": True,
    }


def make_m4_link_targets(brain: Path) -> None:
    for item in m4_link.VIEW_LINKS:
        for rel in m4_link._entry_brain_rels(item):
            target = brain / rel
            target.mkdir(parents=True)
            if item.get("mode") == "materialize":
                suffix = "md" if item["vault_rel"].endswith("/文稿") else "pdf"
                name = f"sample-{target.name}.{suffix}"
                (target / name).write_bytes(b"pdf")


def make_rename_fixture(tmp_path):
    brain, vault = make_roots(tmp_path)
    slides = brain / "knowledge" / "courses" / "solid-state-physics" / "slides"
    (slides / "images" / "slide (1)").mkdir(parents=True)
    (slides / "text" / "slide (1)").mkdir(parents=True)
    (slides / "slide (1).pdf").write_bytes(b"pdf")
    (slides / "slide (1).md").write_text(
        "\n".join(
            [
                "---",
                'title: "slide (1)"',
                'source: "knowledge/courses/solid-state-physics/slides/slide (1).pdf"',
                'pdf_file: "slide (1).pdf"',
                'page_image_dir: "images/slide (1)"',
                "---",
                "# slide (1)",
                "普通正文 slide (1) 不应替换",
                "- PDF原件：[[slide (1).pdf|slide (1).pdf]]",
                "![](<images/slide (1)/page-01.png>)",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return brain, vault, slides


def test_manifest_plan_adds_missing_pdf(tmp_path):
    brain, vault = make_roots(tmp_path)
    pdf = brain / "knowledge" / "courses" / "thermal-statistical-physics" / "lectures" / "ch01.pdf"
    pdf.write_bytes(b"pdf-a")
    plan = m1_registry.build_plan(brain, vault, tmp_path / "run")
    assert any(a["action"] == "manifest_add" and a["brain_path"].endswith("ch01.pdf") for a in plan["actions"])


def test_intake_ticket_plans_sensitive_site_compliance(tmp_path):
    source = tmp_path / "公安备案申请指引_王韬智_20260612.pdf"
    source.write_bytes(b"fake pdf")
    args = type("Args", (), {
        "brain_root": tmp_path / "brain",
        "inbox_root": tmp_path / "brain" / "_inbox",
        "run_id": "run-12",
        "source": "obsidian",
        "file": [str(source)],
        "requested_action": "inbox",
        "target_hint": "",
        "privacy_hint": "",
        "received_at": "2026-06-12T15:44:00+0800",
    })()

    plan = intake_ticket.build_plan(args)

    ticket = plan["tickets"][0]
    assert ticket["class"] == "operations-compliance"
    assert ticket["privacy_hint"] == "personal"
    assert ticket["decision"] == "hold-sensitive-review"
    assert "personal-data" not in ticket["inbox_path"]  # inbox only, not final filing
    assert ticket["redaction"]["ticket_contains_body"] is False


def test_intake_ticket_apply_requires_approval(tmp_path):
    source = tmp_path / "rtime.site.jpg"
    source.write_bytes(b"fake jpg")
    args = type("Args", (), {
        "brain_root": tmp_path / "brain",
        "inbox_root": tmp_path / "brain" / "_inbox",
        "run_id": "run-12",
        "source": "cli",
        "file": [str(source)],
        "requested_action": "inbox",
        "target_hint": "",
        "privacy_hint": "",
        "received_at": "2026-06-12T15:44:00+0800",
    })()
    plan = intake_ticket.build_plan(args)

    try:
        intake_ticket.apply_plan(plan, approved=False)
    except ValueError as exc:
        assert "--approved-plan" in str(exc)
    else:
        raise AssertionError("expected approval refusal")


def test_intake_ticket_apply_copies_only_to_inbox_and_reports(tmp_path):
    source = tmp_path / "lesson1.pdf"
    source.write_bytes(b"course pdf")
    args = type("Args", (), {
        "brain_root": tmp_path / "brain",
        "inbox_root": tmp_path / "brain" / "_inbox",
        "run_id": "run-12",
        "source": "webdav-upload",
        "file": [str(source)],
        "requested_action": "inbox",
        "target_hint": "course/solid-state-physics",
        "privacy_hint": "normal",
        "received_at": "2026-06-12T15:44:00+0800",
    })()
    plan = intake_ticket.build_plan(args)
    result = intake_ticket.apply_plan(plan, approved=True)
    inbox_path = Path(result["copied"][0]["inbox_path"])
    ticket_path = Path(result["copied"][0]["ticket_path"])

    assert inbox_path.exists()
    assert ticket_path.exists()
    assert "_inbox" in inbox_path.parts
    ticket = json.loads(ticket_path.read_text(encoding="utf-8"))
    assert ticket["status"] == "inbox"
    report = intake_ticket.markdown_report(plan, result)
    assert "不写最终 `knowledge/`" in report


def test_m4_link_plans_cross_client_view_entries(tmp_path):
    brain = tmp_path / "brain"
    vault = tmp_path / "brain-notes"
    make_m4_link_targets(brain)

    plan = m4_link.build_plan(brain, vault, tmp_path / "run")

    symlinks = [action for action in plan["actions"] if action["action"] == "symlink"]
    materialized = [action for action in plan["actions"] if action["action"] == "materialize"]
    stignore = [action for action in plan["actions"] if action["action"] == "stignore_rewrite"]
    assert len(symlinks) == 1
    assert len(materialized) == len([item for item in m4_link.VIEW_LINKS if item.get("mode") == "materialize"])
    assert all(action["counts"]["copy"] >= 1 for action in materialized)
    assert stignore
    assert "/01 每日" in stignore[0]["ignored_paths"]
    assert "/10 课程/2026春/固体物理/课件" not in stignore[0]["ignored_paths"]
    assert "/10 课程/2026春/热力学与统计物理/讲义" not in stignore[0]["ignored_paths"]


def test_m4_link_apply_creates_local_view_entries(tmp_path):
    if os.name == "nt":
        return
    brain = tmp_path / "brain"
    vault = tmp_path / "brain-notes"
    make_m4_link_targets(brain)

    plan = m4_link.build_plan(brain, vault, tmp_path / "run")
    result = m4_link.apply_plan(plan, vault, tmp_path / "run")

    expected_done = 1 + len([item for item in m4_link.VIEW_LINKS if item.get("mode") == "materialize"]) + 1
    assert result["summary"]["done"] == expected_done
    daily = vault / "01 每日"
    assert daily.is_symlink()
    assert Path(os.readlink(daily)) == brain / "notes" / "daily"
    course_view = vault / "10 课程/2026春/固体物理/课件"
    assert course_view.is_dir()
    assert not course_view.is_symlink()
    assert (course_view / "sample-slides.pdf").read_bytes() == b"pdf"
    stignore = (vault / ".stignore").read_text(encoding="utf-8")
    assert m4_link.STIGNORE_BEGIN in stignore
    assert "/01 每日" in stignore
    assert "/10 课程/2026春/热力学与统计物理/讲义" not in stignore


def test_m4_link_verify_passes_after_materialized_views_are_current(tmp_path):
    if os.name == "nt":
        return
    brain = tmp_path / "brain"
    vault = tmp_path / "brain-notes"
    make_m4_link_targets(brain)

    plan = m4_link.build_plan(brain, vault, tmp_path / "run")
    m4_link.apply_plan(plan, vault, tmp_path / "run")

    result = m4_link.verify_views(brain, vault, tmp_path / "verify", m4_link.default_manifest())

    assert result["ok"] is True
    assert result["summary"]["errors"] == 0
    assert result["summary"]["pending_actions"] == 0


def test_m4_link_verify_rejects_stignored_syncable_course_view(tmp_path):
    if os.name == "nt":
        return
    brain = tmp_path / "brain"
    vault = tmp_path / "brain-notes"
    make_m4_link_targets(brain)

    plan = m4_link.build_plan(brain, vault, tmp_path / "run")
    m4_link.apply_plan(plan, vault, tmp_path / "run")
    (vault / ".stignore").write_text(
        "\n".join(
            [
                m4_link.STIGNORE_BEGIN,
                "// Entries below are local-only views, usually symlinks into brain.",
                "/01 每日",
                "/10 课程/2026春/固体物理/课件",
                m4_link.STIGNORE_END,
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = m4_link.verify_views(brain, vault, tmp_path / "verify", m4_link.default_manifest())

    assert result["ok"] is False
    assert any("incorrectly ignored" in issue["message"] for issue in result["errors"])


def test_m4_link_materialize_replaces_broken_course_symlink(tmp_path):
    brain = tmp_path / "brain"
    vault = tmp_path / "brain-notes"
    source = brain / "knowledge/courses/solid-state-physics/slides"
    source.mkdir(parents=True)
    (source / "1 绪论.pdf").write_bytes(b"course pdf")
    broken = vault / "10 课程/2026春/固体物理/课件"
    broken.parent.mkdir(parents=True)
    broken.symlink_to(tmp_path / "missing-target", target_is_directory=True)

    manifest = {
        "schema_version": m4_link.VIEW_MANIFEST_SCHEMA,
        "entries": [
            {
                "vault_rel": "10 课程/2026春/固体物理/课件",
                "brain_rel": "knowledge/courses/solid-state-physics/slides",
                "mode": "materialize",
                "include_globs": ["*.pdf"],
                "exclude_globs": [],
                "prune": True,
            }
        ],
    }
    plan = m4_link.build_plan(brain, vault, tmp_path / "run", manifest=manifest)
    assert plan["actions"][0]["replace_symlink"] is True

    result = m4_link.apply_plan(plan, vault, tmp_path / "run")

    assert result["summary"]["done"] == 1
    assert not broken.is_symlink()
    assert (broken / "1 绪论.pdf").read_bytes() == b"course pdf"


def test_m4_link_materialize_merges_legacy_homework_and_exercises(tmp_path):
    brain = tmp_path / "brain"
    vault = tmp_path / "brain-notes"
    course = brain / "knowledge/courses/controlled-fusion-introduction"
    (course / "exercises").mkdir(parents=True)
    (course / "homework").mkdir(parents=True)
    (course / "exercises" / "磁约束作业（2026-05-12）.pdf").write_bytes(b"new exercise")
    (course / "homework" / "聚变作业总.pdf").write_bytes(b"legacy homework")

    manifest = {
        "schema_version": m4_link.VIEW_MANIFEST_SCHEMA,
        "entries": [
            {
                "vault_rel": "10 课程/2026春/受控热核聚变导论/习题",
                "brain_rels": [
                    "knowledge/courses/controlled-fusion-introduction/exercises",
                    "knowledge/courses/controlled-fusion-introduction/homework",
                ],
                "mode": "materialize",
                "include_globs": ["*.pdf"],
                "exclude_globs": [],
                "prune": True,
            }
        ],
    }
    plan = m4_link.build_plan(brain, vault, tmp_path / "run", manifest=manifest)
    result = m4_link.apply_plan(plan, vault, tmp_path / "run")

    assert result["summary"]["done"] == 1
    view = vault / "10 课程/2026春/受控热核聚变导论/习题"
    # The single 习题 vault folder now shows both the new exercise and the legacy homework.
    assert (view / "磁约束作业（2026-05-12）.pdf").read_bytes() == b"new exercise"
    assert (view / "聚变作业总.pdf").read_bytes() == b"legacy homework"

    verify = m4_link.verify_views(brain, vault, tmp_path / "verify", manifest)
    assert verify["ok"] is True


def test_m4_link_absent_entry_backs_up_retired_view_path(tmp_path):
    brain = tmp_path / "brain"
    vault = tmp_path / "brain-notes"
    retired = vault / "10 课程/2026春/先进光子物理/讲义"
    retired.mkdir(parents=True)
    (retired / "old.pdf").write_bytes(b"old")
    manifest = {
        "schema_version": m4_link.VIEW_MANIFEST_SCHEMA,
        "entries": [
            {
                "vault_rel": "10 课程/2026春/先进光子物理/讲义",
                "mode": "absent",
                "reason": "retired test view",
            }
        ],
    }

    plan = m4_link.build_plan(brain, vault, tmp_path / "run", manifest=manifest)
    assert plan["actions"][0]["action"] == "remove_view_path"

    result = m4_link.apply_plan(plan, vault, tmp_path / "run")

    assert result["summary"]["done"] == 1
    assert not retired.exists()
    backup = tmp_path / "run" / "backups" / "removed-view-paths" / "10 课程/2026春/先进光子物理/讲义" / "old.pdf"
    assert backup.read_bytes() == b"old"


def _intake_plan(tmp_path, name="公安备案申请指引_王韬智_20260612.pdf", privacy=""):
    source = tmp_path / name
    source.write_bytes(b"fake body")
    args = type("Args", (), {
        "brain_root": tmp_path / "brain",
        "inbox_root": tmp_path / "brain" / "_inbox",
        "run_id": "run-16-test",
        "source": "cli",
        "file": [str(source)],
        "requested_action": "inbox",
        "target_hint": "",
        "privacy_hint": privacy,
        "received_at": "2026-06-12T23:59:00+0800",
    })()
    return intake_ticket.build_plan(args)


def test_intake_notify_confirm_message_names_files_and_candidates(tmp_path):
    plan = _intake_plan(tmp_path)
    message = intake_ticket.build_notify_message(plan, kind="confirm")
    assert "入库待确认" in message
    assert "公安备案申请指引_王韬智_20260612.pdf" in message
    assert "personal-data/operations/rtime-site/compliance/" in message
    assert "批准入库" in message
    assert "fake body" not in message  # never file bodies

    cmd = intake_ticket.notify_register_args(message, register_cmd="fake-register", due="", target="ou_x")
    assert cmd[0] == "fake-register"
    assert cmd[1:5] == ["add", "--mode", "notify", "--due"]
    assert cmd[5]  # default due filled with a real timestamp
    assert cmd[-2:] == ["--target", "ou_x"]
    assert message in cmd


def test_intake_finalize_moves_inbox_file_and_updates_ticket(tmp_path):
    plan = _intake_plan(tmp_path)
    result = intake_ticket.apply_plan(plan, approved=True)
    ticket_path = Path(result["copied"][0]["ticket_path"])
    dest_dir = tmp_path / "brain" / "personal-data" / "operations" / "rtime-site" / "compliance"

    try:
        intake_ticket.finalize_ticket(ticket_path, dest_dir, approved=False)
    except ValueError as exc:
        assert "--approved" in str(exc)
    else:
        raise AssertionError("expected approval refusal")

    filed = intake_ticket.finalize_ticket(ticket_path, dest_dir, approved=True)
    final_path = Path(filed["final_path"])
    assert final_path.is_file()
    assert not Path(result["copied"][0]["inbox_path"]).exists()  # moved, not duplicated
    ticket = json.loads(ticket_path.read_text(encoding="utf-8"))
    assert ticket["status"] == "filed"
    assert ticket["final_path"] == str(final_path)

    done = intake_ticket.build_notify_message(plan, kind="done")
    assert "入库完成" in done
    assert str(final_path) in done  # done message reads the updated ticket


def test_intake_finalize_rejects_destinations_outside_brain(tmp_path):
    plan = _intake_plan(tmp_path)
    result = intake_ticket.apply_plan(plan, approved=True)
    ticket_path = Path(result["copied"][0]["ticket_path"])

    for bad in (tmp_path / "elsewhere", tmp_path / "brain" / "_inbox" / "nested"):
        try:
            intake_ticket.finalize_ticket(ticket_path, bad, approved=True)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected rejection for {bad}")


def test_apply_requires_approved_plan(tmp_path):
    proc = subprocess.run(
        [sys.executable, str(SCRIPT_DIR / "m1_registry.py"), "--apply", "--brain-root", str(tmp_path / "b"), "--vault-root", str(tmp_path / "v"), "--run-dir", str(tmp_path / "r")],
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0
    assert "--approved-plan" in proc.stderr


def test_zotero_linked_file_webapi_plan_uses_run04_holds(tmp_path):
    brain, _vault = make_roots(tmp_path)
    paper = brain / "knowledge" / "research" / "demo" / "papers" / "paper.pdf"
    paper.parent.mkdir(parents=True)
    paper.write_bytes(b"pdf")
    sha = ic.sha256_file(paper)
    brain_path = ic.rel_to(brain, paper)
    db = make_zotero_run04_db(tmp_path / "zotero.sqlite", brain_path, sha)
    run_dir = tmp_path / "run-04"
    run_dir.mkdir()
    reconcile = {
        "zotero_db_copy": str(db),
        "holds": [{"brain_path": brain_path, "sha256": sha, "reason": "missing linked attachment"}],
    }
    (run_dir / "M4-zotero-reconcile-log.json").write_text(json.dumps(reconcile), encoding="utf-8")

    plan = m4_webapi.build_plan(brain, run_dir)

    assert plan["zotero_library"]["user_id"] == 19256448
    assert plan["summary"]["planned_linked_file_attachments"] == 1
    action = plan["actions"][0]
    assert action["zotero_item_key"] == "PARENT01"
    assert action["payload"]["linkMode"] == "linked_file"
    assert action["payload"]["path"] == f"attachments:{brain_path}"
    assert action["payload"]["parentItem"] == "PARENT01"
    assert "filename" not in action["payload"]


def test_zotero_linked_file_webapi_detects_base_attachment_path(tmp_path):
    brain = tmp_path / "brain"
    profile = tmp_path / "profiles" / "test.default"
    profile.mkdir(parents=True)
    (profile / "prefs.js").write_text(
        "\n".join(
            [
                f'user_pref("extensions.zotero.baseAttachmentPath", "{brain}");',
                f'user_pref("extensions.zotero.translators.better-bibtex.baseAttachmentPath", "{brain}");',
                'user_pref("extensions.zotero.dataDir", "/tmp/Zotero");',
            ]
        ),
        encoding="utf-8",
    )

    detected = m4_webapi._detect_zotero_profile_paths(brain, tmp_path / "profiles")

    assert detected["ok"] is True
    assert detected["matching_profile_count"] == 1
    assert detected["profiles"][0]["base_matches_brain_root"] is True


def test_zotero_linked_file_webapi_apply_requires_credentials(monkeypatch):
    for key in ["ZOTERO_API_KEY", "ZOTERO_LIBRARY_ID", "ZOTERO_USER_ID"]:
        monkeypatch.delenv(key, raising=False)
    try:
        m4_webapi.apply_plan({"actions": []})
    except ValueError as exc:
        assert "ZOTERO_API_KEY" in str(exc)
    else:
        raise AssertionError("expected missing credential refusal")


def test_zotero_linked_file_webapi_apply_uses_detected_user_id(monkeypatch):
    captured = {}

    def fake_existing(api_base, api_key, library_id, library_type, action):
        return None

    def fake_post_items(api_base, api_key, library_id, library_type, items):
        captured.update(
            {
                "api_base": api_base,
                "api_key": api_key,
                "library_id": library_id,
                "library_type": library_type,
                "items": items,
            }
        )
        return {"status": 200, "body": {"successful": {"0": {"key": "ATTACH01", "version": 1}}}}

    monkeypatch.setenv("ZOTERO_API_KEY", "test-key")
    monkeypatch.delenv("ZOTERO_LIBRARY_ID", raising=False)
    monkeypatch.delenv("ZOTERO_USER_ID", raising=False)
    monkeypatch.setattr(m4_webapi, "_get_existing_linked_attachment", fake_existing)
    monkeypatch.setattr(m4_webapi, "_post_items", fake_post_items)
    plan = {
        "run_id": "run-04",
        "method": "zotero-web-api-linked-file",
        "zotero_library": {"type": "user", "user_id": 19256448},
        "zotero_profile": zotero_profile_ok(),
        "summary": {"planned_linked_file_attachments": 1, "plan_holds": 0},
        "holds": [],
        "actions": [
            {
                "action": "webapi_create_linked_file_attachment",
                "brain_path": "knowledge/research/demo/papers/paper.pdf",
                "zotero_item_key": "PARENT01",
                "payload": {
                    "itemType": "attachment",
                    "linkMode": "linked_file",
                    "parentItem": "PARENT01",
                    "path": "attachments:knowledge/research/demo/papers/paper.pdf",
                    "contentType": "application/pdf",
                },
                "rollback": {"does_not_upload_or_delete_pdf": True},
            }
        ],
    }

    result = m4_webapi.apply_plan(plan)

    assert result["ok"] is True
    assert captured["library_id"] == "19256448"
    assert captured["library_type"] == "user"


def test_zotero_linked_file_webapi_apply_rejects_unsafe_plan(monkeypatch):
    monkeypatch.setenv("ZOTERO_API_KEY", "test-key")
    plan = {
        "run_id": "run-04",
        "method": "zotero-web-api-linked-file",
        "zotero_library": {"type": "user", "user_id": 19256448},
        "zotero_profile": zotero_profile_ok(),
        "summary": {"planned_linked_file_attachments": 1, "plan_holds": 0},
        "holds": [],
        "actions": [
            {
                "action": "webapi_create_linked_file_attachment",
                "brain_path": "knowledge/courses/demo/papers/paper.pdf",
                "zotero_item_key": "PARENT01",
                "payload": {
                    "itemType": "attachment",
                    "linkMode": "linked_file",
                    "parentItem": "PARENT01",
                    "path": "attachments:knowledge/courses/demo/papers/paper.pdf",
                    "contentType": "application/pdf",
                },
                "rollback": {"does_not_upload_or_delete_pdf": True},
            }
        ],
    }

    try:
        m4_webapi.apply_plan(plan)
    except ValueError as exc:
        assert "unsafe Zotero Web API plan" in str(exc)
        assert "brain_path_out_of_scope" in str(exc)
    else:
        raise AssertionError("expected unsafe plan refusal")


def test_zotero_linked_file_webapi_apply_rejects_base_path_mismatch(monkeypatch):
    monkeypatch.setenv("ZOTERO_API_KEY", "test-key")
    plan = {
        "run_id": "run-04",
        "method": "zotero-web-api-linked-file",
        "zotero_library": {"type": "user", "user_id": 19256448},
        "zotero_profile": {"ok": False, "expected_brain_root": "/tmp/brain", "profiles": []},
        "summary": {"planned_linked_file_attachments": 1, "plan_holds": 0},
        "holds": [],
        "actions": [
            {
                "action": "webapi_create_linked_file_attachment",
                "brain_path": "knowledge/research/demo/papers/paper.pdf",
                "zotero_item_key": "PARENT01",
                "payload": {
                    "itemType": "attachment",
                    "linkMode": "linked_file",
                    "parentItem": "PARENT01",
                    "path": "attachments:knowledge/research/demo/papers/paper.pdf",
                    "contentType": "application/pdf",
                },
                "rollback": {"does_not_upload_or_delete_pdf": True},
            }
        ],
    }

    try:
        m4_webapi.apply_plan(plan)
    except ValueError as exc:
        assert "zotero_baseAttachmentPath_must_match_brain_root" in str(exc)
    else:
        raise AssertionError("expected base path mismatch refusal")


def test_zotero_linked_file_webapi_preflight_without_credentials(monkeypatch):
    for key in ["ZOTERO_API_KEY", "ZOTERO_LIBRARY_ID", "ZOTERO_USER_ID"]:
        monkeypatch.delenv(key, raising=False)
    plan = {
        "run_id": "run-04",
        "method": "zotero-web-api-linked-file",
        "zotero_library": {"type": "user", "user_id": 19256448},
        "zotero_profile": zotero_profile_ok(),
        "summary": {"planned_linked_file_attachments": 1, "plan_holds": 0},
        "holds": [],
        "actions": [
            {
                "action": "webapi_create_linked_file_attachment",
                "brain_path": "knowledge/research/demo/papers/paper.pdf",
                "zotero_item_key": "PARENT01",
                "payload": {
                    "itemType": "attachment",
                    "linkMode": "linked_file",
                    "parentItem": "PARENT01",
                    "path": "attachments:knowledge/research/demo/papers/paper.pdf",
                    "contentType": "application/pdf",
                },
                "rollback": {"does_not_upload_or_delete_pdf": True},
            }
        ],
    }

    result = m4_webapi.preflight_plan(plan, require_credentials=False)

    assert result["ok"] is True
    assert result["summary"]["credential_checked"] is False
    assert "ZOTERO_API_KEY" in result["credential_error"]


def test_zotero_linked_file_webapi_preflight_with_credentials(monkeypatch):
    seen = []

    def fake_existing(api_base, api_key, library_id, library_type, action):
        seen.append(action["brain_path"])
        if action["brain_path"].endswith("existing.pdf"):
            return {
                "brain_path": action["brain_path"],
                "zotero_parent_item_key": action["zotero_item_key"],
                "zotero_attachment_item_key": "ATTACH01",
                "path": action["payload"]["path"],
            }
        return None

    monkeypatch.setenv("ZOTERO_API_KEY", "test-key")
    monkeypatch.setattr(m4_webapi, "_get_existing_linked_attachment", fake_existing)
    plan = {
        "run_id": "run-04",
        "method": "zotero-web-api-linked-file",
        "zotero_library": {"type": "user", "user_id": 19256448},
        "zotero_profile": zotero_profile_ok(),
        "summary": {"planned_linked_file_attachments": 2, "plan_holds": 0},
        "holds": [],
        "actions": [
            {
                "action": "webapi_create_linked_file_attachment",
                "brain_path": "knowledge/research/demo/papers/existing.pdf",
                "zotero_item_key": "PARENT01",
                "payload": {
                    "itemType": "attachment",
                    "linkMode": "linked_file",
                    "parentItem": "PARENT01",
                    "path": "attachments:knowledge/research/demo/papers/existing.pdf",
                    "contentType": "application/pdf",
                },
                "rollback": {"does_not_upload_or_delete_pdf": True},
            },
            {
                "action": "webapi_create_linked_file_attachment",
                "brain_path": "knowledge/research/demo/papers/new.pdf",
                "zotero_item_key": "PARENT02",
                "payload": {
                    "itemType": "attachment",
                    "linkMode": "linked_file",
                    "parentItem": "PARENT02",
                    "path": "attachments:knowledge/research/demo/papers/new.pdf",
                    "contentType": "application/pdf",
                },
                "rollback": {"does_not_upload_or_delete_pdf": True},
            },
        ],
    }

    result = m4_webapi.preflight_plan(plan)

    assert result["ok"] is True
    assert result["summary"]["credential_checked"] is True
    assert result["summary"]["already_linked"] == 1
    assert result["summary"]["to_create"] == 1
    assert seen == ["knowledge/research/demo/papers/existing.pdf", "knowledge/research/demo/papers/new.pdf"]


def test_zotero_linked_file_webapi_apply_skips_existing(monkeypatch):
    posted = []

    def fake_existing(api_base, api_key, library_id, library_type, action):
        return {
            "brain_path": action["brain_path"],
            "zotero_parent_item_key": action["zotero_item_key"],
            "zotero_attachment_item_key": "ATTACH01",
            "path": action["payload"]["path"],
        }

    def fake_post_items(api_base, api_key, library_id, library_type, items):
        posted.extend(items)
        return {"status": 200, "body": {"successful": {}}}

    monkeypatch.setenv("ZOTERO_API_KEY", "test-key")
    monkeypatch.delenv("ZOTERO_LIBRARY_ID", raising=False)
    monkeypatch.delenv("ZOTERO_USER_ID", raising=False)
    monkeypatch.setattr(m4_webapi, "_get_existing_linked_attachment", fake_existing)
    monkeypatch.setattr(m4_webapi, "_post_items", fake_post_items)
    plan = {
        "run_id": "run-04",
        "method": "zotero-web-api-linked-file",
        "zotero_library": {"type": "user", "user_id": 19256448},
        "zotero_profile": zotero_profile_ok(),
        "summary": {"planned_linked_file_attachments": 1, "plan_holds": 0},
        "holds": [],
        "actions": [
            {
                "action": "webapi_create_linked_file_attachment",
                "brain_path": "knowledge/research/demo/papers/paper.pdf",
                "zotero_item_key": "PARENT01",
                "payload": {
                    "itemType": "attachment",
                    "parentItem": "PARENT01",
                    "path": "attachments:knowledge/research/demo/papers/paper.pdf",
                    "linkMode": "linked_file",
                    "contentType": "application/pdf",
                },
                "rollback": {"does_not_upload_or_delete_pdf": True},
            }
        ],
    }

    result = m4_webapi.apply_plan(plan)

    assert result["ok"] is True
    assert result["summary"]["created"] == 0
    assert result["summary"]["skipped_existing"] == 1
    assert result["skipped_existing"][0]["zotero_attachment_item_key"] == "ATTACH01"
    assert posted == []


def test_zotero_linked_file_webapi_apply_respects_max_create(monkeypatch):
    posted = []

    def fake_existing(api_base, api_key, library_id, library_type, action):
        return None

    def fake_post_items(api_base, api_key, library_id, library_type, items):
        posted.extend(items)
        return {"status": 200, "body": {"successful": {"0": {"key": "ATTACH01", "version": 1}}}}

    monkeypatch.setenv("ZOTERO_API_KEY", "test-key")
    monkeypatch.setattr(m4_webapi, "_get_existing_linked_attachment", fake_existing)
    monkeypatch.setattr(m4_webapi, "_post_items", fake_post_items)

    def action(name, parent):
        return {
            "action": "webapi_create_linked_file_attachment",
            "brain_path": f"knowledge/research/demo/papers/{name}.pdf",
            "zotero_item_key": parent,
            "payload": {
                "itemType": "attachment",
                "linkMode": "linked_file",
                "parentItem": parent,
                "path": f"attachments:knowledge/research/demo/papers/{name}.pdf",
                "contentType": "application/pdf",
            },
            "rollback": {"does_not_upload_or_delete_pdf": True},
        }

    plan = {
        "run_id": "run-04",
        "method": "zotero-web-api-linked-file",
        "zotero_library": {"type": "user", "user_id": 19256448},
        "zotero_profile": zotero_profile_ok(),
        "summary": {"planned_linked_file_attachments": 2, "plan_holds": 0},
        "holds": [],
        "actions": [action("first", "PARENT01"), action("second", "PARENT02")],
    }

    result = m4_webapi.apply_plan(plan, max_create=1)

    assert result["ok"] is True
    assert result["complete"] is False
    assert result["summary"]["created"] == 1
    assert result["summary"]["deferred"] == 1
    assert result["summary"]["writes_limited"] is True
    assert result["deferred"][0]["brain_path"].endswith("second.pdf")
    assert len(posted) == 1


def test_zotero_linked_file_webapi_rollback_deletes_created_only(monkeypatch):
    deleted = []

    def fake_delete_item(api_base, api_key, library_id, library_type, item_key, version):
        deleted.append((library_id, library_type, item_key, version))
        return {"status": 204, "last_modified_version": "361", "body": ""}

    monkeypatch.setenv("ZOTERO_API_KEY", "test-key")
    monkeypatch.delenv("ZOTERO_LIBRARY_ID", raising=False)
    monkeypatch.delenv("ZOTERO_USER_ID", raising=False)
    monkeypatch.setattr(m4_webapi, "_delete_item", fake_delete_item)
    plan = {"zotero_library": {"type": "user", "user_id": 19256448}}
    apply_log = {
        "created": [
            {
                "brain_path": "knowledge/research/demo/papers/paper.pdf",
                "zotero_parent_item_key": "PARENT01",
                "zotero_attachment_item_key": "ATTACH01",
                "version": 360,
            }
        ],
        "skipped_existing": [
            {
                "brain_path": "knowledge/research/demo/papers/existing.pdf",
                "zotero_parent_item_key": "PARENT02",
                "zotero_attachment_item_key": "ATTACH02",
            }
        ],
    }

    result = m4_webapi.rollback_apply_log(apply_log, plan)

    assert result["ok"] is True
    assert result["summary"]["deleted"] == 1
    assert result["summary"]["skipped_existing"] == 1
    assert result["skipped_existing"][0]["reason"] == "not_created_by_apply_log"
    assert deleted == [("19256448", "user", "ATTACH01", 360)]


def test_zotero_bibtex_reset_package_is_plan_only(tmp_path):
    brain, _vault = make_roots(tmp_path)
    paper = brain / "knowledge" / "research" / "demo" / "papers" / "paper.pdf"
    paper.parent.mkdir(parents=True)
    paper.write_bytes(b"pdf")
    sha = ic.sha256_file(paper)
    brain_path = ic.rel_to(brain, paper)
    run_dir = tmp_path / "run-04"
    run_dir.mkdir()
    source_plan = {
        "actions": [
            {
                "action": "zotero_import_linked_pdf",
                "brain_path": brain_path,
                "title": "Demo Paper",
                "author": "demo",
                "year": "2026",
                "planned_citekey": "demo2026paper",
            }
        ]
    }
    webapi_plan = {
        "actions": [
            {
                "action": "webapi_create_linked_file_attachment",
                "brain_path": brain_path,
                "sha256": sha,
                "citekey": "demo2026",
                "zotero_item_key": "PARENT01",
                "payload": {
                    "path": f"attachments:{brain_path}",
                    "title": "paper.pdf",
                    "contentType": "application/pdf",
                },
            }
        ]
    }
    source_path = run_dir / "zotero-run04-plan.json"
    webapi_path = run_dir / "zotero-linked-file-webapi-plan.json"
    source_path.write_text(json.dumps(source_plan), encoding="utf-8")
    webapi_path.write_text(json.dumps(webapi_plan), encoding="utf-8")

    plan = m4_bibtex.build_package(brain, run_dir)

    assert plan["apply_allowed"] is False
    assert plan["summary"]["entries"] == 1
    assert plan["summary"]["pdf_missing"] == 0
    bib = (run_dir / "zotero-bibtex-linked-import-reset-only.bib").read_text(encoding="utf-8")
    assert "@article{demo2026," in bib
    assert f"file = {{PDF:attachments:{brain_path}:application/pdf}}" in bib
    csv_text = (run_dir / "zotero-bibtex-linked-import-reset-only.csv").read_text(encoding="utf-8")
    assert "PARENT01" in csv_text


def test_thermal_duplicate_directory_archive_planned(tmp_path):
    brain, vault = make_roots(tmp_path)
    brain_pdf = brain / "knowledge" / "courses" / "thermal-statistical-physics" / "lectures" / "ch01.pdf"
    brain_pdf.write_bytes(b"same")
    vault_pdf = vault / "课程" / "热力学与统计物理资料" / "PDF" / "讲义" / "ch01.pdf"
    vault_pdf.write_bytes(b"same")
    plan = m1_registry.build_plan(brain, vault, tmp_path / "run")
    assert any(a["action"] == "move_vault_duplicate_to_archive" for a in plan["actions"])


def test_frontmatter_preserves_body(tmp_path):
    brain, _vault = make_roots(tmp_path)
    md = brain / "knowledge" / "courses" / "thermal-statistical-physics" / "md" / "lectures" / "ch01.md"
    md.write_text("# 第一章\n\n正文", encoding="utf-8")
    plan = m3_frontmatter.build_plan(brain, tmp_path / "run")
    assert plan["actions"]
    m3_frontmatter.apply_plan(brain, tmp_path / "run", plan)
    text = md.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert "# 第一章\n\n正文" in text


def test_m0_cli_writes_plan(tmp_path):
    brain, vault = make_roots(tmp_path)
    (brain / "_inbox").mkdir()
    (brain / "knowledge" / "courses" / "thermal-statistical-physics" / "lectures" / "ch01.pdf").write_bytes(b"x")
    run_dir = tmp_path / "run"
    proc = subprocess.run(
        [sys.executable, str(SCRIPT_DIR / "m0_triage.py"), "--brain-root", str(brain), "--vault-root", str(vault), "--run-dir", str(run_dir)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    payload = json.loads((run_dir / "triage-plan.json").read_text(encoding="utf-8"))
    assert payload["summary"]["total"] >= 1


def test_rename_group_records_detailed_references(tmp_path):
    brain, vault, _slides = make_rename_fixture(tmp_path)
    plan = m1_registry.build_plan(brain, vault, tmp_path / "run")
    action = next(a for a in plan["actions"] if a["action"] == "rename_group")
    assert len(action["members"]) == 4
    assert action["references"]
    assert all({"path", "line", "shape"} <= set(ref) for ref in action["references"])


def test_rename_group_moves_four_piece_set_and_rewrites_safe_refs(tmp_path):
    brain, vault, slides = make_rename_fixture(tmp_path)
    plan = m1_registry.build_plan(brain, vault, tmp_path / "run")
    action = next(a for a in plan["actions"] if a["action"] == "rename_group")
    result = m1_registry._apply_rename_group(brain, action)
    assert result["moved"] == 4
    assert (slides / "slide.pdf").exists()
    assert (slides / "slide.md").exists()
    assert (slides / "images" / "slide").exists()
    assert (slides / "text" / "slide").exists()
    text = (slides / "slide.md").read_text(encoding="utf-8")
    assert 'pdf_file: "slide.pdf"' in text
    assert "[[slide.pdf|slide.pdf]]" in text
    assert "images/slide/page-01.png" in text
    assert "普通正文 slide (1) 不应替换" in text


def test_rename_group_rolls_back_on_failure(tmp_path, monkeypatch):
    brain, vault, slides = make_rename_fixture(tmp_path)
    plan = m1_registry.build_plan(brain, vault, tmp_path / "run")
    action = next(a for a in plan["actions"] if a["action"] == "rename_group")
    path_type = type(slides)
    original = path_type.rename

    def fake_rename(self, target):
        if self.name == "slide (1)" and self.parent.name == "text":
            raise OSError("simulated failure")
        return original(self, target)

    monkeypatch.setattr(path_type, "rename", fake_rename)
    try:
        m1_registry._apply_rename_group(brain, action)
    except OSError:
        pass
    else:
        raise AssertionError("expected simulated rename failure")
    assert (slides / "images" / "slide (1)").exists()
    assert not (slides / "images" / "slide").exists()
    assert (slides / "slide (1).pdf").exists()


def test_rename_reference_scan_excludes_archive(tmp_path):
    brain, vault, slides = make_rename_fixture(tmp_path)
    archived = brain / "knowledge" / "_archive" / "old.md"
    archived.parent.mkdir(parents=True)
    archived.write_text("[[slide (1).pdf]]\n", encoding="utf-8")
    plan = m1_registry.build_plan(brain, vault, tmp_path / "run")
    action = next(a for a in plan["actions"] if a["action"] == "rename_group")
    assert all("_archive" not in ref["path"] for ref in action["references"])


def test_apply_plan_failed_rename_group_continues(tmp_path, monkeypatch):
    brain, vault, slides = make_rename_fixture(tmp_path)
    plan = m1_registry.build_plan(brain, vault, tmp_path / "run")
    rename = next(a for a in plan["actions"] if a["action"] == "rename_group")
    readme = brain / "knowledge" / "interests" / "README.md"
    if readme.exists():
        readme.unlink()
    apply_plan = {
        "actions": [
            rename,
            {
                "action": "readme_update",
                "target": str(readme),
                "content": "# interests\n",
            },
        ]
    }
    path_type = type(slides)
    original = path_type.rename

    def fake_rename(self, target):
        if self.name == "slide (1)" and self.parent.name == "text":
            raise OSError("simulated failure")
        return original(self, target)

    monkeypatch.setattr(path_type, "rename", fake_rename)
    result = m1_registry.apply_plan(brain, vault, tmp_path / "run", apply_plan)
    assert any(a["action"] == "rename_group" and a["status"] == "failed" for a in result["actions"])
    assert any(a["action"] == "readme_update" and a["status"] == "done" for a in result["actions"])
    assert readme.exists()
    assert (slides / "images" / "slide (1)").exists()
    assert not (slides / "images" / "slide").exists()
