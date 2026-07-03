# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
import json

from feishu_client import FeishuClient, _card_json, _post_json


def test_guess_file_type_for_common_documents():
    assert FeishuClient._guess_file_type("a.pdf") == "pdf"
    assert FeishuClient._guess_file_type("a.docx") == "doc"
    assert FeishuClient._guess_file_type("a.xlsx") == "xls"
    assert FeishuClient._guess_file_type("a.pptx") == "ppt"
    assert FeishuClient._guess_file_type("a.zip") == "stream"


def test_encode_multipart_form_data_contains_fields_and_file(tmp_path):
    artifact = tmp_path / "report.txt"
    artifact.write_text("hello", encoding="utf-8")

    body = FeishuClient._encode_multipart_form_data(
        "boundary",
        {"file_type": "stream", "file_name": "report.txt"},
        "file",
        str(artifact),
    )

    assert b'name="file_type"' in body
    assert b"stream" in body
    assert b'name="file"; filename="report.txt"' in body
    assert b"hello" in body
    assert body.endswith(b"--boundary--\r\n")


def test_safe_multipart_filename_removes_header_breaks():
    assert FeishuClient._safe_multipart_filename('bad"\r\nname.txt') == "bad_name.txt"


def test_post_json_uses_markdown_rich_text_tag():
    payload = json.loads(_post_json("## 标题\n\n| A | B |\n| --- | --- |"))

    assert payload["zh_cn"]["content"] == [[{
        "tag": "md",
        "text": "## 标题\n\n| A | B |\n| --- | --- |",
    }]]


def test_card_json_uses_markdown_elements_for_assistant_body():
    payload = json.loads(_card_json("## 标题\n\n| A | B |\n| --- | --- |", loading=False))

    assert payload["schema"] == "2.0"
    assert payload["body"]["elements"] == [{
        "tag": "markdown",
        "content": "## 标题\n\n| A | B |\n| --- | --- |",
    }]
