# 接自己的X:可选模块接入教程(K6)

给自部署 rtime-assistant 的用户:每个可选模块怎么接**你自己的**资源(QQ小号/飞书应用/
OB vault/公众号订阅…)。通用流程都是三步——`python3 deploy/setup-wizard.py init
--instance <目录> --modules <id>` 装机(依赖自动带上,见 reference/setup-wizard.zh-CN.md)
→ 按本篇该模块一节补配置 → 验证。模块全清单与装态看面板"模块"页或
`python3 deploy/setup-wizard.py list`。

数据边界(所有模块同一条硬规矩):你的登录态/凭据/聊天内容/笔记全部落在你自己的
实例目录与配置文件,永不进代码仓库;凭据只放宿主机文件或 env。

Syncthing 同步(integration-sync)的接入教程在 docs/reference/sync-integration.zh-CN.md §三。


## 接自己的QQ机器人小号(channel-qq)

让用户在QQ里私聊你的助手实例。协议端用NapCat(OneBot v11,容器镜像已在compose里钉死digest),桥接服务`qq-bridge`经反向WebSocket收发消息。

### 前提(你要自备的资源)

- 一个**专用QQ小号**。不要用主号:第三方协议端叠加腾讯风控,存在周期性踢下线甚至封号的风险,风险由这个小号独立承担。
- 一台**国内直连网络**的Linux主机(装好Docker与Docker Compose)。**QQ登录与长连接必须直连,不能走代理/海外出口**——从海外IP登QQ会触发腾讯风控。compose已在NapCat容器里显式清空`HTTP_PROXY`/`HTTPS_PROXY`/`ALL_PROXY`等变量,你也不要在override里加回去。
- 手机QQ(小号已登录),用于首次扫码。
- 核心模块无需单独装:channel-qq声明了`depends_on: core-config, gateway-core`,向导会自动带上依赖闭包。

数据边界:小号登录态、会话、聊天存档全部落在你自己的实例状态目录(`qq-state/`等,见`deploy/modules.json`的`data_paths`),不会进git仓库。

### 装机

在仓库根目录跑安装向导(注意`--instance`是必填的):

```bash
python3 deploy/setup-wizard.py init --instance <实例目录> --modules channel-qq
```

向导产出`<实例目录>/.env`(内含`COMPOSE_PROFILES=qq`与各模块装配要点注释)和`compose.override.yml`骨架。然后起服务:

```bash
docker compose -f compose.prod.yml -f <实例目录>/compose.override.yml \
  --env-file <实例目录>/.env -p rtime-<名字> up -d
```

QQ相关服务(`qqbr-napcat`、`qq-bridge`)都挂在compose profile `qq`下,`COMPOSE_PROFILES`里没有`qq`就不会启动。整体实例流程见`docs/instance-deploy.zh-CN.md`。

### 配置

完整字段表(env名/默认值/热生效)以`docs/config/qq-bridge.md`为准,这里只列必配项,写进实例`.env`:

- `QQ_OWNER_IDS`:owner的QQ号(逗号/空格分隔)。私聊硬门基线兼默认admin集;留空**不会**变成对所有人开放,而是默认拒绝私聊。
- `QQ_ADMIN_IDS` / `QQ_ALLOWED_USERS` / `QQ_BLOCKED_USERS` / `QQ_PRIVATE_ACCESS`:分级准入——admin私聊全功能;普通白名单可问不可用命令;黑名单最高优先级;`QQ_PRIVATE_ACCESS`默认`admin_allowed`,公开实例可放宽为`friends`。
- `QQ_ACCOUNT`:首次扫码登录成功后填入小号QQ号,之后NapCat重启走quick-login,免重扫。
- `QQ_ONEBOT_ACCESS_TOKEN`:桥与NapCat共享的鉴权token,建议设置(空=不校验)。
- **宿主机路径覆盖**:`compose.prod.yml`里`QQ_NAPCAT_QQ_DIR`、`QQ_NAPCAT_CONFIG_DIR`、`QQ_STATE_DIR`等bind源路径的默认值是维护者机器的路径,自部署必须在`.env`里改成自己的目录(NapCat登录态必须落持久卷,否则每次重启都要重扫码)。
- 直答正则(可选):`QQ_DIRECT_RULES`指向规则JSON后,固定问法命中正则直接秒回、不调模型;样例见`apps/qq-bridge/ops/direct-rules.example.json`。若用git内profile(`RTIME_PROFILE`,经`QQ_RTIME_PROFILE`设置),规则放`profiles/<id>/direct-rules.json`。

