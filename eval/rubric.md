# Evaluation rubric — fixed set (issue #7)

**Scope:** the 12 French cases in `eval/cases.json`, run against the bounded conversation graph. Expected outcomes were declared before the first measured run and are never re-fitted to observed behavior; deviations are reported as failures or as narrow fixes with their failing case. No commit is made during this request.

## Modes and what they measure

| Mode | Chat model | Classifier | Embedder | What the numbers mean |
| --- | --- | --- | --- | --- |
| `offline` (default) | `FakeChatModel` (canned French reply, records prompts; exhaustion cases use the real policy with fault-injected transport) | keyword fallback router (`classifier_factory → None`) | `FakeEmbedder` (deterministic bag-of-words) over the actual supplied public corpus | Routing, gates, tool use, booking state, degradation and reply *shape* — **not** French prose quality or real-model grounding. |
| `--live` (opt-in, budget-gated) | Real `CHAT_MODEL` via the bounded provider policy | Real classifier | Real embeddings | Actual model behavior on the same expected outcomes. 429/529 faults remain deterministic injections; a live 429 is recorded only if incidentally observed. |

Failures are never replaced by projections; each mode's numbers are labeled.

## Pass criteria per check

A case **passes** when every declared check passes. Checks come straight from the case's `expect` block:

| Check | Pass means |
| --- | --- |
| `route` | First-turn `route` equals the declared value (keyword-router route offline); subsequent route changes in booking flows are retained in observed `routes`. |
| `tools_include` | Every listed tool name appears in the *union of all turns'* `tools_called`. Tools are recorded even on failure (`customer_summary.read`, `handoffs.create`), so a failed read still counts as "attempted". |
| `tools_exclude` | None of the listed tool names appear in `tools_called`. |
| `gate_flags` | Every listed evidence-gate code (`archived_pricing`, `fee_timing_conflict`, `invoice_line_items_absent`, `non_contractual_source`) is present in the final result's `gate_flags`. |
| `citations_present` / absent | The final result's `citations` list is non-empty / empty as declared. |
| `reply_must` | Every substring appears in the final reply (case-insensitive). |
| `reply_must_not` | None of the substrings appear in the reply (case-insensitive) — used to forbid invented amounts, success claims and private-field disclosures. |
| `degraded_include` | Every listed degradation marker appears in `degraded` (`api_read_failed`, `chat_model_unavailable`, …). |
| `handoff_stored` | `handoff_id is not None` matches the declared boolean (a stored handoff record exists / does not exist). |
| `booking_saved` | The appointment exists / does not exist in the database for the session customer (checked directly in SQLite, not from prose). |

## Category scoring

- **routing** — correct named route; scoped reads attempted exactly as the route allows; no state-changing tool on a read-only route.
- **grounding** — answers carry citations from the public corpus; contradiction and absence cases end in the declared uncertainty notice, and must-not substrings forbid invented amounts or a resolved conflict.
- **booking** — a saved booking only after the exact `CONFIRMER RDV <code>` phrase; a bad slot never books and changes no state; every booking case records `appointments.book` exactly when the outcome is claimed.
- **handoff** — termination answers first then hands off; sensitive topics hand off immediately without reads; anonymous turns get the generic human route and store nothing.
- **resilience** — API-500 read recovery and OpenRouter 429/529 exhaustion end in the short French safe reply with a handoff offer, with degradation recorded; retrieval is skipped after a failed essential read.
- **privacy** — anonymous case in the fixed set; the deeper negative matrix (cross-session reads, internal-document exclusion, retrieved-content injection resistance, payload minimization, redaction) lives in `tests/test_security.py`.

## Failure handling

- Every failing case stays visible in `eval/results.md` with its expected-vs-observed values and a one-line error analysis; `eval/results.json` is machine-checkable (`passed: true/false` per check).
- Raw user messages, session tokens, account data and replies are never written to results; only safe check names, expected fragments, presence/absence checks, route/tool names, source IDs, flag codes and booking/handoff booleans are recorded.
- A failing case is either (a) reported as a failure with analysis, or (b) fixed narrowly with its failing test recorded in `docs/issue-7-evaluation-security-report.md` — never by editing the expectation to fit the observation.
- Known limits (restated per run): offline runs do not measure live-model grounding; 429/529 are injected, not live-reproduced; injection resistance is asserted at the deterministic-boundary level, not as live-model immunity.
