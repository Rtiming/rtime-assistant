# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
from rtime_chat_runtime.attachment_directives import (
    extract_attachment_directives,
    validate_attachment_path,
)


def test_extract_attachment_directives_strips_internal_markers():
    cleaned, directives = extract_attachment_directives(
        "已生成。\n[[rtime-send-file:/tmp/report.pdf]]\n[[rtime-send-image:/tmp/plot.png]]"
    )

    assert cleaned == "已生成。"
    assert [item.kind for item in directives] == ["file", "image"]
    assert directives[0].path == "/tmp/report.pdf"
    assert directives[1].path == "/tmp/plot.png"


def test_validate_attachment_path_accepts_relative_file(tmp_path):
    artifact = tmp_path / "report.txt"
    artifact.write_text("ok", encoding="utf-8")

    result = validate_attachment_path("report.txt", base_dir=str(tmp_path))

    assert result.ok is True
    assert result.path == str(artifact)
    assert result.size == 2


def test_validate_attachment_path_rejects_sensitive_file_name(tmp_path):
    artifact = tmp_path / "api-token.txt"
    artifact.write_text("secret", encoding="utf-8")

    result = validate_attachment_path(str(artifact), base_dir=str(tmp_path))

    assert result.ok is False
    assert "凭据" in result.reason


def test_validate_attachment_path_requires_image_suffix_for_images(tmp_path):
    artifact = tmp_path / "not-image.txt"
    artifact.write_text("ok", encoding="utf-8")

    result = validate_attachment_path(str(artifact), kind="image")

    assert result.ok is False
    assert "图片" in result.reason
