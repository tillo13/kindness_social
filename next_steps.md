# next_steps — kindness_social

<!-- Auto-maintained.
     • Append a pending item:  `deploy "msg" --next "thing to do later"`
     • Standalone queue (no commit):  `deploy --next "thing to do later"`
     • The nightly cron rewrites the Shipped and Unfinished sections. -->

*Last refreshed: 2026-09-04 04:00*

## 🎯 Pending

<!-- pending:start -->
- [ ] 2026-05-13 07:36 — embed_bios.py emit kindness_embed_v1 — single-line addition, closes embed-text canary signal.
- [ ] 2026-05-13 07:36 — validate _RERANK_CONFIDENCE_FLOOR=0.30 against a week of kindness_rerank_v1 data. If distributions are skewed (all >0.9 or all <0.1), retune.
- [ ] 2026-05-13 07:36 — observation pass on kindness_live_v1 (after 2026-05-20). Look at per-backend score distributions, set new QUALITY_FLOOR empirically in core/quality_filter.py instead of guessing 30.
<!-- pending:end -->

## ✅ Recently shipped

<!-- shipped:start -->
- `068013d` · 2026-09-02 16:50 — upload hygiene: exclude gitignored scratch from the App Engine bundle. _oneoff/ (which the fleet ...
- `cc19418` · 2026-09-02 08:05 — cron: the five chat-bearing sims every 12 hours, was 4-6 h (10 firings/day). On a drip: the share...
- `31e12ae` · 2026-09-02 07:08 — cron: the five chat-bearing sims every 4-6 h, was every 15-60 min (224 firings/day to 22). Turned...
- `3809c72` · 2026-08-28 14:32 — the paid DeepSeek and xAI fallbacks are marked never-use, not merely unfunded. models/deepseek.js...
<!-- shipped:end -->

## ⚠️ Unfinished / WIP

<!-- wip:start -->
**1 file(s) with uncommitted changes:**
- ` M next_steps.md`

<!-- wip:end -->