### 验证

1. 取登录二维码:`docker compose -p rtime-<名字> logs qqbr-napcat`在日志尾部找二维码,用小号手机QQ扫;或本机浏览器开NapCat WebUI `http://127.0.0.1:6099/webui?token=<见NapCat日志>`(WebUI只应监听127.0.0.1,远程机器经SSH隧道访问)。
2. 桥健康检查(两容器都是host网络):

```bash
curl http://127.0.0.1:8080/healthz
```

   返回200即桥存活;`/healthz`同时探测反向WS已连上、小号在线。
3. 用`QQ_OWNER_IDS`里的QQ号给小号发一条私聊,收到助手回复即链路全通;非白名单号应被拒。
4. 防风控自检:确认NapCat容器内没有继承宿主机代理——

```bash
docker compose -p rtime-<名字> exec qqbr-napcat env | grep -i proxy
```

   不应出现指向`127.0.0.1:7890`之类的宿主代理。掉线自愈与按需补码见集成模块integration-qq-selfheal;开发细节见`docs/qq-bridge-development.zh-CN.md`。

## 接自己的飞书机器人(channel-feishu)

### 前提
自备一个飞书企业自建应用:在飞书开放平台创建应用,拿到app_id与app_secret;开通机器人能力;事件订阅选"长连接"方式,并订阅"接收消息"事件(im.message.receive_v1)。桥通过WebSocket长连接收消息,不需要公网回调地址。另准备使用者的open_id(ou_开头,可在开放平台调试工具查到)用于白名单。

### 装机
```bash
python3 deploy/setup-wizard.py init --instance <实例目录> --modules channel-feishu
```
feishu-bridge是base服务,init后随compose直接启动;不接飞书时把FEISHU_*留空即可。

### 配置
全量字段表见docs/config/feishu.md,字段说明见docs/reference/feishu-config.zh-CN.md,env模板见deploy/env/feishu-bridge.prod.env.example。最少要配:
- 凭据,二选一:env设FEISHU_APP_ID/FEISHU_APP_SECRET;或写进FEISHU_CONFIG_JSON指向的JSON文件(默认~/.config/rtime-assistant/feishu.json,键appId/appSecret),compose会把它只读挂进容器。凭据只放宿主机文件,绝不提交进仓库。
- 白名单:ALLOWED_USERS=逗号分隔的私聊open_id;ALLOWED_CHATS=群chat_id;ADMIN_USERS留空回落ALLOWED_USERS。注意:两个白名单全空=对所有人开放,自部署务必先填ALLOWED_USERS。
- 群聊默认需@机器人才应答(REQUIRE_MENTION_IN_GROUP=1)。

面板(admin-core)已注册feishu模块字段,但当前桥运行时仍从env取值(见参考文档§三),以env为准。数据边界:会话状态存实例目录feishu-state/,聊天归档默认关闭(FEISHU_ARCHIVE_ROOT未设=不落盘),你的聊天内容与凭据都留在自己机器,不进代码仓库。

### 验证
```bash
docker compose -f compose.prod.yml -f <实例目录>/compose.override.yml \
  --env-file <实例目录>/.env -p rtime-<实例名> up -d
curl http://127.0.0.1:9981/healthz
```
healthz返回ok且容器日志出现"连接飞书 WebSocket 长连接"即已连上;给机器人发一条私聊消息应收到回复。没回复时看日志:出现"[access] ignored user=..."说明发送者open_id不在ALLOWED_USERS。

