# next_steps — kindness_social

<!-- Auto-maintained.
     • Append a pending item:  `deploy "msg" --next "thing to do later"`
     • Standalone queue (no commit):  `deploy --next "thing to do later"`
     • The nightly cron rewrites the Shipped and Unfinished sections. -->

*Last refreshed: 2026-08-28 04:11*

## 🎯 Pending

<!-- pending:start -->
- [ ] 2026-05-13 07:36 — embed_bios.py emit kindness_embed_v1 — single-line addition, closes embed-text canary signal.
- [ ] 2026-05-13 07:36 — validate _RERANK_CONFIDENCE_FLOOR=0.30 against a week of kindness_rerank_v1 data. If distributions are skewed (all >0.9 or all <0.1), retune.
- [ ] 2026-05-13 07:36 — observation pass on kindness_live_v1 (after 2026-05-20). Look at per-backend score distributions, set new QUALITY_FLOOR empirically in core/quality_filter.py instead of guessing 30.
<!-- pending:end -->

## ✅ Recently shipped

<!-- shipped:start -->
- `8f07aa9` · 2026-08-21 16:30 — pick up the visitor_logging idle release: give the shared DB connection back when this site has n...
- `e61312b` · 2026-08-21 12:39 — evaluator: one LLM call for kindness + toxicity + empathy instead of three. This evaluator was th...
<!-- shipped:end -->

## ⚠️ Unfinished / WIP

<!-- wip:start -->
**2 file(s) with uncommitted changes:**
- ` M core/db_ops.py`
- ` M next_steps.md`

<!-- wip:end -->
