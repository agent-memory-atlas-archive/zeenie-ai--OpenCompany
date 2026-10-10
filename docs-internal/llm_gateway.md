# LLM Gateway (LiteLLM)

OpenCompany's hosted LLM gateway is the stock open-source LiteLLM Proxy, deployed from `gateway/` with BerriAI's official Terraform module. Invited people call LLMs on platform-owned vendor keys, each with a monthly allowance, and spend is recorded per person and per session. The repository holds only configuration and tests; there is no custom gateway code. The operator runbook (deployment, adding people, upgrades) is [gateway/README.md](../gateway/README.md).

**Status:** the gateway (Phase 0) is in `gateway/`. The OpenCompany side (routing, the Connectors entry, attribution headers) is not implemented yet; the contract below is what it will rely on, and `gateway/tests/test_gateway_contract.py` pins each part of it against the local stack.

## Layout

| Path | Contents |
|---|---|
| `gateway/litellm/config.yaml` | Production proxy config: provider wildcards, budget defaults, unpriced-model refusal, opt-in Redis cache, spend-log retention |
| `gateway/local/` | Local stack: Postgres 16, Redis 7, LiteLLM with mock models, and `fake_anthropic.py`, a real upstream event stream for streamed-pricing tests |
| `gateway/tests/test_gateway_contract.py` | Contract tests against the local stack (skipped when it isn't running) |
| `gateway/tests/test_pins.py` | Offline checks: version and price-list pins agree; every served default model is priced |
| `gateway/infra/terraform/` | Wrapper around `BerriAI/litellm/aws`: ECS Fargate, Aurora Postgres (IAM auth), ElastiCache, ALB, ACM, Route 53 records in the delegated `gw.opencompany.sh` zone |
| `.github/workflows/gateway-ci.yml` | Terraform checks, cosign verification of the pinned images, locked and audited test dependencies, contract and pin tests |

## Pins

- **LiteLLM 1.103.4**, in four places that `test_pins.py` keeps in agreement: `local.litellm_version` and the module `version` in `main.tf`, the compose image tag, and `LITELLM_VERSION` in CI. 1.103.x is the only stable line with the streamed-alias billing fix (LiteLLM PR #44341).
- **The price list**: `LITELLM_MODEL_COST_MAP_URL` points at one reviewed commit of LiteLLM's `model_prices_and_context_window.json`, in `main.tf` and the compose file. Unpinned, the gateway downloads upstream `main` at every start, so prices would change unannounced; the list bundled with 1.103.4 predates `gpt-6-sol` and the Claude 5.5 models. `GET /model/cost_map/source` shows the list a process loaded (on the deployed stack it is routed to the backend service, not the gateway tasks).

## Served providers

OpenAI, Anthropic, Gemini, Groq, Cerebras, xAI, Moonshot (OpenCompany's `kimi`) and Mistral for chat; OpenAI and Groq speech; OpenAI embeddings. `test_pins.py` lists them as `SERVED_PROVIDERS` and checks each default model in `server/config/llm_defaults.json` against the pinned list.

Not served: OpenRouter, DeepSeek and Sarvam (no price for their default models), ElevenLabs and Deepgram (OpenCompany speaks their native formats), and every tool API.

## Contract OpenCompany relies on

| Behaviour | Detail | Test |
|---|---|---|
| Wire model names | OpenAI-format calls use `<litellm prefix>/<model>` on `/v1` (`openai/`, `groq/`, `cerebras/`, `xai/`, `moonshot/`, `mistral/`). Anthropic SDK calls send bare `claude-*` ids to `/v1/messages`; Gemini SDK calls use the native `/v1beta/models/<id>:generateContent` route with the key in `x-goog-api-key` | `test_anthropic_messages_route_accepts_bare_model_ids`, `test_gemini_native_route_accepts_virtual_key` |
| Spent allowance | Refused with error type `budget_exceeded` on all three routes; HTTP 429 on 1.103.x, 402 from 1.104. Recognise it by type | `test_budget_refusal_is_typed_on_every_route` |
| Unpriced model | HTTP 403, error type `model_cost_map_missing` | `test_unpriced_model_is_refused` |
| Allowance | `GET /user/info` with the person's key returns `user_info.spend`, `max_budget` and `budget_reset_at`. Needs a Default-type key; LLM-API keys are refused; another user's id is refused | `test_user_info_needs_a_default_key_and_stays_scoped` |
| Attribution | `x-litellm-session-id`, `x-litellm-tags` (comma-separated, low cardinality: `workflow:`, `source:`, `user:`) and `x-litellm-spend-logs-metadata` (JSON) land on the spend row, queryable with `/spend/logs/v2?session_id=` | `test_attribution_headers_reach_spend_logs` |
| Retry replay | Request body `cache: {"use-cache": true, "namespace": <stable per activity>, "ttl": 172800}` (OpenAI format through `extra_body`). A repeated call with the same namespace is a cache hit billed at $0, streamed or not, on `/v1/chat/completions` and `/v1/messages` | `test_cache_namespace_replays_retry_for_free`, `test_streamed_retry_replays_for_free`, `test_anthropic_messages_cache_namespace` |
| Streamed pricing | A real upstream stream through a dotted alias is billed at the deployment's price | `test_streamed_alias_is_priced_at_the_deployment` |

## Keys

People create one Default-type key per install in the console. A key draws on its person's monthly allowance, and LiteLLM reserves the worst-case cost of each call in Redis before admitting it, so a request with a large `max_tokens` can be refused near the cap. A Default key can also create and delete that person's own keys (not raise the allowance); a leaked key is revoked in the console.
