# normalized transcript(聊天归一层)使用参考

设计: [../design/chat-archive-storage-2026-07.zh-CN.md](../design/chat-archive-storage-2026-07.zh-CN.md) §2;
规格: [../specs/spec-a2-normalized-transcript.zh-CN.md](../specs/spec-a2-normalized-transcript.zh-CN.md)。
代码: `packages/rtime-chat-runtime/src/rtime_chat_runtime/transcript.py`(stdlib-only,离线批处理,不在消息热路径)。

## 干什么

把 raw 归档(legacy 平铺 jsonl + envelope 分片)归一成统一事件层,让"谁在哪问了
什么→bot 答了什么"可查可回放,供 A3 质量分析/A4 评测集/F 记忆管线消费。

## CLI

```bash
PYTHONPATH=packages/rtime-chat-runtime/src python3 -m rtime_chat_runtime.transcript \
  <src...> --out <root> [--channel qq]
```

- `src`:legacy 平铺文件(如 messages.jsonl)或 envelope 分片根目录(自动 rglob),可多个。
- 输出:`<root>/transcript/<channel>/YYYY/MM/DD/events.jsonl`(日期取 sent_at)。
- **幂等**:event_id 内容寻址(`rta_evt_<sha256(canonical raw)[:24]>`)——同一条平台
  事件不管来自哪个源、跑多少遍,永远一条;重跑报告里只涨 `deduped`。
- 报告无正文:events_written/deduped/malformed/by_chat_type/by_direction。

## 关键语义

| 项 | 口径 |
|---|---|
| 出站 | `post_type=rtime_outbound`(ws_server.send_action 发出后落 raw)→ direction=outbound, message_class=bot_reply |
| 群临时会话 | 私聊 sub_type=group → chat_type=temporary |
| 隐私 | chat/sender/mentions 一律 sha256 hash,**不落明文 QQ 号**;text/segments 保留(owner-local 证据层) |
| run 关联 | run_id/session_id/model 本期一律 null(A2.1 再接),status=archived |
| 坏行 | 计入 malformed,绝不中断批处理 |

## 测试

`packages/rtime-chat-runtime/tests/test_transcript.py`(字段映射/幂等/legacy↔envelope
同构/明文号否定断言/坏行);出站捕获:`apps/qq-bridge/tests/test_archive_coverage.py::test_outbound_action_archived`。
