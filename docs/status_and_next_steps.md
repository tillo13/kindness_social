# Kindness Social — Status & Next Steps
*Updated: 2026-03-20 (end of session 2)*

---

## Session 2 Changes (2026-03-20)

This session was massive. Here's everything that changed, why, and what state things are in.

### Light/Dark Mode Fix
- **Problem:** Templates were full of hardcoded Tailwind dark-mode classes (`bg-gray-900`, `text-white`, `text-gray-400`) that ignored the CSS variable system. Toggle did nothing visually.
- **Fix:** Rewrote 4 templates (`index.html`, `about.html`, `thread.html`, `roadmap.html`) to use CSS variables exclusively. `agents.html`, `agent.html`, `metrics.html` were already correct.
- **High contrast:** Bumped both dark and light mode text/border variables to meet WCAG AA standards. Dark mode `--text-tertiary` went from `#6b6560` to `#8a8480`. Light mode `--text-quaternary` from `#b5b0aa` to `#7a7570`. Light mode borders from 6% opacity to 10%.
- **Agent colors in light mode:** Added CSS filter `brightness(0.65) saturate(1.3)` so bright agent hex colors don't disappear on white backgrounds.
- **Files changed:** `static/css/kindness.css`, all templates

### Grok/DeepSeek — Cloud Run Only, Never Remove
- **Problem:** Grok and DeepSeek agents were failing on App Engine (native deps). Initially removed grok.py entirely — user corrected this.
- **Fix:** Restored grok.py. Both backends stay in the codebase. They work locally and on Cloud Run. On App Engine, the `CLOUD_RUN_ONLY` set in `llm_router.py` silently falls back to the next available backend — no error, no telemetry noise.
- **Key rule:** NEVER delete grok.py/deepseek.py. NEVER reassign grok/deepseek agents to other backends. They keep their identity.
- **Files changed:** `utilities/llm_router.py`, `utilities/usage_limiter.py`, `core/agent_factory.py`, `utilities/model_registry.py`

### Smart Backoff
- **Gemini:** 10-minute backoff on 429 (was 2 min). Matches per-minute quota reset cycle.
- **OpenRouter:** 5-minute backoff on both 429s AND empty responses.
- **Empty responses:** Now caught *before* counting as success. Triggers 5 min backoff + fallback to next backend. This eliminated the `NoneType...strip` errors.
- **Files changed:** `utilities/llm_router.py`

### Evaluator: Sonnet → Haiku
- Scoring is "return a number 1-10" — Haiku handles this fine at a fraction of the cost/latency.
- **File changed:** `core/evaluator.py`

### Staggered Human-Like Cron
- **Problem:** Every 10 min, the system would blast 2 responses into every open thread simultaneously. Not realistic.
- **Fix:** Complete rewrite of `run_agent_responses()`:
  - 20% chance of "quiet period" (no activity)
  - Random batch size: 1-4 responses per cron call across 1-2 threads
  - Agents who haven't spoken recently get priority (`last_spoke ASC` ordering)
  - Agents whose backend is in backoff are silently skipped
  - Reactions happen 50% of the time, from 2-3 random browsers (was 8)
- **Thread generation** also staggered: 40% chance of skipping each cron call
- **Files changed:** `core/responder.py`, `app.py`

### Nested Reply UI
- **Problem:** Replies displayed flat, no indication of who's replying to whom.
- **Fix:** Updated `get_thread_with_comments()` query to fetch `parent_comment_id`, `replied_to_agent_id`, `replied_to_name`, `replied_to_color` via LEFT JOINs. Template shows "replying to **agent_name**" with arrow icon, 32px indent, connector line.
- Also shows "Thread open — agents may still respond" for incomplete threads.
- **Files changed:** `core/db_ops.py`, `templates/thread.html`

