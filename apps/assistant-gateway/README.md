# assistant-gateway

Obsidian侧边栏助手插件后端。stdlib零依赖（Python≥3.9），运行在orangepi，绑定Tailscale地址。默认只读；在受信任的本机/私有网络场景可显式设置`GATEWAY_ACCESS_MODE=full`进入写入/整理模式。

## 工作方式

```text
插件POST /api/obsidian/prepare (AssistantRequestBody schema_version=1，可选)
  -> 解析当前文件/PDF页码、解锁brain路径、关系/记忆轻量预取
  -> 当 options.prewarm_model=true 时，后台发起模型预热；prepare立即返回
  -> 返回短TTL prepare_id，后续chat可带prepare_id复用解锁结果

插件POST /api/obsidian/chat (AssistantRequestBody schema_version=1)
  -> 从note.text的frontmatter取source/page_image_dir/raw_text_dir等brain相对路径
     或当请求带有效prepare_id时复用prepare缓存的解锁清单
  -> safe_brain_path校验：必须落在BRAIN_ROOT内、存在、且不在personal-data/
  -> 组prompt（任务模式提示+解锁清单+记忆/关系预取+索引查询命令+选区+笔记+用户消息）
  -> subprocess claude-kimi -p --permission-mode dontAsk
       --allowedTools "Read,Glob,Grep,WebSearch,WebFetch,Bash(...brain_library index query...),Bash(rtime-web-fetch *)"
       cwd=BRAIN_ROOT，超时CLAUDE_TIMEOUT
  -> 返回 {"answer", "sources"}（解析回答末尾"来源："块为sources数组）
```

设计要点：路径桥梁是frontmatter（brain相对路径），不维护vault↔brain映射表；并发=1（短FIFO排队）；请求日志只记元数据不记正文（`GATEWAY_LOG_DIR/requests.jsonl`）；插件不保存模型 provider key，模型目录刷新和真实执行都在后端。

## 部署（orangepi）

```bash
# git pull后：
cp deploy/env/assistant-gateway.env.example ~/.config/rtime-assistant/gateway.env  # 按机器改路径
cp deploy/systemd/user/assistant-gateway.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now assistant-gateway
curl http://127.0.0.1:8765/healthz   # 期望 ok
```

插件侧（vault `.obsidian/plugins/rtime-assistant/data.json`）：`chatEndpoint=http://<gateway-host>:8765/api/obsidian/chat`、`healthEndpoint=.../healthz`、`requestTimeoutMs=120000`、建议`maxNoteChars=20000`。

回滚：`systemctl --user disable --now assistant-gateway`，插件endpoint改回本地模拟器。

## 测试

```bash
python3 -m pytest tests/test_assistant_gateway.py -q   # gateway contract tests
# HTTP层冒烟：CLAUDE_BIN指向假脚本起服务，curl healthz与chat（见git历史中的冒烟示例）
```

## v0.2升级（2026-06-11，待run-08部署）

1. **PDF感知**：活动文件是PDF时，经`_indexes/pdf-manifest.jsonl`按basename解析brain正本，自动解锁PDF+同名伴生md+页图目录+诊断层——vault符号链接布局无需映射表。请求可带`context.pdf.page`（当前页码），提示词会引导模型先读该页页图。
2. **SSE流式**：请求体加`"stream": true`（或Accept: text/event-stream）→ `text/event-stream`响应，事件帧`data: {json}\n\n`：

```text
{"type":"status","text":"正在使用Read…"}     工具调用进度
{"type":"delta","text":"…"}                  增量文本
{"type":"done","answer":"…","sources":[…],"trace":{…},"memory_events":{…}}   定稿
{"type":"error","message":"…"}               错误
```

底层用`claude --output-format stream-json --include-partial-messages`，解析器同时兼容token级与整消息两种事件形态（CLI版本差异安全）。
3. **CORS**：全部响应带`Access-Control-Allow-Origin: *`+OPTIONS预检——插件可用`window.fetch`消费流（Obsidian的requestUrl不支持流式）。安全边界仍是Tailscale网络。
4. **提速**：工具轮次不设上限（由`CLAUDE_TIMEOUT`兜底，不再用`--max-turns`截断多文件答案）；提示词加入"无需查文件则直接回答"。非流式路径完全不变（旧插件兼容）。

