# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""M3 multimodal: media parsing, prompt build, vision guard, in/outbound handling."""

import asyncio

import qq_bridge.app as app_mod
import qq_bridge.media as media_mod
from qq_bridge.app import build_model_handler
from qq_bridge.config import QQBridgeConfig
from qq_bridge.media import (
    build_media_prompt,
    extract_file_text,
    fetch_napcat_file,
    to_base64_uri,
)
from qq_bridge.onebot.cqcode import MediaSegment, extract_media, extract_plain_text
from qq_bridge.onebot.protocol import (
    file_upload_action,
    image_send_action,
    parse_message_event,
)
from rtime_chat_runtime.model_routing import model_can_see


def _run(coro):
    return asyncio.run(coro)


def _replies():
    out: list[str] = []

    async def reply(t):
        out.append(t)

    return out, reply


def _actions():
    calls: list[tuple] = []

    async def send_action(action, params):
        calls.append((action, params))

    return calls, send_action


# real NapCat array segment for an animated sticker (from the live archive)
_STICKER_SEG = {
    "type": "image",
    "data": {
        "summary": "[动画表情]",
        "file": "30D1AF.jpg",
        "sub_type": 1,
        "url": "https://multimedia.nt.qq.com.cn/download?appid=1406&fileid=abc",
        "file_size": "143054",
    },
}
_PHOTO_SEG = {
    "type": "image",
    "data": {"file": "p.jpg", "sub_type": 0, "url": "https://cdn/p.jpg"},
}
_FACE_SEG = {"type": "face", "data": {"id": "28"}}


def _event(message, message_id=1):
    return {
        "post_type": "message",
        "message_type": "private",
        "user_id": 111,
        "self_id": 479,
        "message_id": message_id,
        "message": message,
        "raw_message": "",
        "sender": {"user_id": 111},
    }


# --- cqcode.extract_media (array + CQ string) ---
def test_extract_media_array_sticker_photo_face():
    media = extract_media(
        [{"type": "text", "data": {"text": "hi"}}, _STICKER_SEG, _PHOTO_SEG, _FACE_SEG]
    )
    kinds = [m.kind for m in media]
    assert kinds == ["sticker", "image", "face"]
    assert media[0].summary == "[动画表情]" and media[0].url.startswith("https://")
    assert media[1].kind == "image"  # sub_type 0, no summary -> normal photo
    assert media[2].face_id == "28" and media[2].summary  # mapped to a name


def test_extract_media_cq_string_image():
    s = "[CQ:image,summary=&#91;动画表情&#93;,file=x.jpg,sub_type=1,url=https://cdn/x.jpg]看"
    media = extract_media(s)
    assert len(media) == 1 and media[0].kind == "sticker"
    assert media[0].url == "https://cdn/x.jpg"
    assert media[0].summary == "[动画表情]"
    assert extract_plain_text(s) == "看"  # CQ stripped from text


def test_extract_media_file_and_voice():
    media = extract_media(
        [
            {
                "type": "file",
                "data": {
                    "file": "report.pdf",
                    "url": "https://cdn/r.pdf",
                    "file_size": "10",
                },
            },
            {"type": "record", "data": {"url": "https://cdn/v.amr"}},
        ]
    )
    assert media[0].kind == "file" and media[0].name == "report.pdf"
    assert media[1].kind == "voice"


def test_parse_event_populates_media():
    msg = parse_message_event(
        _event([{"type": "text", "data": {"text": "看图"}}, _PHOTO_SEG])
    )
    assert msg.text == "看图"
    assert len(msg.media) == 1 and msg.media[0].kind == "image"


# --- media helpers ---
def test_build_media_prompt_references_paths_and_read():
    p = build_media_prompt(
        "这是什么",
        ["/qq/inbound/a.jpg", "/qq/inbound/b.jpg"],
        inline_notes=["[表情:憨笑]"],
        file_notes=[],
        voice_count=0,
        video_count=0,
    )
    assert "这是什么" in p and "Read" in p
    assert "/qq/inbound/a.jpg" in p and "/qq/inbound/b.jpg" in p
    assert "憨笑" in p


def test_build_media_prompt_voice_and_video_noted():
    p = build_media_prompt(
        "", [], inline_notes=[], file_notes=[], voice_count=1, video_count=1
    )
    assert "语音" in p and "视频" in p


def test_build_media_prompt_voice_transcribed_inlined():
    p = build_media_prompt(
        "",
        [],
        inline_notes=[],
        file_notes=[],
        voice_texts=["帮我复习配分函数"],
        voice_count=0,
        video_count=0,
    )
    assert "帮我复习配分函数" in p and "转写" in p


# --- voice STT (sherpa-onnx) -------------------------------------------------
def _write_silence_wav(path, sample_rate=16000, ms=200):
    import struct
    import wave

    n = int(sample_rate * ms / 1000)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(struct.pack("<%dh" % n, *([0] * n)))


class _FakeStream:
    def __init__(self, text):
        self.result = type("R", (), {"text": text})()

    def accept_waveform(self, sample_rate, samples):
        pass


