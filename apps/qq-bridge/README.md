# qq-bridge

`rtime-assistant` 的 QQ 机器人桥(OneBot v11),用一个**专用小号**登录,让用户在 QQ 里
和助手私聊。设计与路线见 [docs/qq-bridge-development.zh-CN.md](../../docs/qq-bridge-development.zh-CN.md)。

## 状态:M3(多模态,真机运行在 orange pi)

QQ 私聊/群 @bot → 真 Claude+brain 回复(底层 `claude-rtime` → Kimi)。已具备:

- **模型接入**:复用共享核心 `rtime_chat_runtime`(`model_runner`/`model_routing`/`session_store`/`tool_policy`);命令 `/new`(新对话)、`/model <名>`(切模型,opus/sonnet… 经 rtime-models 别名解析)、`/stream on|off`。
- **多模态(M3,真机已验)**:入站**图片/表情包/经典表情/文件**——图片、表情包经 HTTP 从 QQ CDN 下到本机由模型 `Read` 读图理解(Kimi coding 端点经 Read 能读图,实测准确,含读 meme 文字);经典表情映射成文字标签;**文件**因 NapCat 不给 url,经 `get_file` 触发落盘+共享挂载读取(`QQ_NAPCAT_FILE_DIR`),再**桥侧抽文本**喂模型(pypdf/纯文本,**不让模型 Read 二进制 PDF——会渲染成多页图卡死**);**公式/LaTeX 型 PDF 文本提取乱码,降级建议截图**(图能精准读)。出站模型用 `[[rtime-send-image:…]]`/`[[rtime-send-file:…]]` 指令经 OneBot **base64** 段发回(跨 napcat/bridge 容器安全,实测 NapCat 收下)。**视觉不强制路由**(决策3):读不了图的模型不静默丢、提示换模型。语音 STT 单列(orangepi 本地,后续)。
- **流式输出**:即时"⏳思考中"+按段流式答;**默认不显示工具/命令**(`QQ_SHOW_TOOL_CALLS=1` 才显示),真用工具时只一行泛化"🔍 正在查阅资料…"。
- **分级准入硬门**:blocked 优先;admin/allowed 可私聊;公开实例可显式用
  `QQ_PRIVATE_ACCESS` 放开好友/临时会话。空 owner/名单默认拒绝私聊,不会意外 allow-all。
- **正则直答(块5)**:`QQ_DIRECT_RULES` 指向规则 JSON(样例 `ops/direct-rules.example.json`)后,固定问法(班车时刻、FAQ)命中正则**直接模板/数据秒回,不调模型**;班车规则内置 USTC 班车页抓取(消息里的"东区/西区/节假日"等关键词自动换起点/日期,结果缓存 6h);只对过了 owner 门的人生效,规则失败自动回落模型。空=关。
- **brain 检索**:默认 scoped grep(快);`lib_search` 索引检索可一键 opt-in(见下)。
- **聊天存档** `QQ_BRIDGE_ARCHIVE`、**入群审核/防自动入群**(reject 邀请 + 自动退非白名单群)。
- **分级日志**到 stdout(`QQ_DEBUG=1` 出模型命令/工具/错误全栈)。

容器化:镜像 `FROM` 飞书运行镜像(复用 claude+claude-rtime+kimi),`--volumes-from` 继承 .claude/brain/kimi-key;见 `deploy/Dockerfile.m2`,部署细节 docs/channel-unification-plan.zh-CN.md。

**qq 镜像只经 compose 构建,勿裸 `docker build`。** compose(`compose.prod.yml`)在 build 时显式把 `BASE` 传成与镜像 tag 一致的基底(`${RTIME_ASSISTANT_IMAGE:-…/feishu-bridge}:${RTIME_ASSISTANT_IMAGE_TAG:-local}`);裸 `docker build` 会拿 `Dockerfile.m2` 里的默认基底,一旦漂移(历史上的 `orangepi-local`)会缺新 wrapper。基底 tag 以 compose 为准。

### lib_search 索引检索(opt-in)
默认走文件系统 grep(快、简单)。要更好的召回(全库 BM25 排序),设 `QQ_MCP_CONFIG=/qq-state/mcp.json`(镜像已带 gateway+brain-library;挂索引 `-v <brain-index-state-dir>:/brain-index:ro`)。代价:每条消息 +~3-5s(claude 每条 spawn → 重载 jieba + MCP 冷启)。范式 + 实测结论见 docs/channel-unification-plan.zh-CN.md 的「brain 检索接入范式与实测结论」。

## 为什么跑在 orange pi 而不是 Mac

**QQ 登录必须走国内直连。** Mac 的默认出口被 clash TUN 全局接管走**美国节点**
(实测出口 = Los Angeles),从美国 IP 登 QQ 会触发腾讯风控/封号。orange pi 是**合肥
CERNET 国内 IP**、arm64、Docker 现成、还是生产目标——QQ 协议端就放它上面。
**不要去改 Mac 的 clash 全局**(会断掉给 AI 用的美国 relay)。WebUI 经 SSH 隧道到 Mac 扫码。