## 命令行客户端 rtime-chat（agent测试接口，2026-06-12）

`apps/assistant-gateway/rtime_chat.py`，stdlib零依赖，与插件同contract。任何agent（Claude Code/Codex/kimi/qwen/deepseek）或用户不开Obsidian即可对话网关、做e2e断言：

```bash
python3 apps/assistant-gateway/rtime_chat.py --health                    # ok
python3 apps/assistant-gateway/rtime_chat.py "固体物理的能带怎么理解"
python3 apps/assistant-gateway/rtime_chat.py --pdf lesson2-main.pdf --page 5 --stream "这页讲什么"
python3 apps/assistant-gateway/rtime_chat.py --note <vault笔记路径> --task summarize --json
```

- `--stream`：SSE流式，status事件进stderr（`[正在使用Read…]`）、delta实时出stdout。
- `--json`：输出最终`{"answer","sources"}`单行JSON，脚本断言用；HTTP错误时也输出payload并退出码1。
- `--task`：ask/summarize/explain/related/citation-review。
- endpoint默认`http://127.0.0.1:8765`，环境变量`RTIME_GATEWAY_URL`覆盖；客户端**绝不走系统HTTP代理**（Mac的127.0.0.1:7890会把Tailscale网段代理出去导致502）。
- 请求带`"entry":"rtime-chat"`，记忆采集journal会标注入口来源。

实测（2026-06-12凌晨，orangepi）：healthz ok；非流式问答27.7秒返回正确JSON；流式可见工具状态事件与增量文本。单测11项（请求体构造+SSE分帧解析+流消费三态），无网络依赖。

## run-09只读工具入口

这些入口给 agent/脚本使用，不替代 Obsidian UI，也不写 `brain`、vault 或 Zotero：

```bash
# vault/brain路径解析：经brain _indexes/pdf-manifest.jsonl 找正本、伴生md、页图目录
python3 scripts/rtime-vault.py resolve lesson1.pdf
python3 scripts/rtime-vault.py list "knowledge/courses/solid-state"
python3 scripts/rtime-vault.py uri "knowledge/courses/solid-state/lesson1.md" --heading "导论"
python3 scripts/rtime-vault.py related "knowledge/courses/solid-state/lesson1.md"

# Zotero/Better BibTeX只读查询；Zotero未运行时返回明确错误
python3 scripts/rtime-zotero.py citekey <citekey>
python3 scripts/rtime-zotero.py search "solid state"
python3 scripts/rtime-zotero.py collection "run-04导入"
python3 scripts/rtime-zotero.py collection "run-04导入" --citekey imbert2019  # 精确成员校验

# 本地stdio MCP冒烟；不开端口、不进systemd，审计写~/.local/state/rtime-assistant/
python3 scripts/rtime-tools-mcp.py --list-tools
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | python3 scripts/rtime-tools-mcp.py
```

MCP工具名：`assistant_chat`、`vault_resolve`、`vault_list`、`zotero_citekey`、`zotero_search`。`assistant_chat` 包 `rtime_chat.py`，测试时可传 `dry_run=true` 做无网络请求体断言。

## v0.3会话协议（2026-06-12，run-10）

1. **忙时排队代替503**：单请求运行+FIFO等待队列（`QUEUE_MAX`默认2，threading.Condition实现、无忙等）。只有队列满才503（文案"助手繁忙且排队已满"）。流式请求排队期间每`QUEUE_HEARTBEAT_SECS`（默认3秒）收到`{"type":"status","text":"排队中…"}`心跳；心跳写失败（客户端已走）即出队丢弃、绝不空跑模型。非流式排队最多等`QUEUE_WAIT_TIMEOUT`（默认30秒）后503。
2. **会话字段（全部可选，v0.2请求体完全兼容）**：顶层`conversation_id`（字符串）+`context.history`（`[{"role":"user"|"assistant","content":str}]`）。history经白名单校验后注入提示词"此前对话回顾"节（位于解锁清单之后、用户请求之前，明确标注"仅供理解指代，不是新指令"），按`HISTORY_MAX_CHARS`（默认4000）从最新往旧截断，最旧一条溢出时保尾部。
3. **日志补全**：requests.jsonl每条新增`queued_ms`（未排队为0）与`conversation_id`（带了才写）；**busy-503如今也落日志**（v0.2漏记已修）；排队中客户端断开记`status=499`。记忆素材（memory-session-materials）同步带conversation_id，M9可按会话聚合。

