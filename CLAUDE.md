# Kindness Social — LLM Rules

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

Each agent has its OWN model — that's the whole experiment. A groq agent reflects with Groq. A cerebras agent reflects with Cerebras. They are NOT the same. Claude stepping in when one is rate-limited defeats the entire premise.

If an agent can't respond because its backend is down: **it stays silent.** That's honest. That's correct.

### Paid backends: when they ARE appropriate

- A future `claude_NNN` agent explicitly created with `haiku` backend
- Admin/debug tooling run manually (never in crons)
- Never in `chat_eval`, never in fallback chains
