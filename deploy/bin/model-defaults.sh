#!/usr/bin/env bash
# GENERATED — DO NOT EDIT. source: packages/rtime-models/model-registry.json
# regen: python -m rtime_models gen-bash-defaults > deploy/bin/model-defaults.sh
# Non-secret model DEFAULTS for the claude-* provider wrappers. Each wrapper
# keeps its ${RTIME_*:-default} override; this file only supplies the default.

# deepseek-code (claude-deepseek)
REG_DEEPSEEK_MODEL='deepseek-v4-pro[1m]'
REG_DEEPSEEK_FAST_MODEL='deepseek-v4-flash'
REG_DEEPSEEK_BASE_URL='https://api.deepseek.com/anthropic'

# qwen-code (claude-qwen)
REG_QWEN_MODEL='qwen3-coder-next'
REG_QWEN_FAST_MODEL='qwen3-coder-flash'
REG_QWEN_QUALITY_MODEL='qwen3-coder-plus'
REG_QWEN_BASE_URL='https://dashscope-intl.aliyuncs.com/apps/anthropic'

# kimi-code (claude-kimi)
REG_KIMI_MODEL='kimi-code'
REG_KIMI_BASE_URL='https://api.kimi.com/coding'

# ustc-openai agent path (claude-ustc via LiteLLM; base URL is deployment
# topology -> RTIME_LITELLM_BASE_URL, deliberately not a registry value)
REG_USTC_MODEL='deepseek-v4-flash-ascend'

# ollama (claude-ollama)
REG_OLLAMA_MODEL='qwen3.5:9b'
REG_OLLAMA_BASE_URL='http://127.0.0.1:11434'