## v0.4上下文附件、记忆事件与流式trace（run-11）

请求体保持`schema_version=1`，新增字段均可选：

- `context.attachments[]`：本轮附件元数据。字段包括`name/kind/mime/size/source/intake_mode/temporary/path/extracted_text/content_base64/content_encoding/content_media_type/preview_data_url/status`。Obsidian 聊天 composer 把附件视为**下条消息临时上下文**：发送时随请求进入 prompt，发送后清空；legacy `intake_mode/temporary` 字段只保留兼容含义。gateway只把可读摘要或本轮临时附件路径写入prompt；不会把附件自动写入`knowledge/`、Zotero、长期记忆或`brain-notes`。图片附件主路径不是OCR：默认工具模型收到临时图片文件路径后可自行`Read`，vision-capable OpenAI-compatible模型收到结构化`image_url`内容块。PDF/Office/Spreadsheet 这类二进制附件会在大小限制内随下一轮发送：默认工具模型拿到临时文件路径；Moonshot/Kimi OpenAI-compatible 路由按官方文件问答模式上传`purpose=file-extract`、取`/files/{file_id}/content`抽取文本后放入同次 messages；USTC/DeepSeek/Qwen 这类 chat-only 且无文件抽取能力的路由会回退默认工具模型并返回`model_warning`。ZIP/压缩包默认不作为聊天附件发送，需走显式解包/入库流程。
- `context.memory`：本轮记忆意图，例如`commands=["remember"]`或`disabled=true`。gateway会返回`memory_events`摘要；明确记忆意图只写`brain/memory/review-queue/`候选，不直接合并长期`memory/cards/`。
- `options.trace_stream=true`：流式请求返回`trace`，包含`request_received/queue_acquired/claude_spawned/first_stdout_event/first_sse_emit/done_emit/process_exit`等时间戳。插件再叠加Mac侧`first_chunk_received/first_delta_parsed/first_dom_painted/done_received`，用于判断真流式和慢点拆因。
- `options.permission_mode`：本轮 Claude Code 权限/批准策略入口。允许值为`dontAsk/default/acceptEdits/plan/bypassPermissions`；gateway会把它作为`--permission-mode`传给默认工具模型和 Claude-wrapper/Anthropic-compatible runner。chat-only OpenAI-compatible 路由没有 Claude Code 批准回调；需要本地工具时应回退默认Claude runner，而不是假装chat-only模型有工具权限。
- `options.approval_forwarding`：是否把 runner 发出的 permission/approval request 事件转成 SSE `approval_request` 状态给插件显示。gateway只转发状态，不在插件侧自动批准。

默认工具模型的 allowlist 包含 `Read`、`Glob`、`Grep`、只读
`brain_library index query`、Claude Code 内置 `WebSearch`/`WebFetch`，以及
受控只读 fallback `Bash(rtime-web-fetch *)`。公开网页搜索类请求会走真实
模型工具链，由模型自主选择 `WebSearch`/`WebFetch` 或
`rtime-web-fetch search "<查询词>"` / `rtime-web-fetch url <公开URL>`；gateway
不预取网页结果，也不把工具调用替换成快答。
`GATEWAY_WEB_TOOLS_ENABLED=0` 可关闭网络工具；`GATEWAY_EXTRA_ALLOWED_TOOLS`
可追加 live runner 已安装的 MCP/浏览器工具名或 glob，例如
`mcp__browser__*,mcp__playwright__*`。仅把工具名加入 allowlist 不会安装
对应 MCP server 或浏览器运行时。公开网页/搜索请求会走 `budget_profile=web`，
默认 `CLAUDE_WEB_TIMEOUT=170`，给搜索/抓取留足时间（工具轮次不设上限）。

