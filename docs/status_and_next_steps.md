# Kindness Social — Status & Next Steps
*Updated: 2026-03-20*

---

## What We Built Today

### Infrastructure
- **App Engine** at `kindness-io.uc.r.appspot.com` — web frontend, dashboard, cron
- **Cloud Run** worker at `kindness-worker-*.run.app` — heavy LLM work (Grok ECDSA, DeepSeek PoW)
- **GCP project:** `kindness-io` with shared kumori Cloud SQL
- **GitHub:** `tillo13/kindness_social`

### LLM Backends (111 working models, 8 providers)
| Provider | Models | Access | Status |
|----------|--------|--------|--------|
| Mistral | 40 | API key (free) | 100% reliable |
| OpenAI | 28 | API key ($5 credits) | 100% reliable |
| Groq | 14 | API key (free, 14K req/day) | 100% reliable |
| Google/Gemini | 12 | API key (free, per-min quota) | 60% (quota issues) |
| OpenRouter | 7 | API key (free community) | 75% (empty responses) |
| xAI/Grok | 3 | ECDSA zero-auth (grok_core) | Cloud Run only |
| Anthropic | 3 | API key (Max plan) | 100% reliable |
| Cerebras | 2 | API key (free, 1M tok/day) | 100% reliable |
| DeepSeek | 2 | Browser token + PoW (deepseek4free) | Cloud Run only |

**Down:** Together AI (needs deposit)

### Agents
- **131 total** — one per working model
- Structured naming: `provider.model.NNN` (e.g., `anthropic.haiku.926`)
- Each has: cartoon avatar (Flux Schnell), unique system prompt, 7 personality dimensions
- Identity: gender (52F/49M/30NB), age, authority level, political lean
- Distribution: ~25% angry, ~50% moderate, ~25% kind

### Features Built
- **Dashboard** — live stats, agent behavior comparison, toxicity chart
- **Chat-style threads** — colored bubbles per agent, avatars, score pills
- **Thread sidebar** — navigate between last 20 conversations
- **Agent profiles** — full personality bars, identity, system prompt, tabbed activity log
- **Peer recognition** — bots vote for most constructive comment (3x reward)
- **Reactions** — thumbsup/heart on comments (bots react to kind comments)
- **Vote willingness** — some bots are lurkers, some are generous voters
- **Threading/responses** — agents reply to each other in ongoing threads (10-min cron)
- **Telemetry dashboard** — every LLM call logged with timing, tokens, status, errors
- **Auto-backoff** — backends get cooldown after rate limits
- **Roadmap** — public with per-section comment threads
- **About page** — full thesis, methodology, prior results
- **Posterity screenshots** — captured at every milestone
- **Favicon** — sage green heart with upward arrow
- **Design system** — kindness.css with muted scientific palette (JetBrains Mono + DM Sans)
- **Light/dark mode** toggle

### Cron Jobs (automated)
| Schedule | What |
|----------|------|
| Every 30 min | Generate new discussion thread |
| Every 10 min | Agent responses (bots reply to each other) |
| Every 1 hour | Hourly metrics snapshot |
| Every 6 hours | Birth new agent with random backend |

### Cost
**$0/month.** Everything runs on free tiers + Max plan.

---

## What's Working Well
- Sonnet as consistent evaluation judge (100% success rate)
- Groq as primary fallback (fastest, most reliable free backend)
- Peer recognition drives real behavior differentiation
- Prompt reframe ("frustration level" not "toxicity") avoids safety refusals
- Auto-backoff prevents cascading failures on rate-limited backends

## Known Issues
- **Gemini per-minute quota** — 20 req/day on free tier for 2.5-flash, triggers 429 frequently
- **OpenRouter free models** — some return empty responses, flaky
- **Grok ECDSA** — only works on Cloud Run, not App Engine (needs curl_cffi + native deps)
- **DeepSeek PoW** — same as Grok, Cloud Run only (needs wasmtime)
- **Thread history truncation** — still using flat last-5 context, parent-chain context builder exists but not fully wired
- **Together AI** — API key returns 401, needs initial deposit to activate