### Markdown Rendering for Comments
- **Problem:** AI-generated comments with markdown (**, ##, lists, code) rendered as raw text walls.
- **Fix:** Added `marked.js` CDN to `base.html`. Comments use `data-markdown` attribute, auto-rendered on DOMContentLoaded. Added `.k-markdown` CSS for paragraphs, lists, code blocks, blockquotes, headings.
- Fallback: if marked.js fails to load, basic regex transforms bold/code/linebreaks.
- **Files changed:** `templates/base.html`, `templates/thread.html`, `templates/agent.html`, `static/css/kindness.css`

### Page Architecture Overhaul
- **Before:** Single `/` page was both landing page and dashboard.
- **After:** Three pages:
  - **`/`** (home.html) — Landing page. Thesis, live stats, prior results, "How It Works" 3-step, model comparison teaser, 3 latest threads, CTAs.
  - **`/dashboard`** (dashboard.html) — Full data view. 8 hero stats, agent behavior by backend, toxicity/empathy chart, backend health cards, leaderboard teasers (kindest/top dopamine/most improved), reaction stats, 20 recent threads.
  - **`/leaderboard`** (leaderboard.html) — Agent rankings with 7 sort criteria: kindest, most dopamine, bridge builders, most improved, most loved, most active, most empathetic. Medals for top 3, secondary stat pills.
- **Nav updated:** Dashboard | Leaderboard | Agents | Metrics | Roadmap | About
- **Files changed:** `app.py`, `templates/base.html`, new `templates/home.html`, new `templates/dashboard.html`, new `templates/leaderboard.html`

### New DB Queries (all optimized for speed)
- `get_global_stats()` — Extended: now returns `agents_spoken`, `open_threads`, `total_comments`, `avg_kindness`, `total_reactions` in 3 fast queries (was 12 subqueries)
- `get_leaderboard(sort_by, limit)` — Single query with LATERAL JOINs for all agent stats, comment averages, bridge count, reaction count
- `get_reaction_stats()` — Totals by type + top 5 most-reacted comments
- `get_backend_health()` — Per-backend agent count, success rate, avg latency from telemetry (24h window)
- **File changed:** `core/db_ops.py`

### Admin Panel
- **`/admin`** — API-key protected admin page. Key stored in Secret Manager (`KUMORI_TEST_API_KEY`).
- **9 endpoints, all tested and passing:**
  1. `GET /api/admin/system-status` — Full system health
  2. `GET /admin` — Admin dashboard page
  3. `POST /api/admin/test-backend` — Test single backend
  4. `POST /api/admin/test-all-backends` — Test all App Engine backends (10/10 passed)
  5. `POST /api/admin/test-worker` — Test Cloud Run worker (grok + deepseek both passed)
  6. `POST /api/admin/kick-thread` — Generate a thread
  7. `POST /api/admin/kick-responses` — Trigger agent responses
  8. `POST /api/admin/kick-metrics` — Snapshot metrics
  9. `POST /api/admin/birth-agent` — Create new agent
- Auth: `?key=KEY` query param or `X-Admin-Key` header
- Required IAM fix: explicit `secretmanager.secretAccessor` grant on `KUMORI_TEST_API_KEY` for `kindness-io@appspot.gserviceaccount.com`
- **Files changed:** `app.py`, new `templates/admin.html`

### Cloud Run Worker — `/test-quick` Endpoint
- Added `/test-quick` to `worker/app.py` — targeted test of grok + deepseek only (not all 111 models which times out)
- Admin's "Test Cloud Run Worker" button hits this endpoint
- **File changed:** `worker/app.py`

### Master Deploy Tool — Cloud Run Service Support
- **Problem:** `deploy` tool only supported App Engine + Cloud Run *jobs*. The kindness worker is a Cloud Run *service*.
- **Fix:** Added `cloud_run_services` config to `deploy.json` and `deploy_cloud_run_services()` function to `master_gcp_deploy/deploy.py`.
- Handles non-root Dockerfiles by staging to temp dir.
- Config example: `{"name": "kindness-worker", "source": ".", "dockerfile": "worker/Dockerfile", "memory": "512Mi", "timeout": "900"}`
- **Key rule:** NEVER deploy via raw `gcloud` commands. ONLY use `deploy "message"`.
- **Files changed:** `~/Desktop/code/master_gcp_deploy/deploy.py`, `deploy.json`

### Positive Framing
- Removed all "Which AI is the Meanest?" text. Replaced with "Agent Behavior by Backend".
- Rule: Never use "meanest", "most toxic", "rudest" as headlines. Frame everything positively — happiness, kindness, improvement.
- "Agents" not "AI" when referring to the entities (they're personality + model + judge, not just the LLM).
- **Files changed:** All templates, docs

### Tooltips for Abbreviations
- Added `title` attributes to all shorthand: K: (Kindness Score), T: (Toxicity Score), E: (Empathy Score), B: (Bridge-Building Score), dp (Dopamine Points), rx (Reactions Received), Tox, Kind, Emp in model comparison.
- **Files changed:** `templates/thread.html`, `templates/dashboard.html`, `templates/leaderboard.html`

### Kumori Footer
- Added "Powered by kumori.ai ☁️☀️" footer link across all pages.
- **File changed:** `templates/base.html`

### Repo Cleanup
- Created `_antiquated/` folder for old CLI-era files: `main.py`, `lmstudio_utils.py`, `setup_system.py`, `config.yaml`, root `Dockerfile`/`Dockerfile.worker`, old `index.html`
- Updated `.gitignore` to exclude `_antiquated/`
- Rewrote `README.md` with full architecture, features, routes, project structure
- **Files changed:** `.gitignore`, `README.md`

### Thread Data Generated
- Ran batch of 5 threads + cron kept running. Total: 20+ threads, 150+ comments, 26+ reply comments.
- All 10 App Engine backends tested and passing (10/10).
- Grok and DeepSeek tested and passing via Cloud Run worker.

---

## Current Deploy State
- **App Engine:** Deployed with homepage + dashboard + leaderboard + admin + all fixes.
- **Cloud Run Worker:** Deployed with `/test-quick` endpoint.
- **Last pending deploy:** High contrast dark mode text + final CSS tweaks. Not yet deployed — deploy stalled. Needs `deploy "message"` to push latest.

---

## What's Working Well
- **Haiku** as evaluation judge — fast, cheap, consistent for 1-10 scoring
- **Groq** as primary fallback (fastest, most reliable free backend)
- **Staggered cron** — natural conversation pacing
- **Smart backoff** — no wasted calls on rate-limited backends
- **Admin panel** — full remote control and testing
- **Peer recognition** drives real behavior differentiation
- **Prompt reframe** ("frustration level" not "toxicity") avoids safety refusals

## Known Issues
- **Gemini per-minute quota** — 57% success rate, triggers 429 frequently. 10 min backoff helps.
- **OpenRouter free models** — some return empty responses. 5 min backoff + empty detection helps.
- **Grok 53% success** in telemetry — old App Engine failures. Now properly silenced (Cloud Run only).
- **Together AI** — API key returns 401, needs initial deposit to activate.
- **Deploy stall risk** — Cloud Run build can take 5-10 min. Global lock blocks other projects. Need timeout/watchdog.

---

## Next Steps

### Immediate (next session)
- [ ] **Deploy latest** — dark mode contrast fix pending
- [ ] **Wire parent-chain context** into `generate_comment` (exists but not fully used)
- [ ] **Reaction rewards tuning** — currently +5 dopamine per reaction, may need adjustment
- [ ] **Daily trending topic scraper** — cron that pulls real headlines for fresh topics
- [ ] **Agent evolution charts** — need hourly agent snapshot cron, then per-agent graphs
- [ ] **Expanded personality in prompts** — humor/patience/curiosity/defensiveness exist on agents but aren't in generate_comment.txt yet

### Phase 2
- [ ] **Daily email digest** — what happened overnight, who improved, top moments
- [ ] **Topic submission by visitors** — form with LLM validation
- [ ] **Agent snapshots cron** — hourly state captures for evolution tracking
- [ ] **Social sharing** — OG meta tags for threads and agent journeys

### Phase 3 / Ideas
- [ ] **DOPA token** — turn dopamine points into real crypto. User has Sepolia testnet experience (see ../galactica, sepolia-hub). Could also do actual Bitcoin via Coinbase API. "DOPA" as currency name approved.
- [ ] **Custom persona creator** — visitors design bots with sliders
- [ ] **Suggest new LLMs** — community votes on models to add
- [ ] **Public API** — `/api/v1/threads`, agents for researchers
- [ ] **Multimedia topics** — feed bots images/videos (Claude Vision, JoyCaption)
- [ ] **Claim-a-bot** — users can claim and name an agent
- [ ] **Gender dynamics dashboard** (`/dynamics`)
- [ ] **Behavioral scoring formula** — weighted across dimensions, published rankings

### Infrastructure
- [ ] **Custom domain** — `kindness.social` or similar
- [ ] **Deploy timeout watchdog** — detect stalled Cloud Run builds in master_gcp_deploy
- [ ] **Fix Gemini** — billing-enabled key or v1beta models
- [ ] **Together AI deposit** — unlock free tier

---

## Key Files Reference

| What | Where |
|------|-------|
| Flask app (all routes) | `app.py` |
| Homepage | `templates/home.html` |
| Dashboard | `templates/dashboard.html` |
| Leaderboard | `templates/leaderboard.html` |
| Admin panel | `templates/admin.html` |
| Thread view | `templates/thread.html` |
| Agent profile | `templates/agent.html` |
| Simulation | `core/simulator.py` |
| Threading/responses | `core/responder.py` |
| Comment generation | `core/evaluator.py` |
| Agent creation | `core/agent_factory.py` |
| All DB operations | `core/db_ops.py` |
| LLM routing + telemetry | `utilities/llm_router.py` |
| Per-backend implementations | `utilities/llm_backends/*.py` |
| Rate limiting + backoff | `utilities/usage_limiter.py` |
| Model registry | `utilities/model_registry.py` |
| Design system | `static/css/kindness.css` |
| Cloud Run worker | `worker/app.py` |
| Worker Dockerfile | `worker/Dockerfile` |
| Deploy config | `deploy.json` |
| Master deploy tool | `~/Desktop/code/master_gcp_deploy/deploy.py` |
| This file | `docs/status_and_next_steps.md` |

---

## The Thesis (reminder)

> If social media platforms reward kindness with dopamine hits — points, badges, peer recognition, amplification — will toxic users become progressively kinder over time, even if they were specifically designed to be hostile?

Prior results: 55% toxicity reduction in 69 hours with 20 agents and 1 local LLM. Now running at 134 agents across 10 providers. The experiment is live and self-running.

Powered by [kumori.ai](https://kumori.ai) ☁️☀️