gateway 默认给 Claude Code 加 `--bare`、`--no-session-persistence` 和
`--exclude-dynamic-system-prompt-sections` 来减少每次 `claude -p` 的启动
税。线上 2026-06-15 A/B 短请求显示，首个 stream-json 事件从约 2.1s 降到
约 0.9s，总耗时从约 4.7-6.3s 降到约 3.8-4.0s。若
`GATEWAY_EXTRA_ALLOWED_TOOLS` 包含 `mcp__` 工具名，gateway 会自动跳过
`--bare`，避免关闭依赖用户/项目 MCP discovery 的浏览器或远控工具。

当`GATEWAY_ACCESS_MODE=full`时，gateway会强制使用`permission_mode=bypassPermissions`，默认工具模型改用 full-access allowlist：`Read,Write,Edit,MultiEdit,Glob,Grep,WebSearch,WebFetch,Bash(*)`。这允许用户明确要求的资料整理、入库、README/manifest/伴生md更新和索引命令执行；prompt仍要求原件不覆盖不删除、批量/新分类/重名异内容/疑似高敏内容先确认。

私有插件发布文件可放在`GATEWAY_PLUGIN_RELEASE_DIR`，gateway会通过
`/api/obsidian/plugin-release/`服务`release.json`、`manifest.json`、
`main.js`和`styles.css`四个白名单文件。Obsidian插件的私有更新地址可填：

```text
http://<gateway-host>:8765/api/obsidian/plugin-release/
```

客户端续聊（agent回归测试）：

```bash
python3 apps/assistant-gateway/rtime_chat.py --conversation conv-1 \
  --history-file 历史.json "它的低温极限行为是什么"
# 历史.json：[{"role":"user","content":"什么是德拜模型？"},{"role":"assistant","content":"…"}]
```

## v0.5 Obsidian入库入口（run-18）

新增入口：

```text
POST /api/obsidian/intake
```

请求体仍为`schema_version=1`。注意：当前 Obsidian 聊天附件 chip **不再暴露入库按钮**；聊天附件只用于下条消息上下文。此入口保留给独立资料整理命令、未来专用 intake UI，或其他明确入库的 entry adapter 使用。

```json
{
  "schema_version": 1,
  "source": "obsidian",
  "name": "lecture.pdf",
  "content_base64": "...",
  "privacy_hint": "",
  "target_hint": ""
}
```

处理边界：

- 只写`brain/_inbox/<source>/<date>/`和同名`.intake.json` ticket；
- 复用`scripts/brain-intake/intake_ticket.py`的分类和ticket schema；
- 不写最终`knowledge/`、Zotero、长期记忆或`brain-notes`正文；
- `privacy_hint=personal`或`decision=hold-*`时只发确认通知，归位仍等用户确认；
- `requests.jsonl`只记endpoint/status/duration/size/sha8/class/decision/notify等元数据，不记录文件名、正文或base64。

## v0.6 模型目录、记忆注入与关系预取（run-14/15/17/19）

新增模型目录入口：

```text
GET  /api/obsidian/models
POST /api/obsidian/models/refresh
```

返回值只包含非敏感目录：provider、protocol、models、capabilities、last_refreshed、errors。它不会返回 token、keyfile 路径或 secret。Obsidian 请求可在 `options` 中附带：

```json
{
  "model_provider_id": "moonshot-openai",
  "model_id": "kimi-k2.7-code",
  "model_protocol": "openai-chat"
}
```

后端只接受缓存目录白名单里的 provider/model/protocol。非法选择会回退 gateway 默认模型并返回明确 `model_warning`，不会把用户输入拼成任意 CLI 参数。

Provider 协议分三类：

- `claude-wrapper/agent-tools`：Claude Code wrapper 路径，可用 Read/Glob/Grep/Bash 索引工具；
- `anthropic-compatible`：保留给 Anthropic-compatible wrapper；
- `openai-chat`：OpenAI-compatible chat API，不带 Claude Code 工具。Moonshot/Kimi 使用 `https://api.moonshot.ai/v1` 和 `GET /models` 刷新；`kimi-k2.7-code` 标记为 code/chat/long-context/thinking required，不作为快答默认替代品。Moonshot/Kimi 能力目录标记`vision`与`file_extract`：图片走结构化`image_url`，PDF/PPT/DOC/XLS/CSV/文本等文档走`/files`上传+内容抽取。USTC 先用 `RTIME_USTC_MODELS` 静态目录，若 `/models` 可用再刷新；未显式设置 `RTIME_USTC_API_KEY_FILE` 时会读取标准路径 `~/.config/rtime-assistant/ustc-api-key`。USTC/DeepSeek/Qwen chat-only 路由不声明文件抽取或视觉能力，不能直接消费图片/PDF/PPT/ZIP。

