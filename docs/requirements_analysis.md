# Néova customer-relations agent — requirements analysis

Scope: analysis of `take_home_relation_client.md`, the file inventory in `corpus/`, the visible PNG scan, and `data/neova_data.json`. **No PDFs were opened or interpreted.** This is a requirements and evidence inventory, not an implementation proposal. The evidence column describes what a submission would need to demonstrate; it does not claim that such evidence exists yet.

## Supplied material inventory

| File(s) | Observable contents / limits | Source |
| --- | --- | --- |
| `take_home_relation_client.md` | Assignment brief: context, build, supplied inputs, OpenRouter constraints, deliverables and optional bonuses. | Sections “Context” through “AI assistance” |
| `corpus/faq-box-internet.pdf`, `corpus/faq-facturation.pdf`, `corpus/faq-espace-client.pdf`, `corpus/faq-retour-equipement.pdf` | Four PDF files; topic labels are **inferred from filenames only**. | `corpus/` filenames; brief §“What we provide” |
| `corpus/cgv-resiliation.pdf`, `corpus/grille-tarifaire-2026.pdf`, `corpus/promo-rentree-2024.pdf` | Three PDF files; apparent terms, tariff and promotion topics are **filename-only inferences**. Dates in filenames do not establish current validity. | `corpus/` filenames; brief §“What we provide” |
| `corpus/procedure-demenagement.pdf`, `corpus/procedure-escalade-n2.pdf`, `corpus/politique-geste-commercial.pdf` | Three PDF files; apparent moving, escalation and commercial-gesture topics are **filename-only inferences**. | `corpus/` filenames; brief §“What we provide” |
| `corpus/fiche-roaming-international-scan.png` | One readable image scan, marked ref. FIC-ROAM-2026-02, updated 27 February 2026, superseding FIC-ROAM-2025-04; says it is non-contractual. Sections: “Union européenne, DOM inclus” (allowances and excess charges), “Hors Union européenne” (rates and billing lag), “Blocage préventif”, “Contestation des consommations hors forfait” (discount requests belong to an advisor). These are document claims, not independently verified policy. | PNG, header, four named sections and footer |
| `data/neova_data.json` | `_note` says EUR, ISO-8601 dates and Europe/Paris. Six customers with plans, commitments, addresses, balances, equipment and invoice histories; three network incidents; twelve technician slots; four appointment reasons; five escalation categories; empty `appointments` and `tickets` arrays. Customer identifiers, names, phone numbers and addresses are personal data. | JSON keys `_note`, `customers`, `network_incidents`, `technician_slots`, `appointment_reasons`, `escalation_categories`, `appointments`, `tickets` |

The eleven corpus entries are **ten PDFs and one PNG**, not eleven PDFs. No PDF content, priority order, contradictions, or unsupported questions have been verified here. `data/neova_data.json` contains incident and slot dates in August–September **2026**; whether these are fixed simulation dates or meant to be live/current is unspecified. The JSON currently contains no appointment or ticket records. See its `customers[*].last_invoices`, `network_incidents[*].started_at` / `estimated_resolution`, `technician_slots[*].start` / `end`, `appointments` and `tickets`.

## Requirement traceability matrix

“Must” means expressly required by the brief; “bonus” means explicitly optional. Evidence is a proposed acceptance artifact, not an additional mandated deliverable.