## 接自己的网页问答(channel-web)

浏览器问答入口:一个静态页面加JSON-over-SSE聊天接口,每一轮都走与QQ/飞书桥相同的共享运行时(ToolPolicy/SessionStore/run_log/统一模型runner),不直连模型API。真相源:`apps/web-chat/README.md`(架构与端点)、`docs/config/web-chat.md`(字段↔env权威表,schema生成)。

### 前提
- 一台能跑Docker Compose的Linux主机,部署基座是仓库根的`compose.prod.yml`。
- 模型CLI及凭据(容器内claude wrapper,经`CLAUDE_CLI_PATH`指定)。不配也能起服务,只是回显(echo模式),可先验协议再接模型。
- 至少一个声明了`channels.web:`块的profile:`GET /api/profiles`只列声明了该块的profile,块可为空(`web: {}`)。写法参考`profiles/_base/web.yaml`与`profiles/studentunion/profile.yaml`;可覆盖`channels.web.system_prompt_file`(网页版提示词,前端渲染markdown+KaTeX)与`channels.web.mcp_servers`(web会话的网关)。自己的提示词、名单等属于实例数据,放自己的实例目录或私有分支,不要提交回上游仓库。

### 装机
```bash
python3 deploy/setup-wizard.py init --instance ~/rtime-web --modules channel-web
```
向导自动带上依赖(core-config、gateway-core),在实例目录生成`.env`(已含`COMPOSE_PROFILES=web`)和`compose.override.yml`骨架。整体实例流程见`docs/instance-deploy.zh-CN.md`。

### 配置
在实例`.env`里补齐,逐项注释见`apps/web-chat/.env.example`:
- `WEB_CHAT_BIND`/`WEB_CHAT_PORT`:默认`127.0.0.1:8788`仅本机可访问;要开给内网改`0.0.0.0`并自行加访问控制(设计上建议Tailscale ACL)。
- `RTIME_PROFILES_DIR`:宿主机profiles树路径,compose把它只读挂到容器`/etc/rtime/profiles`(即`RTIME_PROFILES_ROOT`)。
- `CLAUDE_CLI_PATH`、`DEFAULT_MODEL`、`PERMISSION_MODE`、`DEFAULT_CWD`:模型接入;read_only的profile会在代码里强制只读权限模式,忽略`PERMISSION_MODE`。
- `WEB_CHAT_MCP_CONFIG`:进程默认MCP配置(内联JSON或路径);profile的`channels.web.mcp_servers`按会话覆盖它。web容器不挂库文件系统,库访问只经网关。
- `WEB_CHAT_READ_ONLY=1`是进程级只读硬门,只能收紧、不能放松profile自身的`read_only`。
- 宿主机路径类变量(`RTIME_ASSISTANT_STATE_DIR`、`CLAUDE_STATE_ROOT`、`CLAUDE_CONFIG_JSON`、`CLAUDE_KIMI_KEYFILE`、`WEB_CHAT_STATE_DIR`)改成自己机器的路径,compose默认值只是示例。
- 可选:`WEB_CHAT_SHOW_TOOL_CALLS`、`WEB_CHAT_RUN_TIMEOUT_SECONDS`、`WEB_CHAT_LOG_LEVEL`/`WEB_CHAT_DEBUG`、`RTIME_WEB_CHAT_PROFILES`(无profiles树时的内联覆盖)、`WEB_CHAT_ARCHIVE_ROOT`/`WEB_CHAT_ARCHIVE_MODE`(默认不落盘归档;开启后聊天记录只写进你指定的目录,不进代码仓)。