当本轮需要本地资料/文件工具（例如查重、扫描目录、读取当前 PDF/课件解锁文件），或附件需要视觉/文件抽取而所选 `openai-chat` 模型不具备相应能力时，gateway 会回退默认工具模型并返回 `model_warning`。这样 chat-only 路线不会误接管需要 Read/Glob/Grep/Bash、视觉或文件抽取的任务。

Prompt 组装新增两类可控预取：

- 已批准记忆：`MEMORY_INJECTION_ENABLED=1` 时从 `brain/memory/cards/` 检索 `type: memory-card` 且 `sensitivity=normal`、非 inferred、未过期的卡片，按本轮问题/历史/文件轻量排序，注入预算内的“关于用户的已批准记忆”节。候选/review 队列绝不注入。
- 动态上下文源：`GATEWAY_CONTEXT_SOURCES_ENABLED=1` 时读取 `brain/_system/rtime-context-sources.jsonl`。每条 source 仅登记 `id/status/kind/title/path/tags/priority/active_from/expires/max_chars` 等 metadata；gateway 只读取 `active`、未过期、通过 `safe_brain_path` 且不在 `personal-data/` 的 brain 相对路径，并按本轮消息、任务模式、当前文件、选区、历史和 tags 排序注入。请求日志只记录 source id/path/kind，不记录 source 正文。
- 记忆候选：明确“记住/调整偏好/记忆这个”或 Obsidian `commands=["remember"]` 只写 review-queue 候选；疑似 token/open_id/验证码/证件/银行卡等敏感内容会 hold，不写文件。长期 `memory/cards/` 仍必须走审核流程或用户明确打开 full-access。
- 相关材料：读取派生 `_indexes/relations.jsonl`，在 `related`/`citation-review` 或有明确解锁资料时预取 top-k 关系，帮助模型少走现场 Grep/Read 回合。

相关环境变量见 `deploy/env/assistant-gateway.env.example`。旧请求体不带模型/记忆字段时仍兼容。

## v0.7 Obsidian prepare 与流式截断保护（2026-06-15）

新增入口：

```text
POST /api/obsidian/prepare
```

该入口默认只做低成本上下文准备；当插件随请求发送 `options.prewarm_model=true`
且后端 `GATEWAY_PREWARM_ENABLED=1` 时，会后台启动一次模型预热：

- PDF 活动文件经 `_indexes/pdf-manifest.jsonl` 解出 brain 正本、伴生 md、页图目录和文本层；
- Markdown 活动文件仍按 frontmatter `source/page_image_dir/raw_text_dir` 解锁；
- 读取已批准记忆命中数量、关系预取数量和模型目录状态；
- 返回 `prepare_id`、`cache_ttl_seconds`、`unlock_count`、`related_count`、`prewarm_status` 等元数据；
- 写 `requests.jsonl` 的 `endpoint=prepare` 记录，但不记录用户正文或消息。

预热不会阻塞 prepare 响应，也不进入主聊天 FIFO 队列。gateway 会按实际路由执行同一套模型能力判断：需要本地资料/附件工具时仍回退默认工具模型；Kimi/USTC 这类 OpenAI-compatible chat 路由只做短 chat 调用。对默认工具模型或 Claude-wrapper 路由，`GATEWAY_LIVE_PREWARM_ENABLED=1` 时不再发送“OK”预热 prompt，而是提前启动一个空白 `--input-format stream-json` CLI 进程等待 stdin；下一次匹配的聊天把真实 prompt 写入该进程，拿到本轮 `result` 后立即终止并后台补一个新的空白进程。这样不会降低模型、工具、轮次或推理质量，也不会复用上一轮对话状态。`GATEWAY_LIVE_PREWARM_IDLE_SECONDS` 控制空白热进程最长等待时间；设 `GATEWAY_LIVE_PREWARM_ENABLED=0` 可回退旧短调用预热。预热完成或 live 进程启动后另写 `endpoint=prewarm` 元数据日志（耗时、模型、状态、错误类型），不写 prompt、消息正文或密钥。