## 拓扑

```
QQ ── NapCat 容器(--network host, 合肥IP登录, WebUI:6099)
        │ OneBot v11 反向WS  → ws://127.0.0.1:8080/onebot/v11
        │ OneBot HTTP 控制口 ← 127.0.0.1:3000 (列群/退群/发消息)
        ▼
   native bridge (orange pi host python3, setsid -f)  ──reuse──▶ rtime_chat_runtime
```

## 运营(ops 脚本)

脚本 `ops/qqbridge.sh` 跑在 orange pi,配置在 `ops/qqbridge.env`(gitignored,见 `.env.example`)。
从 Mac:`ssh orangepi 'cd ~/qq-bridge-spike/apps/qq-bridge/ops && ./qqbridge.sh <cmd>'`

```
qqbridge up | down              # 起/停 NapCat + bridge
qqbridge napcat-restart         # 重启 NapCat(quick-login,免重扫)
qqbridge bridge-restart         # 重启 bridge(改配置/换代码后)
qqbridge status                 # healthz + 登录 + 群数 + 存档行数
qqbridge qr                     # 拷出登录二维码(首次扫码用)
qqbridge groups | groups-leave-all
qqbridge send <qq> <text>       # 经 OneBot API 发私聊
qqbridge logs [n] | archive [n]
```

### 首次登录(需要你扫码,只此一次)

```
ssh orangepi 'cd ~/qq-bridge-spike/apps/qq-bridge/ops && ./qqbridge.sh up'   # 若无 ACCOUNT 则进扫码
ssh orangepi 'cd ~/qq-bridge-spike/apps/qq-bridge/ops && ./qqbridge.sh qr'   # 拷出二维码
scp orangepi:~/qq-bridge-spike/qrcode.png .                                  # 手机 QQ 扫
```
登录后把 `QQ_ACCOUNT` 填进 `qqbridge.env`,以后 `napcat-restart` 走 **quick-login**(从保存的
会话直接登录,不再扫码)。WebUI 备选:`ssh -fN -L 127.0.0.1:6099:127.0.0.1:6099 orangepi`,
浏览器开 `http://localhost:6099/webui?token=<见 NapCat 日志>`。

## 掉线自愈(自动送码到飞书)

腾讯风控会周期性把小号踢下线(`账号状态变更为离线` / quick-login `身份已失效`),这是第三方
协议端 + 小号的**账号侧风控,不可根治**。`qq_selfheal.py`(systemd 服务 `qq-selfheal.service`)
把恢复流程自动化:轮询 `get_status.online` → 确认掉线满 `SELFHEAL_OFFLINE_CONFIRM_SECONDS`(防抖)
→ `docker restart` NapCat 触发登录 → 若 quick-login 自动回在线就发一条飞书告知;若出了新二维码,
**把二维码图片发到 owner 的飞书**(`FEISHU_OWNER_OPEN_ID`)+ 附解码 URL 兜底。把"人工 SSH 取码"
降级成"飞书里扫一下"。凭据复用飞书桥同一份 `feishu.json`,不依赖飞书桥容器、无第三方 Python 依赖。

```
# 安装(orange pi)
sudo cp ops/qq-selfheal.service /etc/systemd/system/ && sudo systemctl enable --now qq-selfheal
systemctl status qq-selfheal ; journalctl -u qq-selfheal -f     # 看守护
python3 ops/qq_selfheal.py --test-text "hi"   # 测飞书文字投递
python3 ops/qq_selfheal.py --test-send        # 测二维码图片投递(用当前码,别扫)
python3 ops/qq_selfheal.py --once             # 只跑一轮(掉线则执行一次自愈)
python3 ops/qq_selfheal.py --check-qr-request # 只查一次按需补码触发文件(有则取码发+删)
```

配置见 `ops/qqbridge.env`(`SELFHEAL_*` + `FEISHU_OWNER_OPEN_ID`)。

### 按需补码(不用等掉线,飞书发一句话即取最新登录码)

不想等自动掉线检测、也不想 SSH:在**飞书私聊**给助手发 **`补码`**(或 `qq码` / `qq二维码` /
`/qqcode`,大小写空格宽松;普通文字或飞书富文本 `post` 都可)即可。飞书桥不把它交给模型,而是往一个共享触发文件写一条请求,本守护
每 `SELFHEAL_QR_REQUEST_CHECK_SECONDS`(默认 4s)轮询到就 `docker cp` 出当前最新 `qrcode.png`
回推你的飞书(与掉线自愈同一条投递链路)。

触发文件是飞书桥容器(无 docker 访问)与本守护(有 docker)之间的共享文件信号,两端 env 必须指向
**同一物理文件**(compose 把 host `~/.local/state/rtime-assistant` 挂到容器 `/var/lib/rtime-assistant`):

