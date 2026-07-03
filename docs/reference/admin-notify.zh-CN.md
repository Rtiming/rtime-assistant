# 管理员上报工具(rtime-notify-admin)使用参考

设计依据:docs/design/a3-studentunion-usage-findings-2026-07.zh-CN.md §四.3(owner 决策3)。
代码:`packages/rtime-chat-runtime/src/rtime_chat_runtime/admin_notify.py`(通道无关分发器,
stdlib-only)+ `deploy/bin/rtime-notify-admin`(模型调用的窄口 CLI)。

## 干什么

给渠道 bot 的模型一个**自主上报**能力:遇到答不上、需人工、疑似问题、值得管理员知道的
情况,模型可调 `rtime-notify-admin` 给管理员发一条通知。是否上报由模型结合情况决定
(提示词引导不滥用),不做字面"转人工"。

## 通道配置(env `RTIME_ADMIN_NOTIFY`)

JSON 数组,每项一个通道;凭据走 env 不入 git。支持类型:

| type | 字段 | 说明 |
|---|---|---|
| `feishu_selfheal` | `queue_dir` | **推荐/零配置**:复用 host 上 qq_selfheal 已在工作的飞书投递(它有飞书凭据+owner open_id)。容器只往共享队列目录写请求文件,守护轮询→发飞书→删文件。**容器零新增密钥**,与 rtime-qq-code 同机制。owner 无需创建任何 webhook。 |
| `feishu_webhook` | `url` | 飞书自定义机器人 incoming webhook(单 URL,无需 app secret)。owner 需在飞书群建自定义机器人拿 URL。 |
| `webhook` | `url` | 通用 webhook,POST `{text, source}`(接自建后端/其他 IM)。 |
| `email` | `host`/`port`/`user`/`password`/`to`/`from`/`tls` | SMTP 邮件到管理员邮箱(stdlib smtplib)。 |
| `qq` | — | 给管理员 QQ:扩展点,需桥触发文件集成(类比 rtime-qq-code),**首期未接线**,返回 not-wired。 |

示例(飞书 webhook + 邮件双通道):
```json
[{"type":"feishu_webhook","url":"https://open.feishu.cn/open-apis/bot/v2/hook/xxx"},
 {"type":"email","host":"smtp.ustc.edu.cn","port":587,"user":"u","password":"p","to":"admin@x"}]
```

## 调用(模型)

```bash
rtime-notify-admin --summary "有同学反复问社团注册截止,库里查不到" \
  --reason 答不上 --urgency low|normal|high --source studentunion-qq
```

- best-effort 多通道分发;JSON stdout **元数据 only**(每通道 ok/detail),不回显正文。
- 退出码:0=至少一个通道送达;1=全部失败;3=未配置任何通道(no-op,明确告诉模型未送达)。

## 权限接线

- 只读公开 bot(学生会):`Bash(rtime-notify-admin *)` 已在 `tool_policy.READONLY_ALLOWED`
  基线内——只读门下也能上报(工具只发通知、不读库不碰凭据)。
- 非只读渠道:命中上报意图(转人工/投诉/反馈/紧急/找管理员等 `_ESCALATION_CONTEXT_RE`)时
  放行 `NOTIFY_ALLOWED_TOOLS` + 附上报提示。
- 未配置 `RTIME_ADMIN_NOTIFY` 时工具 no-op(退出码 3),不误导模型以为已送达。

## 部署(需 owner 提供通道)

在 QQ/飞书容器 env 设 `RTIME_ADMIN_NOTIFY`(如飞书群机器人 webhook URL)。工具随
deploy/bin 进镜像 PATH。未设=能力在位但无通道(安全)。

测试:`packages/rtime-chat-runtime/tests/test_admin_notify.py`(分发/best-effort/未配置/
邮件缺配/QQ未接线)+ `test_tool_policy.py`(只读 allowlist/意图放行/提示)。