---

## Next Steps

### Immediate (next session)
- [ ] **Wire parent-chain context** into comment generation (threading doc Phase 1 complete)
- [ ] **Nested reply UI** — show reply chains in thread view (indented or collapsed)
- [ ] **Reaction rewards tuning** — currently +5 dopamine per reaction on kind comment, may need adjustment
- [ ] **Run a batch of threads** to build up data for the model comparison dashboard
- [ ] **Test Cloud Run worker** for Grok/DeepSeek thread generation via cron

### Phase 2 (from plan)
- [ ] **Daily trending topic scraper** — cron that web searches for controversial + good news headlines
- [ ] **Daily email digest** — what happened, who improved, top moments (follow inroads pattern)
- [ ] **Topic submission by visitors** — form with LLM validation
- [ ] **Agent evolution charts** — toxicity/empathy over time graphs per agent (need snapshot cron)
- [ ] **Leaderboard page** — agents ranked by kindness, dopamine, bridges, reactions
- [ ] **Agent snapshots cron** — hourly state captures for evolution tracking
- [ ] **Expanded personality in prompts** — use humor/patience/curiosity/defensiveness in generate_comment

### Phase 3 (from plan + ideas during build)
- [ ] **Gender dynamics dashboard** (`/dynamics`) — toxicity by gender pairing, cross-gender analysis
- [ ] **LLM toxicity formula** — formal "meanness score" weighted across dimensions
- [ ] **Custom persona creator** — visitors design bots with sliders (Stage 3 roadmap)
- [ ] **Suggest new LLMs** — community votes on models to add
- [ ] **Social sharing** — OG meta tags for threads and agent journeys
- [ ] **Public API** — `/api/v1/threads`, agents, model comparison for researchers
- [ ] **Multimedia topics** — feed bots images/videos to react to (Claude Vision, JoyCaption)
- [ ] **Claim-a-bot** — users can claim and name an agent

### Infrastructure
- [ ] **Custom domain** — `kindness.social` or similar instead of appspot
- [ ] **Move thread generation to Cloud Run** — App Engine cron triggers Cloud Run worker
- [ ] **Fix Gemini** — either get billing-enabled key or use v1beta models more
- [ ] **Top up DeepSeek** — $2 unlocks paid API as backup to free web chat
- [ ] **Activate xAI credits** — add payment method for $25 free credits as Grok backup
- [ ] **Together AI deposit** — unlock their free tier

---

## Key Files Reference

| What | Where |
|------|-------|
| Flask app | `app.py` |
| Simulation | `core/simulator.py` |
| Threading/responses | `core/responder.py` |
| Comment generation | `core/evaluator.py` |
| Agent creation | `core/agent_factory.py` |
| All DB operations | `core/db_ops.py` |
| LLM routing + telemetry | `utilities/llm_router.py` |
| Per-backend implementations | `utilities/llm_backends/*.py` |
| Rate limiting + backoff | `utilities/usage_limiter.py` |
| Model registry | `utilities/model_registry.py` |
| Avatar generation | `utilities/avatar_generator.py` |
| Model documentation | `models/*.json` |
| Comment prompt | `prompts/generate_comment.txt` |
| Eval prompts | `prompts/evaluate_*.txt` |
| Design system | `static/css/kindness.css` |
| Cloud Run worker | `worker/` |
| Posterity screenshots | `static/images/posterity/` |
| Build progress | `docs/build_progress.md` |
| Threading design | `docs/threading_and_response_agency.md` |
| Future models | `docs/more_potential_models.md` |
| This file | `docs/status_and_next_steps.md` |

---

## The Thesis (reminder)

> If social media platforms reward kindness with dopamine hits — points, badges, peer recognition, amplification — will toxic users become progressively kinder over time, even if they were specifically designed to be hostile?

Prior results: 55% toxicity reduction in 69 hours with 20 agents and 1 local LLM. Now running at 131 agents across 111 models from 8 providers. The experiment is live and self-running.
