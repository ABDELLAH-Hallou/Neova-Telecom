# Langfuse tracing — setup and live-audit evidence (bonus, before issue #7)

**Status:** implemented and live-audited on branch `7-pr-6-evaluationsecurity-fixed-cases-privacy-checks-and-actual-results`. Setup followed the official [Langfuse agent skill](https://github.com/langfuse/skills) (`langfuse` skill, installed at `.opencode/skills/langfuse/`) with current docs fetched before any code: OpenAI-SDK integration, Python SDK v4 instrumentation, and the trace best-practices page.

## What was added

| Change | Purpose |
| --- | --- |
| `neova/observability.py` (new) | Opt-in Langfuse glue: `turn()` (one root observation per `/agent/chat` turn, named `agent-chat`, input = user message, output = French reply), `step()` (verb-named children typed `tool`/`retriever`/`span`), lazy client construction that never raises into callers, `flush()`/`shutdown()`. Strictly redacted: no API key, no customer payload, no `user_id`; `session_id` = the random process-local demo session token (grouping only). |
| `neova/provider.py`, `neova/classifier.py` | Chat calls construct the `langfuse.openai` **drop-in client** when tracing is enabled (generations captured automatically with model, tokens, cost, errors) and the plain OpenAI class otherwise — the offline suite constructs no instrumentation. Generation names passed as `name="generate-response"` / `"classify-intent"`; the wrapper consumes the kwarg client-side so it never reaches the OpenRouter body. |
| `neova/graph.py` | `run_conversation` wraps the turn in `observability.turn(...)`; route + classification source recorded in metadata. One trace per turn, `session_id` groups the conversation in the Sessions view. |
| `neova/nodes/classify.py`, `gather.py`, `booking_flow.py`, `handoff.py` | Bounded child steps: `classify-intent` (span), `read-customer-summary` / `read-incidents` / `read-technician-slots` (tool), `retrieve-context` (retriever, with mode/degraded/sources metadata), `book-appointment`, `check-booking-by-key`, `create-handoff` (tools). Tool spans carry names/statuses/counts only — never customer-API payloads. |
| `neova/config.py` | `get_langfuse_public_key/secret_key/host` (host reads `LANGFUSE_BASE_URL` or SDK-native `LANGFUSE_HOST`; EU cloud default) and `langfuse_tracing_enabled()` (honors `LANGFUSE_TRACING_ENABLED=false`). |
| `neova/app.py` | `observability.shutdown()` in the lifespan shutdown (flush traces before exit). |
| `tests/conftest.py` (new) | Autouse fixture strips all `LANGFUSE_*` env vars: the suite can never construct a client or emit network traffic. |
| `tests/test_observability.py` (new) | 9 tests: disabled-by-default no-ops, turn/step wiring and propagation with a fake client (session set, **no user/customer fields**), graceful disable on client failure, shutdown flush, plain-OpenAI fallback, and `name` never sent to OpenRouter when tracing is off. |
| `pyproject.toml` / `uv.lock` | `langfuse>=4.15.6` (Python SDK v4, observations-first API: `start_as_current_observation`, `propagate_attributes`). |
| `.env.example` | Langfuse section documents the enabled/disabled contract and `LANGFUSE_TRACING_ENVIRONMENT`. |

## Live audit (2026-09-25, budget-gated)

Two `/agent/chat` turns (internet incident, termination) plus a two-turn session (moving + fees), with `USAGE_LOG` set. Traces fetched via `GET /api/public/v2/observations` and audited against the trace best-practices page:

- **Structure:** one trace per turn, root `agent-chat` carrying the user message → final French reply; steps nested as siblings (`classify-intent` span + generation, `read-customer-summary`, `read-incidents`, `retrieve-context` (retriever), `generate-response` (generation), `create-handoff` (tool)). Verified across four traces.
- **Sessions:** two turns on one demo session token grouped under one `sessionId` (2 traces, 11 observations) — Sessions replay works.
- **Generations:** model (`mistralai/mistral-small-3.2-24b-instruct`), token usage (271 in / 52 out) and auto-computed cost ($0.0000384) captured on the successful answer; errored classifier calls carry `level=ERROR` + the upstream status message and cost 0 (a failed call is not spend).
- **Errors visible:** the classifier 404 appears as an ERROR generation with the exact OpenRouter routing failure — observability did its job (see finding below).

Audit fixes applied during the loop: none required beyond what was built — types, nesting, naming, root I/O, model/usage/cost and session grouping all matched best practices.

## Findings surfaced by the traces (pre-existing app behavior, not tracing bugs)

1. **Classifier 404 on every live turn.** `CLASSIFIER_MODEL=openai/gpt-4.1-nano` with `extra_body={"provider": {"require_parameters": true}}` fails OpenRouter routing at "Filter by Parameters"; the graph degrades to the keyword router exactly as designed. Decision needed (out of this change's scope): keep `require_parameters` (usage-accounting guarantee, issue #5/#6 decision) and pick a classifier model whose endpoints support it, or relax the flag for that model.
2. **Live retrieval index empty** (`retrieve-context` recorded `degraded=index_empty`, 0 results): this run's database had no indexed corpus, so live answers carried no citations. The eval issue (#7) covers indexing; noted here because traces expose it clearly.
3. Unexplained external `GET /v1/models` polls hit the local server (404s) — no code path in this repo issues them; harmless, not a tracing artifact.

## Offline guarantee

`uv run --locked --extra dev python -m pytest tests -q` → **179 passed** (170 pre-existing + 9 new), no network: tracing off by default in tests, `conftest.py` strips credentials, and the OpenAI drop-in is only constructed when keys are configured.

## Commands

```powershell
# Run with tracing (keys in .env):
uv run --locked --env-file .env -- python main.py
# → one Langfuse trace per /agent/chat turn in the configured project

# Fetch recent observations (Langfuse Cloud v4 API):
# GET $env:LANGFUSE_HOST/api/public/v2/observations?fromStartTime=...&fields=core,basic,io,model,usage,metadata,trace_context
```