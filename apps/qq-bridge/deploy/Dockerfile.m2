# QQ bridge M2 — reuse the Feishu runtime image so claude + the claude-rtime (Kimi)
# wrappers + brain MCP + rtime-chat-runtime are already present. The only addition is
# aiohttp (the reverse-WS server). Run the container with `--volumes-from` the Feishu
# container to inherit .claude (Max creds + brain MCP), /mnt/brain, and the Kimi key.
#
# Build context = the qq-bridge-spike dir on orange pi (has apps/qq-bridge/qq_bridge).
# Clear the host-only docker proxy + use a CN mirror (same reason as the Feishu image).
# Default `:local` matches compose.prod.yml's ${RTIME_ASSISTANT_IMAGE_TAG:-local};
# compose passes BASE explicitly at build time. Do NOT `docker build` this image
# bare — the old `:orangepi-local` default silently pulled a stale base missing the
# new claude/kimi wrappers (that's how claude-ustc went missing on 2026-07-02). See
# apps/qq-bridge/README.md: the qq image is built via compose only.
ARG BASE=rtime-assistant/feishu-bridge:local
FROM ${BASE}

USER root
# aiohttp = reverse-WS server; jieba = brain-library BM25 tokenizer (the only required
# brain-library dep; vector/ONNX extras are intentionally NOT installed → BM25-only,
# light + no model load on the ARM host); pypdf = inbound-file text extraction;
# pydantic-settings = schema-driven config (qq_bridge.config via packages/rtime-config);
# pyyaml = T2 profile loader (rtime_config.profile parses profile.yaml when RTIME_PROFILE set).
RUN HTTP_PROXY="" HTTPS_PROXY="" http_proxy="" https_proxy="" \
    python -m pip install --no-cache-dir \
      -i https://pypi.tuna.tsinghua.edu.cn/simple "aiohttp>=3.9" "jieba>=0.42.1" "pypdf>=4.0" \
      "pydantic-settings>=2.0" "pyyaml>=6.0"
# Voice STT (D): sherpa-onnx Paraformer runs locally on the ARM CPU (RTF≈0.07, the
# community standard for edge Chinese ASR). numpy feeds the wav into the recognizer.
# The ~230MB Paraformer-zh int8 model is NOT baked — it is mounted at runtime
# (QQ_STT_MODEL_DIR) so the image stays small. Best-effort: a failed install only
# disables STT (voice degrades to a "please send text" note), the bridge still runs.
RUN HTTP_PROXY="" HTTPS_PROXY="" http_proxy="" https_proxy="" \
    python -m pip install --no-cache-dir \
      -i https://pypi.tuna.tsinghua.edu.cn/simple "sherpa-onnx>=1.10" "numpy>=1.24" \
    || echo "warn: sherpa-onnx not installed (voice STT unavailable)"
# socat bridges the per-message claude's stdio to the prewarmed brain gateway's unix
# socket (opt-in via QQ_MCP_CONFIG). Best-effort: a failed apt (offline build) leaves
# socat absent, which only disables the indexed-lib_search opt-in — grep still works.
RUN sh -c 'export http_proxy= https_proxy= HTTP_PROXY= HTTPS_PROXY=; \
    apt-get update && apt-get install -y --no-install-recommends socat \
    && rm -rf /var/lib/apt/lists/*' \
    || echo "warn: socat not installed (lib_search-over-socket opt-in unavailable)"

COPY apps/qq-bridge/qq_bridge /app/qq_bridge
# Ship rtime-chat-runtime ourselves (the base image doesn't expose it under /app).
COPY packages/rtime-chat-runtime /app/packages/rtime-chat-runtime
# brain MCP (indexed lib_search): the gateway + the library it dispatches to. The
# gateway subprocesses brain_library.cli with PYTHONPATH=$RTIME_ASSISTANT_ROOT/packages/
# brain-library/src; index is mounted at runtime (BRAIN_LIBRARY_INDEX). See QQ_MCP_CONFIG.
COPY packages/rtime-library-gateway /app/packages/rtime-library-gateway
COPY packages/brain-library /app/packages/brain-library
# Schema-driven config base (P2 pilot): qq_bridge.config imports rtime_config.
COPY packages/rtime-config /app/packages/rtime-config
# T2 profile consumption: QQBridgeConfig.from_profile builds the ConfigStore that
# resolves env>store>profile>default (imported lazily, only when RTIME_PROFILE set).
COPY packages/rtime-admin-core /app/packages/rtime-admin-core
ENV RTIME_CHAT_RUNTIME_SRC=/app/packages/rtime-chat-runtime/src \
    RTIME_CONFIG_SRC=/app/packages/rtime-config/src \
    RTIME_ADMIN_CORE_SRC=/app/packages/rtime-admin-core/src \
    RTIME_ASSISTANT_ROOT=/app \
    PYTHONUNBUFFERED=1
WORKDIR /app
USER 1000:1000
# Reverse-WS server also exposes /healthz on the WS port (host network) — let compose
# (and `docker ps`) see liveness. Reads QQ_BRIDGE_WS_PORT at runtime (default 8080).
HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=20s \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.getenv('QQ_BRIDGE_WS_PORT','8080')+'/healthz', timeout=3).read()" || exit 1
CMD ["python", "-m", "qq_bridge"]
