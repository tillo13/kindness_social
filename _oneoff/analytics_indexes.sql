-- Proposed DDL for the 2026-07-06 QueryCanceled bucket (db_ops_analytics.py).
-- DO NOT run from an app; run manually via psql/cloud console at a quiet hour.
--
-- Finding: NO new index is needed. Both timing-out queries already had the
-- right indexes (idx_kindness_comments_thread for get_featured_thread's
-- LATERAL probes, idx_kindness_reactions_comment for reaction counts) — the
-- root cause was query shape: full scans of the 1.9M-row / 109MB
-- kindness_reactions heap take >10s on the shared f1-micro, so any web-path
-- full pass flirts with the 30s statement_timeout. Fixed in code
-- (core/db_ops_analytics.py: incremental reaction stats; 30d bound on
-- get_featured_thread).
--
-- One piece of cleanup DDL is worth doing: kindness_reactions carries two
-- IDENTICAL btree(comment_id) indexes (confirmed live 2026-07-06):
--   idx_kindness_reactions_comment  (planner picks this one)
--   idx_reactions_comment           (redundant duplicate)
-- Dropping the duplicate saves write amplification + buffer cache on a 257MB
-- table that takes ~20 inserts per comment.

DROP INDEX CONCURRENTLY IF EXISTS idx_reactions_comment;