### 起服务与验证
```bash
cd ~/rtime-web
docker compose -f <仓库路径>/compose.prod.yml -f compose.override.yml \
  --env-file .env -p rtime-web up -d --build

curl http://127.0.0.1:8788/healthz        # 应返回{"ok": true, ...}
curl http://127.0.0.1:8788/api/profiles   # 应列出声明了channels.web的profile
```
浏览器打开`http://127.0.0.1:8788/`,在下拉里选profile发一条消息,能流式收到回复即接通。未配模型时收到回显同样说明链路通,补上`CLAUDE_CLI_PATH`后重启容器再验一次。

## 接自己的Obsidian vault(integration-obsidian)

本模块=Obsidian侧边栏插件(apps/obsidian-rtime-assistant)+本机HTTP网关(apps/assistant-gateway)。插件收集当前笔记/选区上下文发给网关,网关调本机claude CLI只读检索你的brain资料库后流式回答。数据边界:你的vault内容与brain库全部在仓库外、永不入仓;仓库只含代码、schema与文档,插件也不存任何模型密钥。

### 前提(自备)
- 一个自己的Obsidian vault(桌面端;当前manifest为isDesktopOnly,移动端不加载)。
- 一个资料库目录(BRAIN_ROOT指向,与vault分开;没有也可以先空目录跑通)。
- 本机python3;Node.js+npm(构建插件);claude CLI或兼容wrapper(CLAUDE_BIN指向,模型密钥留在CLI自己的配置里,网关env里没有密钥字段)。

### 装机
```bash
python3 deploy/setup-wizard.py init --instance <实例目录> --modules integration-obsidian
```
依赖gateway-core自动带上,产出实例.env骨架(含本模块装配要点注释)。本模块compose_profile为空:网关是仓库检出直跑,不进docker。准备env并试跑:
```bash
cp deploy/env/assistant-gateway.env.example ~/.config/rtime-assistant/gateway.env
# 按下节改好后,前台试跑(把env文件导入环境):
set -a; source ~/.config/rtime-assistant/gateway.env; set +a
python3 apps/assistant-gateway/gateway.py
```
长期跑用deploy/systemd/user/assistant-gateway.service(EnvironmentFile=%h/.config/rtime-assistant/gateway.env;ExecStart里的检出路径按你自己的改)。

构建并安装插件:
```bash
cd apps/obsidian-rtime-assistant
npm install
npm run package:plugin
```
把manifest.json、main.js、styles.css拷进`<vault>/.obsidian/plugins/rtime-assistant/`,在Obsidian第三方插件里启用。建议先用一次性测试vault验证,再接正式vault。

### 配置
完整60字段表见docs/config/assistant-gateway.md,字段语义与真相源说明见docs/reference/assistant-gateway-config.zh-CN.md。要点:
- GATEWAY_BIND/GATEWAY_PORT:默认127.0.0.1:8765,与插件默认端点锁定同步;公网机器绝不绑0.0.0.0。
- BRAIN_ROOT:你自己的资料库根(example里的路径是示例,改成自己的)。
- CLAUDE_BIN:claude CLI/wrapper路径。
- GATEWAY_ACCESS_MODE:默认readonly——这就是只读brain门,插件请求只能读库不能写;full只用于自己完全信任的端点。
- INDEX_PYTHONPATH/INDEX_DB:brain检索索引位置,留空走默认($HOME下state目录)。
所有字段改动需重启网关生效。配置模块已注册进admin-core registry(config_module=assistant-gateway),装了面板的可在面板管理。插件侧设置:聊天端点默认`http://127.0.0.1:8765/api/obsidian/chat`,健康检查默认`http://127.0.0.1:8765/healthz`,改端口要两边同步。

### 验证
```bash
curl http://127.0.0.1:8765/healthz
```
返回正常后,在Obsidian右侧栏打开Rtime Assistant视图发一问,应看到流式回答与来源卡片。不想先起真网关,可用插件自带smoke网关(同端口同契约,只回显上下文不跑模型)先验插件链路:
```bash
cd apps/obsidian-rtime-assistant && npm run smoke:gateway
```
插件内还有命令"运行后端自检",用真实链路跑健康检查/非流式/流式/Markdown渲染/续聊五项并写selftest-report.json。排障见apps/obsidian-rtime-assistant/docs/troubleshooting.md。

