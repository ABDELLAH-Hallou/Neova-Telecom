# Take-home — AI Engineer, Customer Relations Agent

**2 days of work. 7 calendar days to return it. Followed by a 90-minute debrief.**

---

## Context

Néova Télécom is a fictional French ISP and mobile operator. Its contact centre handles calls
about internet failures, billing, moving house, technician appointments and contract
termination.

Build the conversational agent that handles these requests before a human advisor gets
involved — and that knows when to stop and hand over. It answers in French.

---

## What to build

An agentic graph in **LangChain or LangGraph** containing:

1. **A retrieval component** over the knowledge base in `corpus/`.
2. **At least one tool you implement yourself.** Expose the data in `data/` as a small
  **FastAPI** service, then wrap it as an agent tool. At least one of your endpoints must be
   *state-changing* (booking a technician appointment) — decide yourself what that implies for
   validation, confirmation and failure handling.
3. **An escalation path** to a human advisor.

Design the rest. What the graph looks like, how you chunk, when you escalate, what happens
when the API returns a 500 — these are the interesting decisions and they're yours.

Two things the material will do to you: the corpus contains **contradictory documents**, and
some questions have **no answer in it at all**. Handle both.

---



## What we provide


| Path                   | Contents                                                                                                                                      |
| ---------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| `corpus/`              | 11 French support documents — FAQ, CGV extracts, tariffs, internal procedures. Mixed formats, as they came out of the systems that held them. |
| `data/neova_data.json` | Customers, contracts, network incidents, technician slots                                                                                     |


Nothing else is specified. Model the API however you think is right.

---



## OpenRouter

You get **one key with a hard cap of $10**, for both your coding assistant and the models inside your solution.

- OpenAI-compatible, base URL `https://openrouter.ai/api/v1`. Chat on `/chat/completions`,
embeddings on the dedicated `/embeddings` endpoint — same key, same base URL.
Note that **embeddings do not stream**.
- **Any model on OpenRouter is fair game**, within the cap. Pick ones that handle French
well — not all embedding models do — and name what you chose in your README, so that
submissions stay comparable.
- Put model names in **environment variables**, not in your code. We will swap them.
- Never commit the key. `.env.example` only.
- Expect `429` and `529` under load. Retries, backoff and provider fallback are your problem
and part of what we look at.
- Log your token usage and report roughly what you spent. You can track your own budget
without access to our dashboard:

  ```bash
  curl -s https://openrouter.ai/api/v1/key \
    -H "Authorization: Bearer $OPENROUTER_API_KEY"
  ```

  It returns your key's spend and cap, in US dollars:

  ```json
  {"data":{"label":"take-home","usage":2.41,"limit":10,"limit_remaining":7.59,"is_free_tier":false}}
  ```

  Piped through `jq` for just the two numbers you care about:

  ```bash
  curl -s https://openrouter.ai/api/v1/key \
    -H "Authorization: Bearer $OPENROUTER_API_KEY" | jq '.data | {usage, limit, limit_remaining}'
  ```

See the AI assistance section below for tooling and the single-key rule.

---



## Deliverables

A git repo with:

- one documented command that runs it on a clean machine (`docker compose up` or `uv run ...`);
- **a way to measure whether it works.** Build yourself a small evaluation set and report the
numbers, including the failures. This carries real weight — we would rather see 70% with a
clear error analysis than a claim of 100% with nothing behind it;
- a **README, max 2 pages**: how to run it, a sketch of the graph, your eval numbers, three
design decisions and what you traded away for each, what's broken, and what you'd do with
two more days.

Commit history is looked at. 15 honest commits beat one called "initial commit".

---



## Bonus (pick what interests you — none of it is required)

- Guardrails: groundedness checking, refusal behaviour, resistance to injection via retrieved content
- A reranker, hybrid or multi-query retrieval
- **Langfuse** for tracing and observability — traced graph runs, token cost and latency per
node, and ideally your evaluation runs wired into it
- Anything else you think matters more than the above — argue for it in the README

Depth beats breadth. One bonus done properly is worth more than four sketched.

---



## AI assistance

Use it freely — that's partly what the credits are for, and it's how we work. We suggest
**OpenCode** (`opencode.ai`), which has native OpenRouter support. Any other tool is fine —
Claude Code, Codex CLI, Cursor, Cline, your own script. Setting it up is your problem.

One condition: **everything runs on the key we gave you and nothing else.** No personal
subscription, no second provider account, no free tier on the side, and no "sign in with
OpenRouter" flow on an account of your own — paste the key. Same key for your assistant and
for the models inside your solution, so that what you spent is visible and comparable across
candidates.

**You own every line.** In the debrief we'll ask you to modify your code live and justify your
choices.

Questions are welcome and cost you nothing: `mkamaleddine@zaion.ai`.