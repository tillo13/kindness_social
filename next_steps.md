# next_steps — kindness_social

<!-- Auto-maintained.
     • Append a pending item:  `deploy "msg" --next "thing to do later"`
     • Standalone queue (no commit):  `deploy --next "thing to do later"`
     • The nightly cron rewrites the Shipped and Unfinished sections. -->

*Last refreshed: 2026-05-31 04:00*

## 🎯 Pending

<!-- pending:start -->
- [ ] 2026-05-13 07:36 — embed_bios.py emit kindness_embed_v1 — single-line addition, closes embed-text canary signal.
- [ ] 2026-05-13 07:36 — validate _RERANK_CONFIDENCE_FLOOR=0.30 against a week of kindness_rerank_v1 data. If distributions are skewed (all >0.9 or all <0.1), retune.
- [ ] 2026-05-13 07:36 — observation pass on kindness_live_v1 (after 2026-05-20). Look at per-backend score distributions, set new QUALITY_FLOOR empirically in core/quality_filter.py instead of guessing 30.
<!-- pending:end -->

## ✅ Recently shipped

<!-- shipped:start -->
- `e77ed4e` · 2026-05-30 17:57 — agent-LLM resilience: stop backend flakiness from 500-ing crons
<!-- shipped:end -->

## ⚠️ Unfinished / WIP

<!-- wip:start -->
_(clean working tree, no TODO markers in recent files)_
<!-- wip:end -->
