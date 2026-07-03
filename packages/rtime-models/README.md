# rtime-models

Single **non-secret** source of truth for the rtime model directory + routing.

`model-registry.json` collapses the model ids / aliases / model-sets / tiers /
base_urls / capabilities / secret-env-names that used to be copied (and had
drifted) across ~16 places: the Obsidian gateway catalog, the Feishu bridge, the
`claude-rtime` router, the bash provider wrappers, compose/env templates, and the
plugin's capability schema. See `docs/maintainability-standards.zh-CN.md` §三 P3.

## What is and isn't here

- **Here:** provider/model identities, alias→model maps, catalog/routing/code
  model-sets, per-tier model ids (default/fast/quality), provider base_urls,
  per-provider `secret_env_names`, per-model `capabilities`.
- **Never here:** secret values. Each provider lists the *names* of the env
  vars / keyfiles that supply its secret; the live secret is read at runtime by
  the consumer, never stored in the registry.
- **Defaults only:** alias / model-set / tier / base_url values are *defaults*.
  Every consumer keeps its env override (`RTIME_*_MODELS`, `MODEL_ALIASES_JSON`,
  `RTIME_*_MODEL`, the `*_BASE_URL` envs). The registry supplies the fallback.

## Provider fields

| field | meaning |
|---|---|
| `id`, `label`, `protocol` | identity + transport |
| `base_url` / `base_url_cfg_key` / `base_url_env` | default endpoint + the cfg key / env that overrides it |
| `wrapper` | the `deploy/bin/claude-*` wrapper (or null for the configured `CLAUDE_BIN`) |
| `secret_env_names` | env var / keyfile names that supply the secret (read at runtime) |
| `catalog` | appears in the gateway/Obsidian static catalog |
| `file_extract` | provider-level server-side file extraction (Moonshot) |
| `catalog_models` (+`_env`) | default ordered model-id list shown in the catalog |
| `routing_models` (+`_env`) | model ids treated as this provider's chat models for CLI routing |
| `code_models` (+`_env`) | model ids that route to this provider's code wrapper |
| `tiers` (+`tier_env`) | `default`/`fast`/`quality` → model id for the wrapper |
| `models[]` | descriptive entries: `id`, `label`, `cli_model`, `aliases[]`, `capabilities{...}` |

`capabilities` keys: `agent_tools, code, chat, vision, file_extract,
long_context, thinking` (the single list lives in `rtime_models.CAPABILITY_KEYS`
and is drift-checked against the Obsidian `AssistantModelCapabilities` interface).

## Loader

Pure stdlib, offline. `RTIME_MODEL_REGISTRY` overrides the JSON path.

```python
import rtime_models as r
r.catalog_providers()                 # gateway/Obsidian catalog providers
r.base_url("moonshot-openai")         # default endpoint
r.secret_env_names("ustc-openai")     # env names that supply the secret
r.alias_map(["deepseek-code", "qwen-code", "ustc-openai"])  # claude-rtime aliases
r.routing_model_ids("ustc-openai")    # USTC chat routing set
r.code_models_with_aliases("deepseek-code")  # Feishu code-model recognition set
r.default_model_id()                  # "" -> wrapper default (kimi-code)
```

## CLI

```sh
python -m rtime_models validate                 # structural sanity (exit 1 on error)
python -m rtime_models gen-bash-defaults         # render deploy/bin/model-defaults.sh
python -m rtime_models dump                      # pretty-print the parsed registry
```

`deploy/bin/model-defaults.sh` is **generated** from the registry (sourced by the
bash wrappers) and is regenerate-and-diff gated by
`scripts/check-entrypoint-drift.py`. Regenerate with:

```sh
python -m rtime_models gen-bash-defaults > deploy/bin/model-defaults.sh
```
