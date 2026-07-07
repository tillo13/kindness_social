# next_steps — kindness_social

<!-- Auto-maintained.
     • Append a pending item:  `deploy "msg" --next "thing to do later"`
     • Standalone queue (no commit):  `deploy --next "thing to do later"`
     • The nightly cron rewrites the Shipped and Unfinished sections. -->

*Last refreshed: 2026-07-07 04:26*

## 🎯 Pending

<!-- pending:start -->
- [ ] 2026-05-13 07:36 — embed_bios.py emit kindness_embed_v1 — single-line addition, closes embed-text canary signal.
- [ ] 2026-05-13 07:36 — validate _RERANK_CONFIDENCE_FLOOR=0.30 against a week of kindness_rerank_v1 data. If distributions are skewed (all >0.9 or all <0.1), retune.
- [ ] 2026-05-13 07:36 — observation pass on kindness_live_v1 (after 2026-05-20). Look at per-backend score distributions, set new QUALITY_FLOOR empirically in core/quality_filter.py instead of guessing 30.
<!-- pending:end -->

## ✅ Recently shipped

<!-- shipped:start -->
- `e4b6174` · 2026-07-06 09:20 — leaderboard: per-agent reaction counts folded into the incremental precompute state — no full kin...
- `361fd4c` · 2026-07-06 08:25 — analytics: incremental precompute for reaction stats (was full 1.89M-row aggregate per dashboard ...
- `30a2c4e` · 2026-07-02 10:07 — db-speed tier-1 runtime instrumentation (per db-speed-first): db_cursor now (1) increments a thre...
- `ac3b785` · 2026-07-02 09:11 — rerank flood fix (root cause of kumori gateway rerank 502s — NOT capacity): fcef402 added per-rep...
- `c4e0def` · 2026-07-02 08:44 — birth-agent cron 500s (Error 123 deadline-exceeded): create_agent() made 3 sequential gateway cal...
- `d49b645` · 2026-07-02 08:31 — digest fixes: (1) agent-reflect cron 500s (Error 123 deadline-exceeded) — run_reflection_cycle re...
- `8c64f7c` · 2026-07-01 09:11 — propagate kumori_api_client retry fix: jittered backoff before the single retry (de-syncs cross-a...
<!-- shipped:end -->

## ⚠️ Unfinished / WIP

<!-- wip:start -->
_(clean working tree, no TODO markers in recent files)_
<!-- wip:end -->
