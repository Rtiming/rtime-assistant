# syntax=docker/dockerfile:1.7

# Build-host note (orangepi / in-China network):
#   - python:3.11-slim is Debian trixie and its default apt source deb.debian.org
#     is unreachable here; repo.huaweicloud.com mirrors trixie + arm64 and is reachable.
#   - docker injects a host-only proxy (http://127.0.0.1:7890 from ~/.docker/config.json)
#     into build steps; that address is dead inside the container.
# So every apt/npm stage below rewrites apt to repo.huaweicloud.com and clears that
# proxy, so apt-get and `npm install` reach mirrors/registries directly. Override the
# mirror by editing the sed host (e.g. mirrors.tuna.tsinghua.edu.cn) when building
# elsewhere.

FROM python:3.11-slim AS bridge-base

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app/apps/feishu-bridge

COPY apps/feishu-bridge/requirements.txt ./
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt

COPY apps/feishu-bridge/ ./

# Shared chat-runtime primitives (P5, see docs/maintainability-standards.zh-CN.md §三)
# + the schema-driven settings base rtime-config (P2 config 收编: bot_config.py's
# FeishuBridgeConfig imports rtime_config). apps/feishu-bridge/_shared_runtime.py adds
# /app/packages/<pkg>/src to sys.path at startup (parents[2] of main.py == /app), so no
# PYTHONPATH env is needed. Inherited by both the `test` and `runtime` stages below.
COPY packages/rtime-chat-runtime /app/packages/rtime-chat-runtime
COPY packages/rtime-config /app/packages/rtime-config

FROM bridge-base AS test

ENV PYTHONPATH=/app/packages/rtime-admin-core/src:/app/packages/rtime-config/src:/app/packages/rtime-models/src:/app/packages/rtime-chat-runtime/src

