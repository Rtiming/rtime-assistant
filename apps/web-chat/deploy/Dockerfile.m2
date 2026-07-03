# web-chat (T5a/T5b) — layered FROM the feishu runtime image, exactly like qq-bridge.
# The base already carries the claude CLI + claude-rtime (Kimi/USTC/…) wrappers and
# rtime-chat-runtime, so this image adds essentially nothing (stdlib-only backend,
# no FastAPI, no npm — markdown/KaTeX come from a pinned CDN in the browser). That
# keeps the incremental image well under 200MB (design §7 T5a acceptance).
#
# SECURITY / gateway-only consumer (design §5.3): this container does NOT mount
# /mnt/brain — library access is only via the selected profile's web MCP gateway
# (or WEB_CHAT_MCP_CONFIG for override-only instances). Nothing here needs the
# filesystem library.
#
# Do NOT `docker build` this bare — compose passes BASE explicitly at build time
# (same rationale as apps/qq-bridge/deploy/Dockerfile.m2: a stale default base
# silently drops the wrappers). Built via compose only.
ARG BASE=rtime-assistant/feishu-bridge:local
FROM ${BASE}

USER root
COPY apps/web-chat/web_chat /app/web_chat
# Ship rtime-chat-runtime ourselves (the base image doesn't expose it under /app in
# a way this app's _runtime_path bootstrap can find without the env var below).
COPY packages/rtime-chat-runtime /app/packages/rtime-chat-runtime
ENV RTIME_CHAT_RUNTIME_SRC=/app/packages/rtime-chat-runtime/src \
    RTIME_ASSISTANT_ROOT=/app \
    PYTHONUNBUFFERED=1
WORKDIR /app
USER 1000:1000
# /healthz on the bind port (default 8788). Reads WEB_CHAT_PORT at runtime.
HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=15s \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.getenv('WEB_CHAT_PORT','8788')+'/healthz', timeout=3).read()" || exit 1
CMD ["python", "-m", "web_chat"]