后续 `/api/obsidian/chat` 可带顶层 `prepare_id`。如果上下文 key（活动路径、mtime、PDF 页码、模式、目标模块、模型选择）仍匹配，gateway 复用 prepare 的解锁清单；过期或不匹配则自动重新解析，不影响聊天。

流式路径增加完整性保护：如果 `claude --output-format stream-json` 的 `result` 帧为 `error_*`/`is_error=true`，或工具调用后没有任何最终文本，gateway 返回 SSE `{"type":"error","code":"incomplete_answer",...}` 并按 502 记账。这样不会再把“先扫描PDF/目录”这类工具前计划句当成正常答案保存。

## v0.8 多文件调查预算（2026-06-15）

工具轮次默认不设上限（由 `CLAUDE_TIMEOUT` 兜底）；budget profile 只决定单次请求的**超时**与日志标签。默认问答走 `fast` profile（`ask/summarize/explain`），`related/citation-review` 走 `deep` profile。需要时可在插件设置「工具轮次上限」或 env `CLAUDE_MAX_TURNS` 显式设上限（默认 0/空=不限）。

当请求文本、活动文件或附件名命中“查重/去重/重复讲稿/扫描PDF/遍历课件目录/对比文件”等多文件调查意图时，gateway 提高该请求的超时（轮次仍不设上限）：

```text
CLAUDE_INVESTIGATION_TIMEOUT=180
```

同时 prompt 会要求模型先列候选文件、分批读取标题/目录/关键页，并输出可核对的候选表，避免只返回“继续扫描/准备查找”这类计划句。`requests.jsonl` 会记录 `budget_profile`，用于确认线上某次请求走了 `fast/deep/investigation/web` 哪条路由。

运行错误追问进入专门的模型诊断路径：当插件传入
`context.runtime.last_error` 且用户问题像“刚刚为什么报错/这个错误怎么回事”
时，gateway 不解锁当前 PDF/课件资料，而是从 `requests.jsonl` 提取脱敏
诊断证据包（状态码、耗时、预算 profile 等），再调用模型分析。
日志记录 `budget_profile=runtime-diagnosis`。这避免了“解释错误原因”再次
扫描课件，同时保留模型基于运行证据自行研判的能力。

## v0.9 单次 live stdin 预热（2026-06-16）

启动慢的主因不是 Tailscale、排队或 Orange Pi 负载，而是每次聊天重复支付
Claude Code/Kimi wrapper 初始化、provider 首响和提示词 prefill。旧的
prepare 短预热调用只能验证 wrapper/provider/key 路径，不能让下一轮聊天复用
已初始化进程。

v0.9 的预热策略是：

- `prepare` 阶段提前启动一个空白 `claude-kimi -p --input-format stream-json --output-format stream-json` 进程；
- 不向预热进程发送任何假 prompt，直到真实聊天到来；
- 首个匹配聊天把原始完整 prompt 写入 stdin，并照常保留 `Read/Glob/Grep/Bash/WebSearch/WebFetch` 工具 allowlist、permission mode 和模型选择（工具轮次不设上限）；
- 收到本轮 `result` 后立刻终止该进程并后台补位，避免跨请求共享会话状态；
- `trace_ms` 记录 `live_prewarm_claimed_ms` 和 `live_prewarm_age_ms`，线上可直接确认是否命中。

本地/Orange Pi 协议验证显示：同一 live stdin 进程内第二个短请求的首个
stream-json 事件可从约 0.875s 降到约 0.017s；空白进程先闲置 2 秒后再发首
prompt，首个事件约 0.042s。总耗时仍取决于模型思考、工具轮次和回答长度，
因此该优化只承诺减少“启动税”，不通过降级模型、减少工具或压低质量换速度。

## 边界

默认只读：白名单外工具不放行；不读personal-data/（路径校验+提示词双重）；模型key只在orangepi的claude-kimi keyfile；不与live桥共享端口/会话；v1无认证头，安全边界=Tailscale网络（插件支持自定义header后再加token）。full-access只能在 owner 明确授权的私有入口启用，并保持入库确认规则。
