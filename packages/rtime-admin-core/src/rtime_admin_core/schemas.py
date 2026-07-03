# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Config-module schemas registered with the admin core.

Most modules that a panel manages live *in their app* (``qq`` / ``web-chat``) and
are imported lazily so admin-core stays a leaf (see registry.py). Three modules
live here because they have no single owning app: ``models`` (the model directory
/ routing domain — its knobs are read by the ``claude-rtime`` wrapper, the Feishu
bridge, the assistant gateway and the ``rtime-models`` loader alike), plus the
still-illustrative ``library-gateway`` / ``channel-common`` samples awaiting their
own 收编 batches.

``ModelsConfig`` below is the REAL models config (批1 · Lane B): it registers the
env-driven knobs that select/route models and the provider credentials, so every
one is panel-manageable and coverage-guarded. It is registration/coverage only —
it does NOT change model-routing runtime behaviour; the apps still read env
directly today. The env NAMES here are the compatibility contract (every legacy
name kept via ``env_aliases`` / ``AliasChoices``), so a future stage that points a
consumer at the ConfigStore inherits exactly these names.

What is a schema field vs. what stays data: the env-driven *routing/selection*
knobs and the *credentials* are schema fields here. The provider catalog itself —
per-provider model ids, aliases, tiers, capabilities — lives in the DATA file
``packages/rtime-models/model-registry.json`` (loaded by ``rtime_models``); it is
not exploded into schema fields (it is a catalog, not a knob). ``model_registry_path``
is the one bridge: the schema field that points at that data file.

Field metadata recap (from rtime_config.fields):
    x-secret  -> credential; redacted in get_all / diff / audit.
    x-reload  -> "hot" (apply live) or "restart" (process must restart).
    x-scope   -> optional admin-API write scope guarding the field.