class _FakeRecognizer:
    def __init__(self, text):
        self._text = text

    def create_stream(self):
        return _FakeStream(self._text)

    def decode_stream(self, stream):
        pass


def test_read_wav_roundtrip(tmp_path):
    wav = tmp_path / "a.wav"
    _write_silence_wav(wav)
    sample_rate, samples = media_mod._read_wav(str(wav))
    assert sample_rate == 16000 and len(samples) == 3200


def test_transcribe_voice_uses_recognizer(tmp_path, monkeypatch):
    from types import SimpleNamespace

    wav = tmp_path / "out.wav"
    _write_silence_wav(wav)
    monkeypatch.setattr(media_mod, "_get_recognizer", lambda d: _FakeRecognizer("你好"))
    cfg = SimpleNamespace(
        stt_model_dir=str(tmp_path),
        napcat_file_dir=str(tmp_path),
        napcat_http="http://127.0.0.1:3000",
    )
    seg = MediaSegment(kind="voice", name="abc.amr")

    async def fake_get_wav(http, ref, napcat_dir, timeout):
        return str(wav)

    text = _run(media_mod.transcribe_voice(seg, config=cfg, get_wav=fake_get_wav))
    assert text == "你好"


def test_transcribe_voice_off_when_no_model(tmp_path):
    from types import SimpleNamespace

    cfg = SimpleNamespace(
        stt_model_dir="",
        napcat_file_dir=str(tmp_path),
        napcat_http="http://127.0.0.1:3000",
    )
    seg = MediaSegment(kind="voice", name="abc.amr")
    assert _run(media_mod.transcribe_voice(seg, config=cfg)) is None


def test_to_base64_uri(tmp_path):
    f = tmp_path / "x.png"
    f.write_bytes(b"\x89PNG\r\n")
    uri = to_base64_uri(str(f))
    assert uri.startswith("base64://")


def test_fetch_napcat_file_via_shared_mount(tmp_path):
    napcat_dir = tmp_path / "napcat"
    napcat_dir.mkdir()
    (napcat_dir / "report.pdf").write_bytes(b"%PDF-1.4 test")
    dest = tmp_path / "inbound"
    calls = []

    async def send_action(action, params):
        calls.append((action, params))

    seg = MediaSegment(kind="file", name="report.pdf", file_id="fid-1")
    path = _run(
        fetch_napcat_file(
            seg,
            napcat_file_dir=str(napcat_dir),
            dest_dir=str(dest),
            stem="s1",
            send_action=send_action,
        )
    )
    assert path and path.endswith(".pdf")
    assert calls and calls[0][0] == "get_file" and calls[0][1]["file_id"] == "fid-1"
    assert (dest / "s1.pdf").read_bytes() == b"%PDF-1.4 test"  # copied into our dir


def test_extract_file_text_reads_text_file(tmp_path):
    f = tmp_path / "notes.md"
    f.write_text("# 热统复习\n配分函数 Z = Σ exp(-βEi)", encoding="utf-8")
    text, note = extract_file_text(str(f))
    assert "配分函数" in text and "文本" in note


def test_extract_file_text_truncates(tmp_path):
    f = tmp_path / "big.txt"
    f.write_text("x" * 50000, encoding="utf-8")
    text, _ = extract_file_text(str(f), max_chars=1000)
    assert len(text) < 1200 and "截断" in text


def test_extract_file_text_unsupported_type(tmp_path):
    f = tmp_path / "a.bin"
    f.write_bytes(b"\x00\x01\x02")
    text, note = extract_file_text(str(f))
    assert text == "" and "不支持" in note


def test_readable_ratio_flags_garbled():
    from qq_bridge.media import _readable_ratio

    assert _readable_ratio("热统配分函数 Z = Σ exp(-βE)") > 0.8  # normal text
    assert _readable_ratio("ุ໾৘ն Ӊູ bӈඔ ၹሰ ჰሰᇉਈ") < 0.6  # garbled CID glyphs


def test_fetch_napcat_file_none_when_unconfigured(tmp_path):
    seg = MediaSegment(kind="file", name="x.pdf", file_id="fid-2")

    async def send_action(action, params):
        raise AssertionError("get_file must not be called without a mount dir")

    path = _run(
        fetch_napcat_file(
            seg,
            napcat_file_dir="",  # not configured => unsupported, no action fired
            dest_dir=str(tmp_path / "inbound"),
            stem="s2",
            send_action=send_action,
        )
    )
    assert path is None


# --- vision guard ---
def test_model_can_see_defaults_and_aliases():
    assert model_can_see("") is True  # wrapper default kimi sees via Read
    assert model_can_see("opus") is True  # alias -> claude-opus (vision)
    assert model_can_see("claude-opus-4-6") is True
    assert model_can_see("kimi-code") is True  # empirical override


