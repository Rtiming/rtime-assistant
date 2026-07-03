---
name: reminder
description: 登记、查看、取消 rtime 飞书定时提醒。当用户说“提醒我”“定时提醒”“闹钟”“X分钟后叫我”时使用；必须写入 rtime reminders JSONL，不使用 Claude Code Cron。
---

# 提醒登记技能

飞书手机推送提醒只走系统提醒队列：

```text
brain/_system/reminders.jsonl
```

不要用 Claude Code `CronCreate`、`CronList`、`CronDelete` 登记飞书提醒；
这些任务只属于当前 Claude 会话，不会被 `reminder.timer` 发送到用户手机。

## 登记

1. 用 `date -Iseconds` 确认当前时间。
2. 默认按北京时间（UTC+8）解析用户时间，除非用户明确指定其他时区。
3. 把时间转成带时区的 ISO，例如 `2026-06-11T09:30:00+08:00`。
4. 明确选择模式：
   - `notify`：到点直接发送固定消息，不唤醒助手模型；
   - `wake`：到点启动一次独立助手任务，把任务结果发到飞书。

```bash
rtime-reminder-register add --mode notify --due "2026-06-11T09:30:00+08:00" --message "提醒内容"
rtime-reminder-register add --mode wake --due "2026-06-11T09:30:00+08:00" --message "唤醒标题" --prompt "到点执行的助手任务"
```

周期提醒只允许：

```text
none hourly daily weekly
```

```bash
rtime-reminder-register add --mode notify --due "2026-06-12T08:00:00+08:00" --repeat daily --message "提醒内容"
```

## 查看和取消

```bash
rtime-reminder-register list --status pending
rtime-reminder-register list --status failed
rtime-reminder-register cancel --id <reminder-id>
```

工具输出只返回 id、due、repeat、status、message 长度和 target 是否存在。不要在普通回复中泄露 Feishu open_id 或私密提醒正文。

如果登记失败提示没有 target，使用当前运行环境的
`RTIME_REMINDER_DEFAULT_TARGET`；飞书 App 迁移后不要从旧提醒记录复制
target，旧 App 的 open_id 不能跨 App 使用。