COPY apps/feishu-bridge/requirements-dev.txt ./
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install -r requirements-dev.txt
COPY deploy/bin/ /app/deploy/bin/
RUN chmod 0755 /app/deploy/bin/*
# Model registry so the bundled feishu tests exercise the live-read path (matches
# production), not only the import-guarded fallback.
COPY packages/rtime-models /app/packages/rtime-models
# Admin registry + generated config docs are part of the feishu test contract.
COPY packages/rtime-admin-core /app/packages/rtime-admin-core
COPY docs/config/feishu.md /app/docs/config/feishu.md

CMD ["python", "-m", "pytest", "tests", "-q"]

FROM python:3.11-slim AS docpack-base

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/packages/brain-docpack/src:/app/packages/brain-library/src:/app/packages/brain-citation/src:/app/packages/rtime-assistant-runtime/src:/app/packages/rtime-hub-connector/src:/app/packages/rtime-context/src:/app/packages/rtime-profile/src:/app/packages/rtime-automation/src:/app/packages/rtime-review/src

WORKDIR /app

# CN mirror + drop injected host-only proxy (see header note)
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/root/.cache/pip \
    set -eux; \
    for f in /etc/apt/sources.list /etc/apt/sources.list.d/debian.sources; do \
      if [ -f "$f" ]; then sed -i -e 's|deb.debian.org|repo.huaweicloud.com|g' -e 's|security.debian.org|repo.huaweicloud.com|g' "$f"; fi; \
    done; \
    export http_proxy='' https_proxy='' all_proxy='' HTTP_PROXY='' HTTPS_PROXY='' ALL_PROXY=''; \
    apt-get -o Acquire::http::Proxy=false -o Acquire::https::Proxy=false update; \
    apt-get -o Acquire::http::Proxy=false -o Acquire::https::Proxy=false install -y --no-install-recommends poppler-utils; \
    rm -rf /var/lib/apt/lists/*; \
    python -m pip install --upgrade pip; \
    python -m pip install pytest

FROM docpack-base AS docpack-test

COPY . .

CMD ["python", "-m", "pytest", "tests", "-q"]

FROM docpack-base AS docpack-office-base

# CN mirror + drop injected host-only proxy (see header note)
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    set -eux; \
    for f in /etc/apt/sources.list /etc/apt/sources.list.d/debian.sources; do \
      if [ -f "$f" ]; then sed -i -e 's|deb.debian.org|repo.huaweicloud.com|g' -e 's|security.debian.org|repo.huaweicloud.com|g' "$f"; fi; \
    done; \
    export http_proxy='' https_proxy='' all_proxy='' HTTP_PROXY='' HTTPS_PROXY='' ALL_PROXY=''; \
    apt-get -o Acquire::http::Proxy=false -o Acquire::https::Proxy=false update; \
    apt-get -o Acquire::http::Proxy=false -o Acquire::https::Proxy=false install -y --no-install-recommends \
      fonts-dejavu-core \
      libreoffice-calc \
      libreoffice-impress \
      libreoffice-writer; \
    rm -rf /var/lib/apt/lists/*

FROM docpack-office-base AS docpack-office-test

COPY . .

CMD ["python", "-m", "pytest", "tests", "-q"]

FROM bridge-base AS runtime

ARG INSTALL_CLAUDE_CODE=1

# CN mirror + drop injected host-only proxy (see header note); npm registry reached directly
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/root/.npm \
    set -eux; \
    for f in /etc/apt/sources.list /etc/apt/sources.list.d/debian.sources; do \
      if [ -f "$f" ]; then sed -i -e 's|deb.debian.org|repo.huaweicloud.com|g' -e 's|security.debian.org|repo.huaweicloud.com|g' "$f"; fi; \
    done; \
    export http_proxy='' https_proxy='' all_proxy='' HTTP_PROXY='' HTTPS_PROXY='' ALL_PROXY=''; \
    apt-get -o Acquire::http::Proxy=false -o Acquire::https::Proxy=false update; \
    apt-get -o Acquire::http::Proxy=false -o Acquire::https::Proxy=false install -y --no-install-recommends \
      bash \
      ca-certificates \
      curl \
      git \
      procps \
      ripgrep \
      nodejs \
      npm; \
    if [ "$INSTALL_CLAUDE_CODE" = "1" ]; then npm install -g @anthropic-ai/claude-code; fi; \
    rm -rf /var/lib/apt/lists/*

COPY deploy/bin/claude-kimi /usr/local/bin/claude-kimi
COPY deploy/bin/claude-rtime /usr/local/bin/claude-rtime
COPY deploy/bin/claude-deepseek /usr/local/bin/claude-deepseek
COPY deploy/bin/claude-qwen /usr/local/bin/claude-qwen
COPY deploy/bin/claude-ustc /usr/local/bin/claude-ustc
COPY deploy/bin/claude-ollama /usr/local/bin/claude-ollama
# Registry-generated non-secret model defaults, sourced by the claude-* wrappers.
COPY deploy/bin/model-defaults.sh /usr/local/bin/model-defaults.sh
COPY deploy/bin/rtime-web-fetch /usr/local/bin/rtime-web-fetch
COPY deploy/bin/rtime-reminder-register /usr/local/bin/rtime-reminder-register
COPY deploy/bin/rtime-reminder-mcp /usr/local/bin/rtime-reminder-mcp
COPY deploy/bin/rtime-reminder-wake-runner /usr/local/bin/rtime-reminder-wake-runner
COPY deploy/bin/rtime-context-source /usr/local/bin/rtime-context-source
COPY deploy/bin/rtime-memory-candidate /usr/local/bin/rtime-memory-candidate
COPY deploy/bin/rtime-qq-code /usr/local/bin/rtime-qq-code
COPY deploy/bin/rtime-notify-admin /usr/local/bin/rtime-notify-admin
RUN chmod 0755 /usr/local/bin/claude-kimi /usr/local/bin/claude-rtime /usr/local/bin/claude-deepseek /usr/local/bin/claude-qwen /usr/local/bin/claude-ustc /usr/local/bin/claude-ollama /usr/local/bin/rtime-web-fetch /usr/local/bin/rtime-reminder-register /usr/local/bin/rtime-reminder-mcp /usr/local/bin/rtime-reminder-wake-runner /usr/local/bin/rtime-context-source /usr/local/bin/rtime-memory-candidate /usr/local/bin/rtime-qq-code /usr/local/bin/rtime-notify-admin

# Model registry (single non-secret source of truth) so claude-rtime + the bridge
# read model routing live; the generated model-defaults.sh above mirrors it for the
# bash wrappers. PYTHONPATH lets claude-rtime (in /usr/local/bin) import it too.
COPY packages/rtime-models /app/packages/rtime-models
ENV PYTHONPATH=/app/packages/rtime-models/src

ENV HOME=/var/lib/rtime-assistant
ENV CLAUDE_BIN=/usr/local/bin/claude
ENV CLAUDE_CLI_PATH=/usr/local/bin/claude-rtime
ENV CLAUDE_KIMI_KEYFILE=/run/secrets/rtime-assistant/claude-kimi-key
ENV RTIME_CLAUDE_FALLBACK=/usr/local/bin/claude-kimi
ENV RTIME_DEEPSEEK_CLAUDE_WRAPPER=/usr/local/bin/claude-deepseek
ENV RTIME_QWEN_CLAUDE_WRAPPER=/usr/local/bin/claude-qwen
ENV RTIME_USTC_CLAUDE_WRAPPER=/usr/local/bin/claude-ustc
ENV RTIME_OLLAMA_CLAUDE_WRAPPER=/usr/local/bin/claude-ollama
ENV RTIME_DEEPSEEK_API_KEY_FILE=/run/secrets/rtime-assistant/deepseek-api-key
ENV RTIME_QWEN_API_KEY_FILE=/run/secrets/rtime-assistant/qwen-api-key
ENV RTIME_USTC_API_KEY_FILE=/run/secrets/rtime-assistant/ustc-api-key

RUN mkdir -p /var/lib/rtime-assistant

CMD ["python", "main.py"]