# --- outbound action builders ---
def test_image_send_action_private():
    msg = parse_message_event(_event("x"))
    action, params = image_send_action(msg, "base64://AAAA")
    assert action == "send_private_msg"
    assert params["user_id"] == 111
    assert params["message"][0]["type"] == "image"
    assert params["message"][0]["data"]["file"] == "base64://AAAA"


def test_file_upload_action_private():
    msg = parse_message_event(_event("x"))
    action, params = file_upload_action(msg, "base64://AAAA", "r.pdf")
    assert action == "upload_private_file"
    assert params["user_id"] == 111 and params["name"] == "r.pdf"


# --- handler: inbound image is downloaded and Read ---
def test_handler_inbound_image_downloaded_and_read(monkeypatch, tmp_path):
    cfg = QQBridgeConfig(
        owner_ids=frozenset({"111"}),
        claude_cli="/x/claude",
        sessions_dir=str(tmp_path),
        stream_output=False,
    )

    async def fake_download(url, dest_dir, **k):
        return "/qq/inbound/pic.jpg"

    captured = {}

    async def fake_run(prompt, *, allowed_tools=None, **k):
        captured["prompt"] = prompt
        captured["allowed"] = allowed_tools
        return ("我看到一张图", "sess-1", False)

    monkeypatch.setattr(media_mod, "download_url", fake_download)
    monkeypatch.setattr(app_mod, "download_url", fake_download)
    monkeypatch.setattr(app_mod, "run_claude", fake_run)

    out, reply = _replies()
    msg = parse_message_event(
        _event([{"type": "text", "data": {"text": "这是什么"}}, _PHOTO_SEG])
    )
    _run(build_model_handler(cfg)(msg, reply))

    assert any("收到附件" in o for o in out)  # media ack
    assert "/qq/inbound/pic.jpg" in captured["prompt"] and "Read" in captured["prompt"]
    assert any("我看到一张图" in o for o in out)


# --- handler: vision guard tells user when model can't see ---
def test_handler_vision_guard_blocks_image_only(monkeypatch, tmp_path):
    cfg = QQBridgeConfig(
        owner_ids=frozenset({"111"}),
        claude_cli="/x/claude",
        sessions_dir=str(tmp_path),
        stream_output=False,
        model="deepseek-v4-flash-ascend",
    )

    async def fake_download(url, dest_dir, **k):
        return "/qq/inbound/pic.jpg"

    ran = {"called": False}

    async def fake_run(prompt, **k):
        ran["called"] = True
        return ("x", "s", False)

    monkeypatch.setattr(app_mod, "download_url", fake_download)
    monkeypatch.setattr(app_mod, "model_can_see", lambda m: False)
    monkeypatch.setattr(app_mod, "run_claude", fake_run)

    out, reply = _replies()
    msg = parse_message_event(_event([_PHOTO_SEG]))  # image only, no text
    _run(build_model_handler(cfg)(msg, reply))

    assert any("看不了图" in o for o in out)
    assert ran["called"] is False  # image-only + blind model => no model run


def test_handler_vision_guard_text_still_answered(monkeypatch, tmp_path):
    cfg = QQBridgeConfig(
        owner_ids=frozenset({"111"}),
        claude_cli="/x/claude",
        sessions_dir=str(tmp_path),
        stream_output=False,
    )

    async def fake_download(url, dest_dir, **k):
        return "/qq/inbound/pic.jpg"

    captured = {}

    async def fake_run(prompt, **k):
        captured["prompt"] = prompt
        return ("文字答案", "s", False)

    monkeypatch.setattr(app_mod, "download_url", fake_download)
    monkeypatch.setattr(app_mod, "model_can_see", lambda m: False)
    monkeypatch.setattr(app_mod, "run_claude", fake_run)

    out, reply = _replies()
    msg = parse_message_event(
        _event([{"type": "text", "data": {"text": "在吗"}}, _PHOTO_SEG])
    )
    _run(build_model_handler(cfg)(msg, reply))

    assert any("看不了图" in o for o in out)  # warned
    assert "/qq/inbound/pic.jpg" not in captured["prompt"]  # image dropped from prompt
    assert any("文字答案" in o for o in out)  # text still answered


# --- handler: outbound image directive is sent via OneBot ---
def test_handler_outbound_image_sent(monkeypatch, tmp_path):
    cfg = QQBridgeConfig(
        owner_ids=frozenset({"111"}),
        claude_cli="/x/claude",
        sessions_dir=str(tmp_path),
        stream_output=False,
    )
    img = tmp_path / "out.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)

    async def fake_run(prompt, **k):
        return (f"给你图\n[[rtime-send-image:{img}]]", "s", False)

    monkeypatch.setattr(app_mod, "run_claude", fake_run)

    out, reply = _replies()
    calls, send_action = _actions()
    msg = parse_message_event(_event("发我图"))
    _run(build_model_handler(cfg)(msg, reply, send_action))

    assert any("给你图" in o for o in out)
    assert all("rtime-send-image" not in o for o in out)  # directive stripped from text
    assert calls and calls[0][0] == "send_private_msg"
    assert calls[0][1]["message"][0]["data"]["file"].startswith("base64://")