| ID | Priority | Requirement / acceptance intent | Source (exact file, brief section) | Evidence required to prove it |
| --- | --- | --- | --- | --- |
| R1 | Must | A conversational agent handles pre-advisor customer requests, responds **in French**, and recognizes when to hand over. | `take_home_relation_client.md` §“Context” | French conversation transcripts covering the stated request types (internet failure, billing, moving, appointments, termination), including at least one handover. |
| R2 | Must | Use an **agentic graph in LangChain or LangGraph**. | `take_home_relation_client.md` §“What to build” opening and item 3 | Executable graph definition and a documented graph sketch; a run showing its routing/steps. |
| R3 | Must | Include retrieval over the supplied `corpus/` knowledge base. | `take_home_relation_client.md` §“What to build” item 1; §“What we provide”; `corpus/` inventory above | Demonstrable ingestion/retrieval of corpus sources, with retrieval results linked to originating file; evidence the PNG is not silently omitted if corpus-wide coverage is claimed. PDFs must be inspected later to confirm content and coverage. |
| R4 | Must | Implement at least one custom agent tool; expose relevant `data/` through a small **FastAPI** service and wrap that service as an agent tool. | `take_home_relation_client.md` §“What to build” item 2; `data/neova_data.json` top-level keys | API routes documented/tested using supplied records; a graph run invoking the wrapped tool and using its returned data. |
| R5 | Must | At least one API endpoint changes state by booking a technician appointment; determine and demonstrate validation, confirmation and failure handling. | `take_home_relation_client.md` §“What to build” item 2; `data/neova_data.json` keys `technician_slots`, `appointment_reasons`, `appointments` | Before/after booking state; successful confirmation; rejected invalid/unavailable/conflicting booking; failure outcome without false confirmation. Exact rules are left to the candidate. |
| R6 | Must | Provide a path to a **human advisor**. | `take_home_relation_client.md` §“What to build” item 3; `data/neova_data.json` key `escalation_categories` | Conversation and graph trace showing escalation rather than unsupported autonomous resolution, with a usable handoff signal/record. Brief does not mandate a specific ticket schema or live human integration. |
| R7 | Must | Handle contradictory corpus documents and questions with no answer in the corpus. | `take_home_relation_client.md` §“What to build” final paragraph | Evaluation cases with a documented contradiction and an unanswerable question, expected safe outcomes and observed outcomes. Identification of actual contradictory passages requires later PDF review. |
| R8 | Must (design to justify) | Make explicit graph/chunking/escalation decisions and define what happens when the API returns HTTP 500. | `take_home_relation_client.md` §“What to build” design paragraph | Brief rationale plus run/test showing API-500 behavior and no unsupported success claim. Chunking and escalation policy are choices, not prescribed values. |
| R9 | Must | Use only the provided OpenRouter key for AI assistance **and** solution models; stay within its $10 hard cap. No other account, subscription, free tier or personal key. | `take_home_relation_client.md` §“OpenRouter”; §“AI assistance” | Configuration/account provenance and spending record for the supplied key; no alternative credentials or provider use. Do not disclose the key in evidence. |
| R10 | Must | Choose OpenRouter models suitable for French; name chosen models in README; model names are environment variables, not hard-coded. Keep the key out of commits; provide `.env.example` only. | `take_home_relation_client.md` §“OpenRouter” model/key bullets | README names and environment variable documentation, config inspection, safe `.env.example`, and repository/commit-history secret check. |
| R11 | Must | Handle OpenRouter `429` and `529` load responses with retries, backoff and provider fallback; log token usage and report approximate spend. | `take_home_relation_client.md` §“OpenRouter” reliability/spend bullets | Simulated 429/529 tests or traces showing retry/backoff/fallback and safe terminal behavior; token-usage logs and approximate spend report. Fallback must still respect R9. |
| R12 | Must | Deliver a git repository with **one documented command** that runs on a clean machine. | `take_home_relation_client.md` §“Deliverables” first bullet | Reproducible clean-machine run following the single documented command and stated prerequisites. |
| R13 | Must | Build a small evaluation set, provide a way to measure performance, report actual numbers **including failures** and error analysis. | `take_home_relation_client.md` §“Deliverables” second bullet | Evaluation cases, scoring criteria, reproducible execution, numerical results, failed-case list and analysis. No target accuracy percentage is specified. |
| R14 | Must | README of at most two pages: run instructions, graph sketch, evaluation numbers, **three** design decisions with trade-offs, known breakage, and two-more-days plan. | `take_home_relation_client.md` §“Deliverables” third bullet; §“OpenRouter” model/spend bullets | README length/content review plus linked run/evaluation evidence and model/spend disclosure. |
| R15 | Expected review criterion | Maintain an honest, inspectable commit history; “15 honest commits” is an illustration, **not** a required count. Own and be able to explain/modify every line during the 90-minute debrief. | `take_home_relation_client.md` §“Deliverables” commit-history sentence; §“AI assistance” final paragraph; header | Commit log with meaningful progression; candidate can explain decisions and make a live change. No numeric commit threshold is stated. |
| R16 | Bonus | Groundedness checking, refusal behavior and resistance to retrieved-content prompt injection. | `take_home_relation_client.md` §“Bonus” first bullet | Targeted adversarial/unanswerable tests and observed refusal/grounding outcomes, if claimed. |
| R17 | Bonus | Reranking, hybrid retrieval or multi-query retrieval. | `take_home_relation_client.md` §“Bonus” second bullet | Retrieval comparison/ablation on evaluation cases, if claimed. |
| R18 | Bonus | Langfuse tracing/observability: graph traces, token cost and latency per node; ideally evaluation runs traced. | `take_home_relation_client.md` §“Bonus” third bullet | Trace examples with node-level usage, cost and latency; evaluation linkage if claimed. |
| R19 | Bonus | Another justified enhancement may substitute for listed bonuses; depth matters more than number. | `take_home_relation_client.md` §“Bonus” final bullets/paragraph | README argument and focused evidence for the claimed enhancement. |

**Conditional interface note:** If embeddings are used, the brief specifies the OpenRouter `/embeddings` endpoint (not streamed) with the same key/base URL; chat uses `/chat/completions`. It does **not** require embeddings specifically. Source: `take_home_relation_client.md` §“OpenRouter” API bullet.

## Must-have versus bonus

- **Must-have:** R1–R14, including safe treatment of contradiction/absence, a real booking mutation, failure handling, evaluation failures and single-key budget constraints.
- **Expected review criterion, not a prescribed count:** R15. Timing in the header (“2 days of work”, “7 calendar days to return”, “90-minute debrief”) is assignment context, not an in-app feature.
- **Optional bonus:** R16–R19. The brief explicitly says none is required and prefers one completed bonus to several sketches (`take_home_relation_client.md` §“Bonus”).

## Ambiguities / questions to resolve

