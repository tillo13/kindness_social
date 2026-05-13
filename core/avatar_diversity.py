"""Avatar visual-diversity canary — closes the embed-image real-world gap.

Sample N agent avatars from the kindness fleet, embed each via kumori's
free embed-image pool, then compute average nearest-neighbor cosine
distance per describe-backend. High distance → backend "sees" the fleet
as visually diverse; low distance → backend keeps producing near-clones.

Emits one kindness_imgembed_v1 quality-sample per (describe-backend, run)
to the kumori catalog. Cadence: weekly (cheap — one batch embed call
per run).

Per-imggen-backend attribution would require joining with kumori_api_usage
(only the describe-backend is on the agent row). Future enhancement; for
now grouping by describe_backend is honest enough since the describe step
is part of the same chain we're validating.
"""
import logging
import math
import random
import time

logger = logging.getLogger(__name__)

# Subsample size — enough to be statistically meaningful, small enough that
# one weekly run is bytes-cheap and Cohere-cap-cheap.
_SAMPLE_SIZE = 30

# Minimum group size before we emit a per-backend sample. Below this the
# nearest-neighbor distance isn't statistically meaningful.
_MIN_GROUP_SIZE = 4


def _cosine_distance(a, b):
    """1 - cosine_similarity. 0 = identical, 1 = orthogonal, 2 = opposite."""
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 1.0
    return 1.0 - (dot / (na * nb))


def _avg_nearest_neighbor_distance(vectors):
    """For each vector, find the closest other vector; return the average
    of those minimum distances. High value = visually diverse group; low
    value = backend keeps generating near-clones."""
    n = len(vectors)
    if n < 2:
        return None
    nn_distances = []
    for i in range(n):
        best = None
        for j in range(n):
            if i == j:
                continue
            d = _cosine_distance(vectors[i], vectors[j])
            if best is None or d < best:
                best = d
        nn_distances.append(best)
    return sum(nn_distances) / len(nn_distances)


def _fetch_avatar_b64(agent_id):
    """Pull one avatar from GCS, return base64-encoded bytes or None."""
    try:
        import base64
        from utilities.avatar_generator import _get_gcs_bucket
        bucket = _get_gcs_bucket()
        blob = bucket.blob(f'{agent_id}.jpg')
        if not blob.exists():
            return None
        data = blob.download_as_bytes()
        return base64.b64encode(data).decode('ascii')
    except Exception as e:
        logger.debug(f"avatar fetch failed for {agent_id}: {e}")
        return None


def run_diversity_canary():
    """Main entry point — called by the cron route. Returns a summary dict
    suitable for HTTP response. Fire-and-forget for the catalog emit; never
    raises into the cron route."""
    from utilities.postgres_utils import db_cursor

    # Defensive imports — kumori client may not be vendored yet on first run
    try:
        from utilities.kumori_api_client import embed_image as _embed_image
    except ImportError:
        return {'ok': False, 'error': 'kumori_api_client.embed_image not available'}
    try:
        from utilities.kumori_api_client import emit_quality_sample as _emit_quality_sample
    except ImportError:
        _emit_quality_sample = None

    # Pull sample agents — only those whose avatar has actually been described
    # (so we know a describe_backend is available for grouping).
    with db_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT agent_id, avatar_describe_backend
              FROM kindness_agents
             WHERE avatar_describe_backend IS NOT NULL
             ORDER BY random()
             LIMIT %s
        """, (_SAMPLE_SIZE,))
        rows = cur.fetchall()

    if len(rows) < _MIN_GROUP_SIZE:
        return {'ok': False, 'error': f'only {len(rows)} described avatars — need ≥{_MIN_GROUP_SIZE}'}

    # Fetch + encode in sequence (130 max, weekly, fine to be slow)
    sampled = []  # list of (agent_id, describe_backend, b64)
    for r in rows:
        b64 = _fetch_avatar_b64(r['agent_id'])
        if b64:
            sampled.append((r['agent_id'], r['avatar_describe_backend'], b64))
    if len(sampled) < _MIN_GROUP_SIZE:
        return {'ok': False, 'error': f'fetched only {len(sampled)} avatars from GCS'}

    # One batch embed call — embed_image accepts a list, returns vectors[].
    t0 = time.time()
    try:
        vectors, embed_backend = _embed_image([s[2] for s in sampled])
    except Exception as e:
        return {'ok': False, 'error': f'embed_image failed: {type(e).__name__}: {e}'}
    duration_ms = int((time.time() - t0) * 1000)
    if not vectors or len(vectors) != len(sampled):
        return {'ok': False, 'error': f'embed_image returned {len(vectors or [])} vectors for {len(sampled)} images'}

    # Group by describe_backend and compute per-group diversity
    by_backend = {}  # describe_backend -> list[vector]
    for (_aid, describer, _b64), vec in zip(sampled, vectors):
        by_backend.setdefault(describer or 'unknown', []).append(vec)

    summary = []
    for describer, group_vecs in by_backend.items():
        if len(group_vecs) < _MIN_GROUP_SIZE:
            summary.append({
                'describe_backend': describer,
                'n': len(group_vecs),
                'diversity_score': None,
                'note': f'group too small (<{_MIN_GROUP_SIZE}), skipped emit',
            })
            continue
        avg_nn = _avg_nearest_neighbor_distance(group_vecs)
        # Normalize to 0-100. Cosine distance in [0, 2]; in practice for
        # natural-image embeddings nearest-neighbor distances tend to land
        # in [0.1, 0.6]. Multiply by 100, clamp 0-100.
        score = int(max(0, min(100, round(avg_nn * 100))))
        summary.append({
            'describe_backend': describer,
            'n': len(group_vecs),
            'diversity_score': score,
            'avg_nn_distance': round(avg_nn, 4),
        })
        # Emit to kumori catalog — fire-and-forget. backend field = embed
        # backend (we're scoring embed-image as a capability); describe
        # backend lives in judge_notes for analysis.
        if _emit_quality_sample is not None:
            try:
                import json
                _emit_quality_sample(
                    backend=embed_backend or 'embed_unknown',
                    score=score,
                    ok=True,
                    judge_kind='kindness_imgembed_v1',
                    modality='embed_image',
                    duration_ms=duration_ms,
                    judge_notes=json.dumps({
                        'describe_backend': describer,
                        'group_size': len(group_vecs),
                        'avg_nn_distance': round(avg_nn, 4),
                        'sample_size': len(sampled),
                    })[:500],
                )
            except Exception as e:
                logger.debug(f"imgembed sample emit failed: {e}")

    return {
        'ok': True,
        'sampled': len(sampled),
        'embed_backend': embed_backend,
        'duration_ms': duration_ms,
        'by_describe_backend': summary,
    }
