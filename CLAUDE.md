# Kindness Social — LLM Rules

**📋 Always read `next_steps.md` at the repo root first** — it's auto-maintained nightly with the latest shipped commits, pending queue (from `deploy --next`), and any uncommitted WIP or TODO markers.

## Claude / Anthropic API: NEVER as a fallback

Claude (haiku/sonnet/opus) costs real money and must NEVER be used as a fallback or catch-all.

**Claude is ONLY allowed when an agent is explicitly assigned `haiku`, `sonnet`, or `opus` as their `llm_backend` in the database.** No agent should ever be assigned a paid backend — see `agent_factory.py` `AVAILABLE_BACKENDS`.

### Where this applies

| Location | Rule |
|---|---|
| `llm_router.chat()` | Already correct — each agent uses only its own assigned backend, stays silent on failure |
| `llm_router.chat_eval()` | Free pool only (`EVAL_POOL_FREE`). If all free backends are down, skip the eval — do NOT fall back to haiku |
| `reflector.py` | Uses `chat(backend, ...)` with the agent's own backend — correct, keep it that way |
| `evaluator.py` | Uses `chat_eval()` — free only, skip score if all backends are down |
| `agent_factory.py` | `AVAILABLE_BACKENDS` must never include `haiku`, `sonnet`, `opus`, `gpt4o`, or any paid backend |

### The point of this project

Each agent has its OWN model — that's the whole experiment. A groq agent reflects with Groq. A cerebras agent reflects with Cerebras. They are NOT the same.

**An agent MUST always use its exact assigned backend. No exceptions. No substitutions.**

- Backend rate-limited? Agent waits and stays silent.
- Backend quota exhausted? Agent stays silent until it resets.
- Backend temporarily down? Agent stays silent.

Never substitute a different model — that would change who the agent *is*. The wait is correct. The silence is honest.

### Paid backends: when they ARE appropriate

- A future `claude_NNN` agent explicitly created with `haiku` backend
- Admin/debug tooling run manually (never in crons)
- Never in `chat_eval`, never in fallback chains
