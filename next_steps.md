# next_steps — kindness_social

<!-- Auto-maintained.
     • Append a pending item:  `deploy "msg" --next "thing to do later"`
     • Standalone queue (no commit):  `deploy --next "thing to do later"`
     • The nightly cron rewrites the Shipped and Unfinished sections. -->

*Last refreshed: 2026-09-16 05:45*

## 🎯 Pending

<!-- pending:start -->
- [ ] 2026-05-13 07:36 — embed_bios.py emit kindness_embed_v1 — single-line addition, closes embed-text canary signal.
- [ ] 2026-05-13 07:36 — validate _RERANK_CONFIDENCE_FLOOR=0.30 against a week of kindness_rerank_v1 data. If distributions are skewed (all >0.9 or all <0.1), retune.
- [ ] 2026-05-13 07:36 — observation pass on kindness_live_v1 (after 2026-05-20). Look at per-backend score distributions, set new QUALITY_FLOOR empirically in core/quality_filter.py instead of guessing 30.
<!-- pending:end -->

## ✅ Recently shipped

<!-- shipped:start -->
- `d32998a` · 2026-09-15 18:36 — Snapshot prune: delete by primary key in batches, and cap a run. As one DELETE matching an id sub...
- `85f770f` · 2026-09-15 17:53 — Snapshot retention: past 30 days, keep one agent snapshot per day. The snapshot cron writes a row...
- `e13325f` · 2026-09-15 10:10 — visitor_logging credential fetch fails closed: no fallback to the postgres superuser.
<!-- shipped:end -->

## ⚠️ Unfinished / WIP

<!-- wip:start -->
_(clean working tree, no TODO markers in recent files)_
<!-- wip:end -->
