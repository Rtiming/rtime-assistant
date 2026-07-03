# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""
飞书 API 异步封装。
流式方案：发送内联卡片消息 → 用 patch 逐步更新内容（比 cardkit 流式卡片更简单可靠）。
"""

import asyncio
import json
import mimetypes
import os
import re
import tempfile
import time

import lark_oapi as lark
from lark_oapi.api.im.v1.model import (
    CreateMessageRequest,
    CreateMessageRequestBody,
    PatchMessageRequest,
    PatchMessageRequestBody,
    ReplyMessageRequest,
    ReplyMessageRequestBody,
)
from rtime_chat_runtime.run_log import append_run_event


def _card_json(content: str, loading: bool = False) -> str:
    """
    生成卡片 JSON 字符串（Card JSON 2.0）

    飞书卡片 markdown 元素有长度限制（约 3000 字符），
    超过限制时自动分段为多个 markdown 元素。
    """
    elements = []
    if loading:
        elements.append({"tag": "markdown", "content": "⏳ 思考中..."})
    else:
        # 飞书 markdown 元素长度限制约 3000 字符，保守使用 2800
        MAX_CHUNK_SIZE = 2800

        if len(content) <= MAX_CHUNK_SIZE:
            # 内容不长，直接发送
            elements.append({"tag": "markdown", "content": content})
        else:
            # 内容过长，分段发送
            # 尝试按段落分割，避免在句子中间截断
            chunks = []
            current_chunk = ""

            # 按换行符分割
            lines = content.split('\n')

            for line in lines:
                # 如果单行就超过限制，强制截断
                if len(line) > MAX_CHUNK_SIZE:
                    # 先保存当前块
                    if current_chunk:
                        chunks.append(current_chunk)
                        current_chunk = ""

                    # 强制分割长行
                    for i in range(0, len(line), MAX_CHUNK_SIZE):
                        chunks.append(line[i:i + MAX_CHUNK_SIZE])
                    continue

                # 检查加上这行是否会超过限制
                if len(current_chunk) + len(line) + 1 > MAX_CHUNK_SIZE:
                    # 超过限制，保存当前块，开始新块
                    if current_chunk:
                        chunks.append(current_chunk)
                    current_chunk = line
                else:
                    # 未超过限制，追加到当前块
                    if current_chunk:
                        current_chunk += '\n' + line
                    else:
                        current_chunk = line

            # 保存最后一块
            if current_chunk:
                chunks.append(current_chunk)

            # 为每个块创建 markdown 元素
            for i, chunk in enumerate(chunks):
                # 第一块不加前缀，后续块加分段标记
                if i > 0:
                    chunk = f"**（续 {i}）**\n\n{chunk}"
                elements.append({"tag": "markdown", "content": chunk})

    return json.dumps({
        "schema": "2.0",
        "body": {"elements": elements},
    }, ensure_ascii=False)


def _post_json(content: str) -> str:
    """Build legacy Feishu rich-text post content using the md tag."""
    return json.dumps({
        "zh_cn": {
            "content": [[{"tag": "md", "text": content}]],
        },
    }, ensure_ascii=False)


class FeishuClient:
    def __init__(self, client: lark.Client, app_id: str = "", app_secret: str = ""):
        self.client = client
        self._app_id = app_id
        self._app_secret = app_secret
        self._tenant_access_token = ""
        self._tenant_access_token_expires_at = 0.0

    async def _retry_with_backoff(self, coro_func, max_retries: int = 3, initial_delay: float = 0.5, operation: str = ""):
        """
        执行异步操作，失败时指数退避重试。

        Args:
            coro_func: 返回 coroutine 的可调用对象
            max_retries: 最多重试次数（不包括首次尝试）
            initial_delay: 初始延迟秒数

        Returns:
            操作结果

        Raises:
            最后一次尝试的异常
        """
        started = time.monotonic()
        delay = initial_delay
        last_error = None

        for attempt in range(max_retries + 1):
            try:
                result = await coro_func()
                if operation:
                    append_run_event(
                        "feishu_api_call",
                        operation=operation,
                        ok=True,
                        attempts=attempt + 1,
                        dur_ms=int((time.monotonic() - started) * 1000),
                    )
                return result
            except Exception as e:
                last_error = e
                if attempt < max_retries:
                    print(f"[retry] 第 {attempt + 1} 次失败，{delay:.1f}s 后重试: {e}", flush=True)
                    await asyncio.sleep(delay)
                    delay *= 2  # 指数退避
                else:
                    print(f"[retry] 已达最大重试次数 {max_retries + 1}，放弃", flush=True)

        if operation:
            append_run_event(
                "feishu_api_call",
                operation=operation,
                ok=False,
                attempts=max_retries + 1,
                dur_ms=int((time.monotonic() - started) * 1000),
                error_type=type(last_error).__name__ if last_error else "unknown",
            )
        raise last_error

    # ── 发送消息 ──────────────────────────────────────────────

    async def send_card_to_user(self, open_id: str, content: str = "", loading: bool = True) -> str:
        """向用户发送卡片消息，返回 message_id（带重试）"""
        async def _send():
            req = (
                CreateMessageRequest.builder()
                .receive_id_type("open_id")
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(open_id)
                    .msg_type("interactive")
                    .content(_card_json(content, loading=loading))
                    .build()
                )
                .build()
            )
            resp = await self.client.im.v1.message.acreate(req)
            if not resp.success():
                raise RuntimeError(f"发送卡片消息失败: {resp.code} {resp.msg}")
            return resp.data.message_id

        return await self._retry_with_backoff(_send, max_retries=3, operation="send_card_to_user")

    async def reply_card(self, message_id: str, content: str = "", loading: bool = True) -> str:
        """回复用户消息（卡片形式），触发通知。返回回复消息的 message_id（带重试）"""
        async def _reply():
            req = (
                ReplyMessageRequest.builder()
                .message_id(message_id)
                .request_body(
                    ReplyMessageRequestBody.builder()
                    .msg_type("interactive")
                    .content(_card_json(content, loading=loading))
                    .build()
                )
                .build()
            )
            resp = await self.client.im.v1.message.areply(req)
            if not resp.success():
                raise RuntimeError(f"回复卡片消息失败: {resp.code} {resp.msg}")
            return resp.data.message_id

        return await self._retry_with_backoff(_reply, max_retries=3, operation="reply_card")

    async def update_card(self, message_id: str, content: str):
        """用 patch 更新已发送的卡片内容（带重试）"""
        async def _update():
            req = (
                PatchMessageRequest.builder()
                .message_id(message_id)
                .request_body(
                    PatchMessageRequestBody.builder()
                    .content(_card_json(content, loading=False))
                    .build()
                )
                .build()
            )
            resp = await self.client.im.v1.message.apatch(req)
            if not resp.success():
                raise RuntimeError(f"patch 卡片失败: {resp.code} {resp.msg}")

        await self._retry_with_backoff(_update, max_retries=3, operation="update_card")

    async def upload_image(self, path: str) -> str:
        """上传本地图片到飞书，返回 image_key（不阻塞事件循环）。"""
        return await asyncio.to_thread(self._upload_image_sync, path)

    async def upload_file(self, path: str, file_type: str | None = None, file_name: str | None = None) -> str:
        """上传本地文件到飞书，返回 file_key（不阻塞事件循环）。"""
        return await asyncio.to_thread(self._upload_file_sync, path, file_type, file_name)

    async def send_image_to_user(self, open_id: str, path: str) -> str:
        """上传并发送图片消息给用户。"""
        async def _send():
            image_key = await self.upload_image(path)
            req = (
                CreateMessageRequest.builder()
                .receive_id_type("open_id")
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(open_id)
                    .msg_type("image")
                    .content(json.dumps({"image_key": image_key}, ensure_ascii=False))
                    .build()
                )
                .build()
            )
            resp = await self.client.im.v1.message.acreate(req)
            if not resp.success():
                raise RuntimeError(f"发送图片消息失败: {resp.code} {resp.msg}")
            return resp.data.message_id

        return await self._retry_with_backoff(_send, max_retries=2, operation="send_image_to_user")

    async def reply_image(self, message_id: str, path: str) -> str:
        """上传并以图片消息回复用户。"""
        async def _reply():
            image_key = await self.upload_image(path)
            req = (
                ReplyMessageRequest.builder()
                .message_id(message_id)
                .request_body(
                    ReplyMessageRequestBody.builder()
                    .msg_type("image")
                    .content(json.dumps({"image_key": image_key}, ensure_ascii=False))
                    .build()
                )
                .build()
            )
            resp = await self.client.im.v1.message.areply(req)
            if not resp.success():
                raise RuntimeError(f"回复图片消息失败: {resp.code} {resp.msg}")
            return resp.data.message_id

        return await self._retry_with_backoff(_reply, max_retries=2, operation="reply_image")

    async def send_file_to_user(self, open_id: str, path: str) -> str:
        """上传并发送文件消息给用户。"""
        async def _send():
            file_key = await self.upload_file(path)
            req = (
                CreateMessageRequest.builder()
                .receive_id_type("open_id")
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(open_id)
                    .msg_type("file")
                    .content(json.dumps({"file_key": file_key}, ensure_ascii=False))
                    .build()
                )
                .build()
            )
            resp = await self.client.im.v1.message.acreate(req)
            if not resp.success():
                raise RuntimeError(f"发送文件消息失败: {resp.code} {resp.msg}")
            return resp.data.message_id

        return await self._retry_with_backoff(_send, max_retries=2, operation="send_file_to_user")

    async def reply_file(self, message_id: str, path: str) -> str:
        """上传并以文件消息回复用户。"""
        async def _reply():
            file_key = await self.upload_file(path)
            req = (
                ReplyMessageRequest.builder()
                .message_id(message_id)
                .request_body(
                    ReplyMessageRequestBody.builder()
                    .msg_type("file")
                    .content(json.dumps({"file_key": file_key}, ensure_ascii=False))
                    .build()
                )
                .build()
            )
            resp = await self.client.im.v1.message.areply(req)
            if not resp.success():
                raise RuntimeError(f"回复文件消息失败: {resp.code} {resp.msg}")
            return resp.data.message_id

        return await self._retry_with_backoff(_reply, max_retries=2, operation="reply_file")

    async def download_image(self, message_id: str, image_key: str) -> str:
        """下载飞书图片到临时文件，返回本地路径（不阻塞事件循环）"""
        return await asyncio.to_thread(
            self._download_image_sync, message_id, image_key
        )

    async def download_file(self, message_id: str, file_key: str, file_name: str = "") -> str:
        """下载飞书文件/音视频到临时文件，返回本地路径（不阻塞事件循环）"""
        return await asyncio.to_thread(
            self._download_file_sync, message_id, file_key, file_name
        )

    def _download_image_sync(self, message_id: str, image_key: str) -> str:
        """同步下载逻辑，在线程池中执行"""
        return self._download_message_resource_sync(
            message_id=message_id,
            resource_key=image_key,
            resource_type="image",
            file_name="",
            default_prefix="feishu-img",
            default_suffix=".jpg",
        )

    def _download_file_sync(self, message_id: str, file_key: str, file_name: str = "") -> str:
        """同步下载文件/音视频资源，在线程池中执行。"""
        return self._download_message_resource_sync(
            message_id=message_id,
            resource_key=file_key,
            resource_type="file",
            file_name=file_name,
            default_prefix="feishu-file",
            default_suffix=".bin",
        )

    def _upload_image_sync(self, path: str) -> str:
        """同步上传图片，在线程池中执行。"""
        return self._upload_multipart_sync(
            url="https://open.feishu.cn/open-apis/im/v1/images",
            fields={"image_type": "message"},
            file_field="image",
            path=path,
            result_key="image_key",
        )

    def _upload_file_sync(self, path: str, file_type: str | None = None, file_name: str | None = None) -> str:
        """同步上传文件，在线程池中执行。"""
        real_name = file_name or os.path.basename(path)
        return self._upload_multipart_sync(
            url="https://open.feishu.cn/open-apis/im/v1/files",
            fields={
                "file_type": file_type or self._guess_file_type(path),
                "file_name": real_name,
            },
            file_field="file",
            path=path,
            result_key="file_key",
        )

    def _tenant_access_token_sync(self, ctx) -> str:
        import urllib.request

        if not self._app_id or not self._app_secret:
            raise RuntimeError("Feishu app_id/app_secret 未配置，无法调用资源 API")

        now = time.time()
        if self._tenant_access_token and now < self._tenant_access_token_expires_at - 60:
            return self._tenant_access_token

        token_body = json.dumps({"app_id": self._app_id, "app_secret": self._app_secret}).encode()
        token_req = urllib.request.Request(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            data=token_body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(token_req, context=ctx, timeout=10) as r:
            payload = json.loads(r.read())

        code = payload.get("code", 0)
        if code != 0:
            raise RuntimeError(f"获取 tenant_access_token 失败: {code} {payload.get('msg', '')}")
        token = payload.get("tenant_access_token", "")
        if not token:
            raise RuntimeError("获取 tenant_access_token 失败: 响应缺少 tenant_access_token")
        self._tenant_access_token = token
        self._tenant_access_token_expires_at = now + int(payload.get("expire", 7200))
        return token

    def _upload_multipart_sync(
        self,
        *,
        url: str,
        fields: dict[str, str],
        file_field: str,
        path: str,
        result_key: str,
    ) -> str:
        import ssl
        import urllib.request
        import uuid

        if not os.path.isfile(path):
            raise FileNotFoundError(path)

        ctx = ssl.create_default_context()
        token = self._tenant_access_token_sync(ctx)
        boundary = f"----rtime-feishu-{uuid.uuid4().hex}"
        body = self._encode_multipart_form_data(boundary, fields, file_field, path)
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, context=ctx, timeout=60) as r:
            payload = json.loads(r.read())

        code = payload.get("code", 0)
        if code != 0:
            raise RuntimeError(f"飞书资源上传失败: {code} {payload.get('msg', '')}")
        key = payload.get("data", {}).get(result_key, "")
        if not key:
            raise RuntimeError(f"飞书资源上传失败: 响应缺少 {result_key}")
        return key

    @staticmethod
    def _encode_multipart_form_data(
        boundary: str,
        fields: dict[str, str],
        file_field: str,
        path: str,
    ) -> bytes:
        chunks: list[bytes] = []
        for name, value in fields.items():
            chunks.extend([
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                str(value).encode(),
                b"\r\n",
            ])

        filename = FeishuClient._safe_multipart_filename(os.path.basename(path))
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        chunks.extend([
            f"--{boundary}\r\n".encode(),
            (
                f'Content-Disposition: form-data; name="{file_field}"; '
                f'filename="{filename}"\r\n'
            ).encode(),
            f"Content-Type: {content_type}\r\n\r\n".encode(),
        ])
        with open(path, "rb") as f:
            chunks.append(f.read())
        chunks.extend([b"\r\n", f"--{boundary}--\r\n".encode()])
        return b"".join(chunks)

    @staticmethod
    def _safe_multipart_filename(name: str) -> str:
        sanitized = re.sub(r'[\r\n"\\\\]+', "_", name or "attachment")
        return sanitized[:120] or "attachment"

    @staticmethod
    def _guess_file_type(path: str) -> str:
        suffix = os.path.splitext(path)[1].lower()
        if suffix == ".pdf":
            return "pdf"
        if suffix in {".doc", ".docx"}:
            return "doc"
        if suffix in {".xls", ".xlsx"}:
            return "xls"
        if suffix in {".ppt", ".pptx"}:
            return "ppt"
        if suffix in {".mp4", ".m4v", ".mov"}:
            return "mp4"
        if suffix in {".m4a", ".mp3", ".ogg", ".opus", ".wav"}:
            return "opus"
        return "stream"

    def _download_message_resource_sync(
        self,
        *,
        message_id: str,
        resource_key: str,
        resource_type: str,
        file_name: str,
        default_prefix: str,
        default_suffix: str,
    ) -> str:
        """Download a user-sent message resource through the message resource API."""
        import ssl
        import urllib.parse
        import urllib.request

        ctx = ssl.create_default_context()
        token = self._tenant_access_token_sync(ctx)

        quoted_key = urllib.parse.quote(resource_key, safe="")
        url = (
            f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}"
            f"/resources/{quoted_key}?type={urllib.parse.quote(resource_type, safe='')}"
        )
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        tmp_path = self._resource_tmp_path(file_name, default_prefix, default_suffix)
        with urllib.request.urlopen(req, context=ctx, timeout=30) as r:
            content_type = r.headers.get("Content-Type", "")
            if not file_name:
                tmp_path = self._apply_content_type_suffix(tmp_path, content_type)
            with open(tmp_path, "wb") as f:
                f.write(r.read())

        return tmp_path

    @staticmethod
    def _resource_tmp_path(file_name: str, default_prefix: str, default_suffix: str) -> str:
        import uuid

        name = os.path.basename(file_name or "").strip()
        if name:
            name = re.sub(r"[\x00-\x1f/\\\\:]+", "_", name)
            stem, suffix = os.path.splitext(name)
            stem = stem[:80] or default_prefix
            suffix = suffix[:16] or default_suffix
            return os.path.join(tempfile.gettempdir(), f"{default_prefix}-{uuid.uuid4().hex[:8]}-{stem}{suffix}")
        return os.path.join(tempfile.gettempdir(), f"{default_prefix}-{uuid.uuid4().hex[:8]}{default_suffix}")

    @staticmethod
    def _apply_content_type_suffix(path: str, content_type: str) -> str:
        lower = (content_type or "").lower()
        mapping = [
            ("png", ".png"),
            ("gif", ".gif"),
            ("webp", ".webp"),
            ("jpeg", ".jpg"),
            ("pdf", ".pdf"),
            ("zip", ".zip"),
            ("json", ".json"),
            ("text", ".txt"),
        ]
        for needle, suffix in mapping:
            if needle in lower and not path.endswith(suffix):
                return os.path.splitext(path)[0] + suffix
        return path

    async def update_card_with_buttons(self, message_id: str, content: str, buttons: list[dict],
                                      flow: bool = False):
        """更新卡片内容并附加操作按钮。flow=True 时横排自动换行，False 时竖排。"""
        base = json.loads(_card_json(content))
        btn_elements = []
        for i, btn in enumerate(buttons):
            btn_elements.append({
                "tag": "button",
                "text": {"tag": "plain_text", "content": btn["text"]},
                "type": "default",
                "size": "small",
                "name": f"btn_{i}",
                "value": btn["value"],
                "behaviors": [{"type": "callback", "value": btn["value"]}],
            })
        if flow and btn_elements:
            # 横排: column_set + flex_mode flow
            columns = [{"tag": "column", "width": "auto", "elements": [b]} for b in btn_elements]
            base["body"]["elements"].append({"tag": "column_set", "flex_mode": "flow", "columns": columns})
        else:
            # 竖排: 每个按钮独占一行
            base["body"]["elements"].extend(btn_elements)
        card_content = json.dumps(base, ensure_ascii=False)

        async def _update():
            req = (
                PatchMessageRequest.builder()
                .message_id(message_id)
                .request_body(
                    PatchMessageRequestBody.builder()
                    .content(card_content)
                    .build()
                )
                .build()
            )
            resp = await self.client.im.v1.message.apatch(req)
            if not resp.success():
                raise RuntimeError(f"patch 卡片失败: {resp.code} {resp.msg}")

        await self._retry_with_backoff(_update, max_retries=3, operation="update_card_with_buttons")

    async def update_card_elements(self, message_id: str, elements: list[dict]):
        """用自定义 elements 列表更新卡片（支持 markdown + button 混排）"""
        card_content = json.dumps({
            "schema": "2.0",
            "body": {"elements": elements},
        }, ensure_ascii=False)

        async def _update():
            req = (
                PatchMessageRequest.builder()
                .message_id(message_id)
                .request_body(
                    PatchMessageRequestBody.builder()
                    .content(card_content)
                    .build()
                )
                .build()
            )
            resp = await self.client.im.v1.message.apatch(req)
            if not resp.success():
                raise RuntimeError(f"patch 卡片失败: {resp.code} {resp.msg}")

        await self._retry_with_backoff(_update, max_retries=3, operation="update_card_elements")

    async def reply_text(self, message_id: str, text: str) -> str:
        """回复纯文本消息（触发通知）"""
        async def _reply():
            req = (
                ReplyMessageRequest.builder()
                .message_id(message_id)
                .request_body(
                    ReplyMessageRequestBody.builder()
                    .msg_type("text")
                    .content(json.dumps({"text": text}))
                    .build()
                )
                .build()
            )
            resp = await self.client.im.v1.message.areply(req)
            if not resp.success():
                raise RuntimeError(f"回复文本消息失败: {resp.code} {resp.msg}")
            return resp.data.message_id

        return await self._retry_with_backoff(_reply, max_retries=2, operation="reply_text")

    async def reply_post(self, message_id: str, text: str) -> str:
        """以旧富文本 post(md) 回复用户消息（兼容接口，触发通知）。"""
        async def _reply():
            req = (
                ReplyMessageRequest.builder()
                .message_id(message_id)
                .request_body(
                    ReplyMessageRequestBody.builder()
                    .msg_type("post")
                    .content(_post_json(text))
                    .build()
                )
                .build()
            )
            resp = await self.client.im.v1.message.areply(req)
            if not resp.success():
                raise RuntimeError(f"回复富文本消息失败: {resp.code} {resp.msg}")
            return resp.data.message_id

        return await self._retry_with_backoff(_reply, max_retries=2, operation="reply_post")

    async def reply_markdown(self, message_id: str, text: str) -> str:
        """以交互卡片 markdown 元素回复用户消息（触发通知）。"""
        async def _reply():
            req = (
                ReplyMessageRequest.builder()
                .message_id(message_id)
                .request_body(
                    ReplyMessageRequestBody.builder()
                    .msg_type("interactive")
                    .content(_card_json(text, loading=False))
                    .build()
                )
                .build()
            )
            resp = await self.client.im.v1.message.areply(req)
            if not resp.success():
                raise RuntimeError(f"回复Markdown卡片失败: {resp.code} {resp.msg}")
            return resp.data.message_id

        return await self._retry_with_backoff(_reply, max_retries=2, operation="reply_markdown")

    async def send_text_to_user(self, open_id: str, text: str) -> str:
        """发送纯文本消息"""
        async def _send():
            req = (
                CreateMessageRequest.builder()
                .receive_id_type("open_id")
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(open_id)
                    .msg_type("text")
                    .content(json.dumps({"text": text}))
                    .build()
                )
                .build()
            )
            resp = await self.client.im.v1.message.acreate(req)
            if not resp.success():
                raise RuntimeError(f"发送文本消息失败: {resp.code} {resp.msg}")
            return resp.data.message_id

        return await self._retry_with_backoff(_send, max_retries=2, operation="send_text_to_user")

    async def send_post_to_user(self, open_id: str, text: str) -> str:
        """发送旧富文本 post(md) 消息（兼容接口）。"""
        async def _send():
            req = (
                CreateMessageRequest.builder()
                .receive_id_type("open_id")
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(open_id)
                    .msg_type("post")
                    .content(_post_json(text))
                    .build()
                )
                .build()
            )
            resp = await self.client.im.v1.message.acreate(req)
            if not resp.success():
                raise RuntimeError(f"发送富文本消息失败: {resp.code} {resp.msg}")
            return resp.data.message_id

        return await self._retry_with_backoff(_send, max_retries=2, operation="send_post_to_user")

    async def send_markdown_to_user(self, open_id: str, text: str) -> str:
        """发送交互卡片 markdown 元素消息。"""
        async def _send():
            req = (
                CreateMessageRequest.builder()
                .receive_id_type("open_id")
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(open_id)
                    .msg_type("interactive")
                    .content(_card_json(text, loading=False))
                    .build()
                )
                .build()
            )
            resp = await self.client.im.v1.message.acreate(req)
            if not resp.success():
                raise RuntimeError(f"发送Markdown卡片失败: {resp.code} {resp.msg}")
            return resp.data.message_id

        return await self._retry_with_backoff(_send, max_retries=2, operation="send_markdown_to_user")