| 端 | env | 路径 |
| --- | --- | --- |
| 飞书桥(容器,写) | `RTIME_QQ_QR_REQUEST_FILE` | `/var/lib/rtime-assistant/qq-qr-request` |
| 本守护(host,读) | `SELFHEAL_QR_REQUEST_FILE` | `~/.local/state/rtime-assistant/qq-qr-request` |

只有 owner(飞书桥 `ADMIN_USERS`)能触发;去抖靠触发文件 mtime,发送失败会回一条飞书告警而不重复轰炸。

## 防封加固(NapCat 配置)

- **WebUI 6099 锁 127.0.0.1**:`state/napcat/config/webui.json` 的 `host` 设 `127.0.0.1`(默认 `::`
  = 全网卡,LAN/Tailnet 可达;暴露的弱口令 WebUI 被扫是真实的连带封号链路)。SSH 隧道扫码不受影响。
- **关 o3HookMode**:`state/napcat/config/napcat.json`(及 `napcat_<QQ>.json`)`o3HookMode` 设 `0`,
  NapCat 官方安全页建议以降低被识别为外挂的概率。关掉后建议自测多模态(图片/文件/语音)仍正常。
- **不要让 NapCat 继承宿主机代理**:QQ 登录与长连接必须走 Orange Pi 国内直连。生产 compose 和
  `ops/qqbridge.sh` 会显式清空 `HTTP_PROXY`/`HTTPS_PROXY`/`ALL_PROXY` 等变量;排障时先确认
  `docker exec qqbr-napcat env | grep -i proxy` 没有指向 `127.0.0.1:7890` 之类宿主机代理。
- 专用小号、国内直连、持久卷(`state/napcat/QQ`)完整、宿主机 NTP 同步,是降低触发频率的基础前提。

## 本地测试(无需账号)

```bash
.venv/bin/python -m pytest apps/qq-bridge/tests -q
```
覆盖:CQ 码文本提取、事件解析+身份映射、完整反向 WS 回环(假 NapCat:owner echo / 陌生人
被拒 / `/healthz`)、入群审核(邀请 reject / 非白名单自动退 / 白名单保留)、存档器。

## 结构

```
qq_bridge/
  config.py      # QQBridgeConfig.from_env():owner、ws、archive、invite policy、group allowlist
  app.py         # echo handler + build_request_handler(入群审核) + build_notice_handler(自动退群)
  archive.py     # 原始事件落 JSONL
  __main__.py    # python -m qq_bridge
  onebot/{cqcode,protocol,ws_server}.py   # CQ码、事件解析、aiohttp 反向WS服务端 + /healthz
ops/             # qqbridge.sh 运营脚本 + qqbridge.env.example
tests/
deploy/          # compose.spike.yml 等(早期 compose 方案,实际用 ops 脚本的原生方案)
```

复用:`packages/rtime-chat-runtime`(import 直接用)。本 app 不进 uv workspace,运行靠
路径 bootstrap(`qq_bridge/_runtime_path.py`,同飞书桥 `_shared_runtime.py`)。

## 配置(环境变量)

| 变量 | 默认 | 说明 |
|---|---|---|
| `QQ_OWNER_IDS` | (空) | owner/admin 默认来源;私聊不会因空值 allow-all |
| `QQ_ALLOWED_USERS` | (空) | 普通私聊白名单:可问但不能用 admin 命令/自选模型 |
| `QQ_PRIVATE_ACCESS` | `admin_allowed` | `admin_allowed` / `friends` / `friends_and_temporary`;只放开好友/临时会话提问,不自动通过好友请求 |
| `QQ_GROUP_REPLY_AT_SENDER` | `0` | 群聊文本回复是否在开头 @ 提问者;公开答疑实例可设 `1` |
| `QQ_BRIDGE_WS_PORT` | 8080 | 反向 WS 端口(NapCat 连这里) |
| `QQ_ONEBOT_ACCESS_TOKEN` | (空=不校验) | 与 NapCat 端一致 |
| `QQ_BRIDGE_ARCHIVE` | (空=关) | 原始事件 JSONL 路径 |
| `QQ_GROUP_INVITE_POLICY` | reject | reject / allow / owner |
| `QQ_GROUP_ALLOWLIST` | (空) | 允许停留的群;空=进任何群即退 |
| `QQ_DIRECT_RULES` | (空=关) | 正则直答规则 JSON 路径(样例 `ops/direct-rules.example.json`);命中不调模型 |
| `QQ_REPLAY_GRACE_SECONDS` | 5 | 重连补推保护:OneBot `time` 早于当前 WS 连接超过 N 秒的消息只归档、不回复、不跑模型;0=关 |
| `QQ_SUPPRESS_SENDS_WHEN_OFFLINE` | 1 | 心跳显示账号离线后,丢弃后续 send/upload 动作,避免离线后继续 `sendMsg` 超时 |

## 后续(见设计档 M2–M5)

M2 私聊接 Claude+brain;M3 图片/文件;M4 会话/命令对齐 + 抽 `ChannelDisplay` 端口与飞书桥共用;
M5 Docker/systemd 生产化 + 健康门(`claude` CLI 与 brain MCP 须装进容器内)。