## 接自己的微信公众号订阅(integration-wechat-mp)

本模块把"公众号→助手可检索"的链路串起来,三件套:we-mp-rss(vendored第三方RSS抓取服务,`tools/chat-intake/wechat-mp-rss/`,保留上游LICENSE)+wechat-archiver(把文章归档为Markdown+本地图片,`tools/chat-intake/wechat-archiver/`)+we-mp-rss-mcp(公众号检索MCP,`tools/chat-intake/we-mp-rss-mcp/`)。逐模块运行手册见`tools/chat-intake/OPERATIONS.md`(§4–6)与`tools/chat-intake/README.md`。

### 前提
- 一台常开的Linux主机(树莓派级即可)。we-mp-rss要求Python 3.13+;MCP端Python 3.12+即可(依赖`mcp`、`httpx`)。
- 一个你自己的微信号:we-mp-rss靠网页扫码授权来抓公众号数据,授权会话会过期,过期需重扫。
- 自设一个管理员强口令(首次init用USERNAME/PASSWORD env注入)。上游默认口令众所周知,**不要用默认口令跑**,即使只监听内网。
- 数据边界:文章库(we-mp-rss实例`data/`)、登录token、归档产物(`ARCHIVE_DIR`)全部落在你自己的机器上,不进代码仓库;`config.yaml`已被`.gitignore`排除。

### 装机
先用安装向导登记模块(该模块没有compose profile,向导只在实例`.env`里写入装配要点注释,服务需手动起):

```bash
python3 deploy/setup-wizard.py init --instance <实例目录> --modules integration-wechat-mp
```

起we-mp-rss(独立venv):

```bash
cd tools/chat-intake/wechat-mp-rss
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp config.example.yaml config.yaml
USERNAME=admin PASSWORD='<你的强口令>' \
  .venv/bin/python main.py -job True -init True
```

`-init True`首次建管理员账号,读env `USERNAME`/`PASSWORD`(部分Linux环境自带`USERNAME`变量,两个都要显式传)。起wechat-archiver(复用we-mp-rss的venv,`fastapi`/`httpx`/`markdownify`已在其requirements.txt里):

```bash
WEMP_BASE_URL=http://127.0.0.1:8001 WEMP_USERNAME=admin \
  WEMP_PASSWORD='<你的强口令>' ARCHIVE_DIR=<归档目录> \
  tools/chat-intake/wechat-mp-rss/.venv/bin/python tools/chat-intake/wechat-archiver/archiver.py
```

长期运行请自行包systemd/cron;仓库暂无这两个服务的模板(`deploy/systemd/user/`里没有)。

### 配置
- we-mp-rss:`config.yaml`(从`config.example.yaml`复制,支持`${VAR:-默认}`环境变量替换,逐项注释见该文件)。关键env:`USERNAME`/`PASSWORD`(仅`-init True`建号时读)、`SECRET_KEY`(JWT签名密钥,不设会自动生成并持久化到`data/.secret_key`,设了env优先,建议显式设强随机串)、`PORT`(默认8001)。
- wechat-archiver:`WEMP_BASE_URL`(we-mp-rss地址,默认`http://127.0.0.1:8001`)、`WEMP_USERNAME`/`WEMP_PASSWORD`(**必填,无默认密码**,建议放env文件600)、`ARCHIVE_DIR`(归档根目录)、`ARCH_PORT`(默认8011)。
- we-mp-rss-mcp:同样三个`WEMP_*`。注意`server.py`的内置默认地址不是本机,**务必显式设`WEMP_BASE_URL`**。注册示例(Claude Code):

```bash
pip install mcp httpx
claude mcp add wechat-mp \
  -e WEMP_BASE_URL=http://127.0.0.1:8001 \
  -e WEMP_USERNAME=admin -e WEMP_PASSWORD='<你的强口令>' \
  -- python3 tools/chat-intake/we-mp-rss-mcp/server.py
```

