# next_steps — kindness_social

<!-- Auto-maintained.
     • Append a pending item:  `deploy "msg" --next "thing to do later"`
     • Standalone queue (no commit):  `deploy --next "thing to do later"`
     • The nightly cron rewrites the Shipped and Unfinished sections. -->

*Last refreshed: 2026-06-25 04:14*

## 🎯 Pending

<!-- pending:start -->
- [ ] 2026-05-13 07:36 — embed_bios.py emit kindness_embed_v1 — single-line addition, closes embed-text canary signal.
- [ ] 2026-05-13 07:36 — validate _RERANK_CONFIDENCE_FLOOR=0.30 against a week of kindness_rerank_v1 data. If distributions are skewed (all >0.9 or all <0.1), retune.
- [ ] 2026-05-13 07:36 — observation pass on kindness_live_v1 (after 2026-05-20). Look at per-backend score distributions, set new QUALITY_FLOOR empirically in core/quality_filter.py instead of guessing 30.
<!-- pending:end -->

## ✅ Recently shipped

<!-- shipped:start -->
- `18fbfe5` · 2026-06-21 08:28 — worker: fix deepseek SSE parser — only kept lines with literal '"o":"APPEND"' (first token only),...
- `827ba36` · 2026-06-21 07:47 — fix 6-hourly /thread 500s: backfill-avatars was saturating the single F1. Replace N serial GCS HE...
<!-- shipped:end -->

## ⚠️ Unfinished / WIP

<!-- wip:start -->
**1 file(s) with uncommitted changes:**
- ` M next_steps.md`

<!-- wip:end -->