1. **Corpus authority and contradictions:** Which document outranks another when dates or claims disagree (contractual terms vs FAQ vs tariff vs promotion vs scan)? Which specific passages conflict? The brief flags contradictions but no PDF was read (`take_home_relation_client.md` §“What to build”; `corpus/` PDF filenames; PNG header/footer).
2. **Corpus coverage:** Does “retrieval over `corpus/`” require all eleven items, including the scanned PNG, and what counts as adequate text extraction? (`take_home_relation_client.md` §“What to build” item 1; §“What we provide”; `corpus/fiche-roaming-international-scan.png`).
3. **Handoff semantics:** Is an emitted escalation decision sufficient, or must an actual persisted ticket/human queue exist? `tickets` starts empty and categories are supplied, but the brief does not prescribe a workflow (`take_home_relation_client.md` §“What to build” item 3; `data/neova_data.json` keys `tickets`, `escalation_categories`).
4. **Booking semantics:** Who may book, how is identity checked, is explicit user confirmation mandatory, may the same customer book twice, and what persistence/idempotency is expected across restarts? The candidate is explicitly asked to decide validation/confirmation/failure handling (`take_home_relation_client.md` §“What to build” item 2; `data/neova_data.json` keys `technician_slots`, `appointments`).
5. **Time model:** Are August/September 2026 dates a frozen scenario, or must stale slots be rejected relative to runtime? What is the intended evaluation date? (`data/neova_data.json` `_note`, `network_incidents`, `technician_slots`).
6. **Provider fallback:** Does “provider fallback” mean an alternate model/provider routed **within the same supplied OpenRouter key**, and how will this be reconciled with “nothing else”? The latter forbids a second provider account (`take_home_relation_client.md` §“OpenRouter” reliability bullet; §“AI assistance” single-key condition).
7. **Evaluation expectations:** No minimum score, required case count or rubric is given; how much of the PDF content and which failure types will reviewers test? (`take_home_relation_client.md` §“Deliverables” evaluation bullet; §“What to build” final paragraph).
8. **Privacy/authentication:** Customer records include phone, address and billing details, but the brief gives no authentication, consent or redaction policy. What access constraints are expected in a demo? (`data/neova_data.json` `customers`; `take_home_relation_client.md` §“What to build” API freedom).
9. **README page count:** Page size/rendering is undefined for a Markdown file. The two-page limit is explicit, but how it is measured is not (`take_home_relation_client.md` §“Deliverables”).

## Facts versus assumptions / unverified claims

| Established by inspected material | Assumption or unverified claim (do not treat as established) |
| --- | --- |
| The brief calls for LangChain **or** LangGraph, FastAPI, a state-changing booking endpoint and human escalation (`take_home_relation_client.md` §“What to build”). | A particular graph topology, database, endpoint shape, UI, identity protocol or persistence strategy is required. |
| The directory has eleven corpus files: ten PDFs and one PNG (`corpus/` filenames). | Any PDF's actual wording, currency, page count, document authority, or a specific pair of contradictory passages. |
| The PNG has a dated “Version en vigueur” header and a “document non contractuel” footer (`corpus/fiche-roaming-international-scan.png`, header/footer). | That the scan is legally authoritative today or overrides unseen PDFs. |
| The JSON has six customers, three incidents, twelve slots, four appointment reasons, five escalation categories, and empty appointments/tickets (`data/neova_data.json`, named keys). | That all incidents/slots are current, that balances are live, or that a booking is already stored. |
| The brief requires a single supplied key, $10 cap, rate-limit resilience and token/spend reporting (`take_home_relation_client.md` §“OpenRouter”; §“AI assistance”). | That credentials are present locally, budget remains, or the evaluation has already run. |
| The brief asserts that contradictory documents and unanswerable questions exist (`take_home_relation_client.md` §“What to build” final paragraph). | That this analysis has identified their exact instances; PDFs were deliberately skipped. |

## Submission checklist (future verification, not work completed)

- [ ] Ensure this analysis file is included in the submitted repository: the existing `.gitignore` ignores `docs` (`.gitignore`, final line). This analysis did not modify ignore rules or stage files.
- [ ] Review all ten PDFs later; record actual sections, contradictions, dates/authority and unsupported test questions (`corpus/*.pdf`; R3, R7).
- [ ] Demonstrate French graph runs, corpus retrieval, FastAPI-backed tool, state-changing booking, and human escalation (R1–R6).
- [ ] Show contradiction/no-answer behavior and API-500 failure behavior, with explicit design rationale (R7–R8).
- [ ] Verify same supplied OpenRouter key throughout, $10 cap discipline, env-configured model names, no committed secret, 429/529 resilience, usage logs and spend report (R9–R11).
- [ ] Verify one clean-machine run command and a small reproducible evaluation with actual numbers, failures and error analysis (R12–R13).
- [ ] Verify README ≤2 pages with graph sketch, model names, eval, three decisions/trade-offs, known breakage and two-more-days plan (R10, R14).
- [ ] Review meaningful commit history and prepare to justify/modify code at debrief (R15).
- [ ] If any bonus is claimed, attach focused evidence and explain its value in README (R16–R19).