- 每日抓取`daily_crawl.py`凭据同样只走env(cron行里`set -a; . <env文件>; set +a`后再跑)。

### 验证
服务活着:

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8001/   # 期望200
curl -s http://127.0.0.1:8001/docs >/dev/null && echo ok          # OpenAPI可达
curl -s http://127.0.0.1:8011/archive/list                        # archiver可达
```

浏览器开`http://<主机>:8001`,用你自己的账号(不是默认口令)登录→按界面提示微信扫码授权→搜索并订阅一个公众号→文章列表出现内容。归档链路:`curl -X POST http://127.0.0.1:8011/archive/account/<mp_id>`后,`ARCHIVE_DIR`下出现该号的Markdown与图片。MCP链路:注册后在助手里调`list_subscriptions()`应返回你订阅的号,`search_articles("关键词")`能命中文章(工具清单见`tools/chat-intake/we-mp-rss-mcp/README.md`)。

## 接自己的管理面板(panel-admin)

管理面板是给部署者自己用的本机HTTP控制台(admin-api):改配置树、看审计与历史、管模块开关。它永远绑127.0.0.1,不设计成公网服务。

### 前提
自备三样:一台跑本项目的主机(Python≥3.10,建议装uv)、一个放admin状态的目录、一个bearer keys文件。后两样都放在git仓库外——你的配置、密钥、审计日志属于你自己的部署数据,不会也不应进仓库。面板不需要域名或公网端口。

### 装机
在仓库根执行:

```bash
python3 deploy/setup-wizard.py init --instance <实例目录> --modules panel-admin
uv sync --all-packages
```

向导只生成实例文件(.env骨架、compose.override.yml、data/与state/目录),不碰docker。panel-admin不是compose服务(manifest里compose_profile为null),用下面的命令直接起服。

### 配置
先从模板建keys文件(格式见packages/rtime-admin-api/keys.example.json:JSON数组,每项name/key/scopes,key为至少16字符的随机串;可选expires_at、revoked、project_roles、is_platform_super)。把示例值全部换掉,并收紧权限:

```bash
cp packages/rtime-admin-api/keys.example.json <你的keys路径>
chmod 600 <你的keys路径>
# 每个key用足够长的随机串,例如:
openssl rand -hex 24
```

起服所需环境变量(真相源:packages/rtime-admin-api/src/rtime_admin_api/wiring.py头注与docs/reference/admin-api.zh-CN.md第五节):
- RTIME_ADMIN_STORE_DIR(必填):admin状态目录,存config.json、secrets.json、history/、audit.jsonl、salt,启动时自动建为0700。
- RTIME_ADMIN_API_KEYS(必填):keys文件路径,绝不入git;不设则拒绝启动。
- RTIME_ADMIN_API_HOST/RTIME_ADMIN_API_PORT:默认127.0.0.1:8790。绑非回环地址须显式设RTIME_ADMIN_API_ALLOW_NONLOOPBACK=1,否则启动即报错——不建议开。
- RTIME_MODULES_MANIFEST(可选):指向仓库的deploy/modules.json,启用面板"模块"总览(GET /v1/modules);不设则该端点返501。

```bash
export RTIME_ADMIN_STORE_DIR=<实例目录>/state/admin
export RTIME_ADMIN_API_KEYS=<你的keys路径>
export RTIME_MODULES_MANIFEST=<仓库路径>/deploy/modules.json
uv run python -m rtime_admin_api
```

### 验证

```bash
curl -sS -H "Authorization: Bearer <你的key>" http://127.0.0.1:8790/v1/health
```

返回{"ok":true,...}即接通。浏览器打开http://127.0.0.1:8790/panel,面板壳无鉴权但本身无用,须在页面里粘贴token才能调/v1;设了RTIME_MODULES_MANIFEST后"模块"页应能列出全部模块及安装状态。端点与scope细节见docs/reference/admin-api.zh-CN.md。