"""

from __future__ import annotations

from pydantic_settings import SettingsConfigDict
from rtime_config import RtimeBaseSettings, config_field, secret_field
from rtime_config.fields import Reload


class ModelsConfig(RtimeBaseSettings):
    """Model directory / routing + provider credentials (the ``rtime-models`` domain).

    The single manageable surface for how requests are routed to a model and which
    credentials reach each provider. Two layers, kept apart on purpose:

      - The provider CATALOG (model ids / aliases / tiers / capabilities / the
        wrapper each provider uses) is DATA, not config: it lives in
        ``packages/rtime-models/model-registry.json`` and is loaded by
        ``rtime_models``. ``model_registry_path`` points at it.
      - The env-driven ROUTING/SELECTION knobs and the provider CREDENTIALS are
        the fields below. Non-secret defaults mirror the registry / deploy env; the
        live per-provider API keys are secrets read from env or a keyfile and are
        never stored in config plaintext (x-secret => redacted in get_all/diff/audit).

    ``env_prefix=""`` (not ``RTIME_MODELS_``) on purpose: every field declares its
    COMPLETE set of accepted env names via ``env_aliases``, so the accepted env
    surface equals exactly what x-env-aliases documents — no implicit prefix-derived
    names silently widening it. Mirrors QQBridgeConfig / WebChatConfig.

    Behaviour note: this is registration/coverage only. Field defaults match the
    values the wrappers/loader already use as their fallback, and every legacy env
    name still loads via AliasChoices, so pointing a consumer at the ConfigStore
    later is byte-identical. No model-routing runtime path changes here.
    """

    model_config = SettingsConfigDict(
        env_prefix="",
        extra="ignore",
        populate_by_name=True,
        validate_default=True,
        case_sensitive=False,
    )

    # --- routing / selection -------------------------------------------------
    default_model: str = config_field(
        "claude",
        description="别名或模型 id:新请求默认路由到的模型。空串=经包裹脚本默认(kimi-code)。"
        "热切换:下条消息即生效。",
        reload=Reload.HOT,
        scope="write:models",
        env_aliases=["DEFAULT_MODEL"],
    )
    model_registry_path: str | None = config_field(
        None,
        description="model-registry.json 数据文件路径;None=用包内置默认"
        "(packages/rtime-models/model-registry.json)。该文件是 provider 目录"
        "(model ids/别名/tier/能力),是数据不是逐字段配置。",
        scope="write:models",
        env_aliases=["RTIME_MODEL_REGISTRY"],
    )
    model_aliases_json: str | None = config_field(
        None,
        description="飞书 /model 的别名覆盖表(内联 JSON:{别名:模型id})。空=用 registry "
        "分层别名(feishu_model_aliases)兜底。审计 §二.3 收编的三张别名表之一。",
        scope="write:models",
        env_aliases=["MODEL_ALIASES_JSON"],
    )
    ustc_agent: bool = config_field(
        True,
        description="科大 provider 是否走 claude-ustc(经 LiteLLM 做 Anthropic→OpenAI 协议"
        "翻译)带工具的 agent 路径;False(RTIME_USTC_AGENT=0)退回无工具的纯聊天直连。",
        scope="write:models",
        env_aliases=["RTIME_USTC_AGENT"],
    )
    ustc_agent_model: str | None = config_field(
        None,
        description="科大 agent 路径默认模型 id(tier default);None=用 registry 默认"
        "(deepseek-v4-flash-ascend)。",
        scope="write:models",
        env_aliases=["RTIME_USTC_AGENT_MODEL"],
    )
    ollama_model: str | None = config_field(
        None,
        description="本地 Ollama 包裹脚本(claude-ollama)默认模型 id;None=用 registry 默认"
        "(qwen3.5:9b)。",
        scope="write:models",
        env_aliases=["RTIME_OLLAMA_MODEL"],
    )

    # --- provider base urls (endpoint overrides; registry/deploy supply default) --
    ustc_base_url: str = config_field(
        "https://api.llm.ustc.edu.cn/v1",
        description="科大 OpenAI 兼容 base_url。改后需重启读取。",
        scope="write:models",
        env_aliases=["RTIME_USTC_BASE_URL"],
    )
    ollama_base_url: str = config_field(
        "http://127.0.0.1:11434",
        description="本地/边缘 Ollama 的 base_url。改后需重启读取。",
        scope="write:models",
        env_aliases=["RTIME_OLLAMA_BASE_URL"],
    )
    moonshot_base_url: str = config_field(
        "https://api.moonshot.ai/v1",
        description="Moonshot/Kimi OpenAI 兼容 base_url。",
        scope="write:models",
        env_aliases=["RTIME_MOONSHOT_BASE_URL"],
    )
    deepseek_anthropic_base_url: str = config_field(
        "https://api.deepseek.com/anthropic",
        description="DeepSeek 的 Anthropic 兼容 base_url(claude-deepseek 包裹脚本用)。",
        scope="write:models",
        env_aliases=["RTIME_DEEPSEEK_ANTHROPIC_BASE_URL"],
    )
    qwen_anthropic_base_url: str = config_field(
        "https://dashscope-intl.aliyuncs.com/apps/anthropic",
        description="通义千问的 Anthropic 兼容 base_url(claude-qwen 包裹脚本用)。",
        scope="write:models",
        env_aliases=["RTIME_QWEN_ANTHROPIC_BASE_URL"],
    )
    litellm_base_url: str | None = config_field(
        None,
        description="LiteLLM 网关 base_url(部署拓扑决定,故为 env 而非 registry 值);"
        "claude-ustc agent 路径经它做协议翻译。None=用部署默认。",
        scope="write:models",
        env_aliases=["RTIME_LITELLM_BASE_URL"],
    )

    # --- credentials (secrets: value stays in env/keyfile, never in config) ----
    litellm_master_key: str | None = secret_field(
        None,
        description="LiteLLM 网关的 master key(秘密)。",
        scope="write:models",
        env_aliases=["LITELLM_MASTER_KEY"],
    )
    ustc_api_key: str | None = secret_field(
        None,
        description="科大 provider 的 API key(秘密,值走 env/秘密文件,不入配置明文)。",
        scope="write:models",
        env_aliases=["RTIME_USTC_API_KEY"],
    )
    ustc_api_key_file: str | None = secret_field(
        None,
        description="科大 API key 的秘密文件路径(值走文件;路径本身在面板只见 ***)。",
        scope="write:models",
        env_aliases=["RTIME_USTC_API_KEY_FILE"],
    )
    moonshot_api_key: str | None = secret_field(
        None,
        description="Moonshot/Kimi provider 的 API key(秘密)。",
        scope="write:models",
        env_aliases=["RTIME_MOONSHOT_API_KEY", "MOONSHOT_API_KEY", "KIMI_API_KEY"],
    )
    deepseek_api_key: str | None = secret_field(
        None,
        description="DeepSeek provider 的 API key(秘密)。",
        scope="write:models",
        env_aliases=["RTIME_DEEPSEEK_API_KEY", "DEEPSEEK_API_KEY"],
    )
    qwen_api_key: str | None = secret_field(
        None,
        description="通义千问 provider 的 API key(秘密)。",
        scope="write:models",
        env_aliases=["RTIME_QWEN_API_KEY", "QWEN_API_KEY", "DASHSCOPE_API_KEY"],
    )
    kimi_keyfile: str | None = secret_field(
        None,
        description="Kimi Code 包裹脚本(claude-kimi)的秘密文件路径(值走文件)。",
        scope="write:models",
        env_aliases=["CLAUDE_KIMI_KEYFILE"],
    )


class LibraryGatewayConfig(RtimeBaseSettings):
    """brain 库网关(``rtime-library-gateway`` 域)—— 真实收编模型。

    覆盖 packages/rtime-library-gateway 进程实际读取的每个 env(grep gate.py /
    mcp_server.py / dispatch.py):传输(http host/port/socket)、空闲超时、prewarm、
    policy/audit 路径、索引路径(BRAIN_LIBRARY_INDEX)、brain/hub/reminders 根,以及
    嵌入模型(BRAIN_LIBRARY_EMBED_MODEL[_DIR],由网关进程内的 warm 检索器与 dispatch
    传给 indexer 子进程读取)。这些之前是网关内裸 os.environ.get / 样例未接线
    (见 config-full-coverage-plan §一;index-settings-adjustment §四把索引旋钮列全)。

    x-env-aliases 保全旧 env 名(P2 stage ① 行为保持);无前缀的名字(BRAIN_*)用
    env_aliases 显式声明,带前缀的名字(RTIME_LIBRARY_GATEWAY_*)由 env_prefix 推导
    (仍显式列 alias,让覆盖率守卫用到的正是运行时读的那个名)。x-reload:传输/路径类
    改后须重启进程读取(restart);idle_timeout 是 hot(每次 serve_stdio 读)。x-scope:
    改网关运维旋钮属 write:library(不解密、不读库内容)。x-secret:索引本身无密钥
    (DEK 另论,见 p5-impl),此模型无 secret 字段。
    """

    model_config = SettingsConfigDict(env_prefix="RTIME_LIBRARY_GATEWAY_")

    # --- 传输(host-network 网关多进程各绑一个回环端口 / socket) --------------
    http_host: str = config_field(
        "127.0.0.1",
        description="HTTP 传输绑定地址;默认仅回环,勿改为 0.0.0.0 暴露公网。改后重启。",
        scope="write:library",
        env_aliases=["RTIME_LIBRARY_GATEWAY_HTTP_HOST"],
    )
    http_port: int = config_field(
        8780,
        description=(
            "HTTP 传输监听端口(1..65535);现网 owner=8780、学生会=8781。运行时:仅当此 env "
            "非空才走 HTTP 传输,否则走 unix socket / stdio(socket_path 优先级见下)。改后需重启。"
        ),
        ge=1,
        le=65535,
        scope="write:library",
        env_aliases=["RTIME_LIBRARY_GATEWAY_HTTP_PORT"],
    )
    socket_path: str | None = config_field(
        None,
        description=(
            "unix socket 路径(常驻 warm 守护:一进程多短连接,jieba/embedder 只加载一次)。"
            "设了 http_port 时优先 HTTP;都不设走 stdio。改后需重启。"
        ),
        scope="write:library",
        env_aliases=["RTIME_LIBRARY_GATEWAY_SOCKET"],
    )
    idle_timeout: int = config_field(
        1800,
        description=(
            "stdio serve 的 stdin 空闲超时(秒),防半开 ssh 连接泄漏 mcp 进程;<=0 关闭该守卫。"
        ),
        ge=0,
        reload=Reload.HOT,
        scope="write:library",
        env_aliases=["RTIME_LIBRARY_GATEWAY_IDLE_TIMEOUT"],
    )
    prewarm: bool = config_field(
        # 与运行时真实默认对齐:mcp_server._maybe_prewarm 默认 ON('1');owner 实例开,
        # 学生会 scoped 实例经 env 置 0 关(板子内存紧,每 prewarm 钉一份 ONNX embedder)。
        True,
        description=(
            "启动时后台预热 jieba+嵌入模型(首个 hybrid 查询免 ~1.4s 冷加载,启动略慢、"
            "常驻多一份 embedder 内存)。默认开;0/false/no/off 关。"
        ),
        scope="write:library",
        env_aliases=["RTIME_LIBRARY_GATEWAY_PREWARM"],
    )

    # --- policy / audit 路径 ------------------------------------------------------
    policy_path: str | None = config_field(
        None,
        description=(
            "策略 JSON 路径;None=仓库内置默认(owner 全开)。学生会 scoped 实例指向"
            "studentunion-policy.json。owner wrapper 从不设=走内置默认(改此会静默改 owner 行为)。改后重启。"
        ),
        scope="write:library",
        env_aliases=["RTIME_LIBRARY_GATEWAY_POLICY"],
    )
    audit_log: str | None = config_field(
        None,
        description=(
            "审计 JSONL 路径(仅元数据);None=用 policy 里的 audit_log。owner 与 scoped "
            "两进程须分流,勿共享一个文件。改后重启。"
        ),
        scope="write:library",
        env_aliases=["RTIME_LIBRARY_GATEWAY_AUDIT_LOG"],
    )

    # --- 索引 / 库根(dispatch.py 解析,子进程与 warm 检索器共读) ---------------
    index_path: str | None = config_field(
        None,
        description=(
            "brain 检索索引 sqlite 文件路径(显式最高优先);None=default_index() 按内置"
            "优先级探测($XDG_STATE_HOME/…,orangepi 经 wrapper 指 NVMe)。改后重启。"
        ),
        scope="write:library",
        env_aliases=["BRAIN_LIBRARY_INDEX", "RTIME_LIBRARY_GATEWAY_INDEX"],
    )
    brain_root: str | None = config_field(
        None,
        description=(
            "brain 库根目录;None=按内置候选探测(/mnt/brain、NVMe、~/OrangePi-Store)。改后重启。"
        ),
        scope="write:library",
        env_aliases=["BRAIN_ROOT", "RTIME_BRAIN_ROOT"],
    )
    hub_root: str | None = config_field(
        None,
        description="rtime-hub 事实库根(lib.hub 面板);None=按内置候选探测。改后重启。",
        scope="write:library",
        env_aliases=["RTIME_HUB_ROOT"],
    )
    reminders_path: str | None = config_field(
        None,
        description="提醒 JSONL 路径(lib.settings.reminder_*);None=按内置候选探测。改后重启。",
        scope="write:library",
        env_aliases=["RTIME_REMINDERS_PATH"],
    )

    # --- 嵌入模型(hybrid 检索;网关进程 warm 检索器 + indexer 子进程读) --------
    embed_model: str | None = config_field(
        None,
        description=(
            "嵌入模型 key(如 bge-small / qwen3-0.6b);None=默认 bge-small。缺模型自动降级纯 "
            "BM25。切模型使增量向量复用失效,须全量重 embed(BRAIN_INDEX_FULL=1)。改后重启。"
        ),
        scope="write:library",
        env_aliases=["BRAIN_LIBRARY_EMBED_MODEL"],
    )
    embed_model_dir: str | None = config_field(
        None,
        description=(
            "本地嵌入模型目录(ONNX);None=不启用向量(纯 BM25 schema-3)。改后重启。"
        ),
        scope="write:library",
        env_aliases=["BRAIN_LIBRARY_EMBED_MODEL_DIR"],
    )


class ChannelCommonConfig(RtimeBaseSettings):
    """跨渠道通用运行时约束(飞书 / QQ 桥共读的一档)。

    这些是 audit §一 点名的"无前缀跨进程共享名"家族(DEFAULT_MODEL/PERMISSION_MODE
    等,靠 compose 改写消歧)。收编方案给它们 RTIME_CHAT_ 新前缀 + 旧名兜底;这里
    示范该形态。约束是**实例级**(read_only 等),与模型无关。
    """

    model_config = SettingsConfigDict(env_prefix="RTIME_CHAT_")

    read_only: bool = config_field(
        False,
        description="实例只读硬门:True 时禁所有写工具(学生会实例开,owner 实例关)。",
        scope="write:channel",
        env_aliases=["RTIME_CHAT_READ_ONLY", "READ_ONLY"],
    )
    permission_mode: str = config_field(
        "default",
        description="工具权限模式:default / acceptEdits / bypassPermissions / plan。",
        scope="write:channel",
        env_aliases=["RTIME_CHAT_PERMISSION_MODE", "PERMISSION_MODE"],
    )
    max_turns: int = config_field(
        0,
        description="单次会话工具轮次上限;0=无上限(仅超时兜底),勿轻易设小。",
        ge=0,
        reload=Reload.HOT,
        scope="write:channel",
        env_aliases=["RTIME_CHAT_MAX_TURNS", "CLAUDE_MAX_TURNS"],
    )
    reply_timeout_seconds: int = config_field(
        600,
        description="等待模型完成一条回复的超时(秒)。热载:下条消息生效。",
        ge=1,
        reload=Reload.HOT,
        scope="write:channel",
        env_aliases=["RTIME_CHAT_REPLY_TIMEOUT_SECONDS"],
    )


class ChatRuntimeConfig(RtimeBaseSettings):
    """rtime-chat-runtime 包的直接 env 旋钮(``chat-runtime`` 域,批 3 收编)。

    packages/rtime-chat-runtime 是刻意 dependencies=[] 的纯运行时叶子(五个渠道无关
    原语),不引入 pydantic;故其唯一的**直接字面量** env 旋钮
    (RTIME_CAMPUS_URLS_FILE,campus_urls.load_campus_urls 读)的 schema 由 admin-core
    自有(与 library-gateway 同理:零依赖叶子的 schema 归这里),注册后面板可管理、
    覆盖率守卫可证——不给 rtime-chat-runtime 加依赖、不改其运行时取值链(仍裸读 env),
    行为零变更。

    仅收编**直接以字面量读**的 env。其余 rtime-chat-runtime 的 env 都不在这里:
    - RTIME_ASSISTANT_RUN_LOG(run_log.py)是部署注入的运行日志路径 → 留 allowlist
      (deploy-path,跨切面共享,由部署 owns)。
    - tool_policy.py 读的 read_only_env / personal_library_env 是**注入的 env 名**
      (实例属性,如 QQ_READ_ONLY / QQ_PERSONAL_LIBRARY 由渠道入口传入),不是本模块的
      字面量;那些 env 名归各渠道配置(qq / feishu)owns,本库只按入口给的名去读。
    """

    model_config = SettingsConfigDict(
        env_prefix="",
        extra="ignore",
        populate_by_name=True,
        validate_default=True,
        case_sensitive=False,
    )

    campus_urls_file: str | None = config_field(
        None,
        description="校园服务 URL 表覆盖/扩展文件(JSON)路径;None/空=用内置表。"
        "hot:campus 意图命中即按 mtime 重读(campus_urls.load_campus_urls 有 mtime 缓存)。",
        reload=Reload.HOT,
        scope="write:channel",
        env_aliases=["RTIME_CAMPUS_URLS_FILE"],
    )


class SyncIntegrationConfig(RtimeBaseSettings):
    """Syncthing 同步集成(``sync`` 域,K3 通用可选模块)。

    Syncthing 是外部服务(用户自装自配,内容永不进本仓库);本模块是**助手侧的指针**:
    同步的笔记目录在哪、Syncthing REST API 怎么连——面板"模块"页(K5)用它渲染表单、
    亮同步健康灯(经 /rest/system/status 或 /rest/db/status 查询)。与 models 的
    registry 同理:Syncthing 自己的配置(设备/共享/版本控制)是它的数据,不在这里逐
    字段铺开;这里只有"这台机器怎么找到它"。

    没有任何字段被现有运行时读(注册面先行,同 ustc-kb/qq-selfheal 的镜像纹理);
    K5 面板状态探测是第一个消费者。全部 HOT:探测在请求时读。
    """

    model_config = SettingsConfigDict(
        env_prefix="",
        extra="ignore",
        populate_by_name=True,
        validate_default=True,
        case_sensitive=False,
    )

    notes_root: str | None = config_field(
        None,
        description="被同步的笔记目录(如 brain-notes)在本机的路径;None=本机不参与"
        "笔记同步。只是指针:目录内容由 Syncthing 管,永不入仓。",
        reload=Reload.HOT,
        scope="write:library",
        env_aliases=["RTIME_SYNC_NOTES_ROOT"],
    )
    api_url: str = config_field(
        "http://127.0.0.1:8384",
        description="本机 Syncthing REST API 地址(GUI 同端口);面板健康探测用。",
        reload=Reload.HOT,
        scope="write:library",
        env_aliases=["RTIME_SYNC_API_URL"],
    )
    api_key: str | None = secret_field(
        None,
        description="Syncthing API key(GUI→操作→设置→API 密钥);None=不做带鉴权的"
        "状态查询(/rest/noauth/health 仍可探活)。",
        reload=Reload.HOT,
        scope="write:library",
        env_aliases=["RTIME_SYNC_API_KEY"],
    )
    folder_id: str | None = config_field(
        None,
        description="笔记共享在 Syncthing 里的 folder id(如 brain-notes);"
        "None=健康灯只探进程存活,不查该共享的同步完成度。",
        reload=Reload.HOT,
        scope="write:library",
        env_aliases=["RTIME_SYNC_FOLDER_ID"],
    )
