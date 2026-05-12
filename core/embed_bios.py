"""Agent bio embedding — populates kindness_agents.bio_vec via the kumori
free-LLM embed pool, plus similarity queries for the admin UI.

Bios are SYNTHESIZED from existing agent traits (preset + phrases + lean +
demographics) — kindness_agents has no free-form bio column. Synthesis is
deterministic so re-embedding the same agent yields the same vector.

Schema bootstrap (idempotent) lives in `ensure_schema()`. Call it before
any embed/query operation.
"""
import json
import logging

from utilities.postgres_utils import db_cursor
from utilities.kumori_api_client import embed_text as _kumori_embed_text

logger = logging.getLogger(__name__)

_SCHEMA_READY = False


def ensure_schema():
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    with db_cursor(dict_cursor=False, commit=True) as cur:
        cur.execute("ALTER TABLE kindness_agents ADD COLUMN IF NOT EXISTS bio_vec FLOAT8[]")
        cur.execute("ALTER TABLE kindness_agents ADD COLUMN IF NOT EXISTS bio_vec_at TIMESTAMP")
    _SCHEMA_READY = True


def render_bio(agent):
    """Deterministic bio text from agent row. Order matters for embedding
    stability — never reorder without a backfill rerun."""
    lean = agent.get('political_lean') or 0
    lean_word = ('very left' if lean < -0.5 else 'leans left' if lean < -0.1
                 else 'centrist' if abs(lean) < 0.1
                 else 'leans right' if lean < 0.5 else 'very right')
    tox = float(agent.get('toxicity_baseline') or 0)
    emp = float(agent.get('empathy_baseline') or 0)
    opn = float(agent.get('openness_to_change') or 0)
    tox_word = 'aggressive' if tox > 0.6 else 'spiky' if tox > 0.3 else 'gentle'
    emp_word = 'deeply empathetic' if emp > 0.6 else 'moderately empathetic' if emp > 0.3 else 'detached'
    opn_word = 'open-minded' if opn > 0.6 else 'moderate' if opn > 0.3 else 'set in their ways'
    gender = agent.get('gender_presentation') or 'nonbinary'
    age = (agent.get('age_bracket') or 'middle_aged').replace('_', ' ')
    auth = agent.get('authority_level') or 'medium'
    phrases_raw = agent.get('common_phrases')
    if isinstance(phrases_raw, str):
        try:
            phrases = json.loads(phrases_raw)
        except Exception:
            phrases = []
    else:
        phrases = phrases_raw or []
    phrase_str = ', '.join(f'"{p}"' for p in phrases[:4]) if phrases else 'no signature phrases'
    return (
        f"{age} {gender}, {auth} authority. "
        f"Politically {lean_word}. "
        f"{tox_word.capitalize()} tone, {emp_word}, {opn_word}. "
        f"Signature phrases: {phrase_str}."
    )


def embed_agent(agent_row):
    """Embed one agent and persist. Returns vector or None on failure.
    Failures are swallowed — bio embedding must NEVER block agent creation."""
    ensure_schema()
    agent_id = agent_row.get('agent_id') or agent_row.get('id')
    if not agent_id:
        return None
    bio = render_bio(agent_row)
    try:
        vectors, backend = _kumori_embed_text([bio], input_type='search_document')
    except Exception as e:
        logger.warning(f"embed_agent: kumori embed_text failed for {agent_id}: {e}")
        return None
    if not vectors or not vectors[0]:
        logger.warning(f"embed_agent: empty vector for {agent_id}")
        return None
    vec = vectors[0]
    try:
        with db_cursor(dict_cursor=False, commit=True) as cur:
            cur.execute(
                "UPDATE kindness_agents SET bio_vec = %s, bio_vec_at = NOW() WHERE agent_id = %s",
                (vec, agent_id),
            )
    except Exception as e:
        logger.warning(f"embed_agent: persist failed for {agent_id}: {e}")
        return None
    logger.info(f"embed_agent {agent_id} via {backend} (dim={len(vec)})")
    return vec


def backfill_missing(limit=None):
    """Embed every agent with bio_vec IS NULL. Returns (n_done, n_failed)."""
    ensure_schema()
    with db_cursor(dict_cursor=True) as cur:
        sql = ("SELECT * FROM kindness_agents "
               "WHERE bio_vec IS NULL AND is_active = TRUE ORDER BY id")
        if limit:
            sql += f" LIMIT {int(limit)}"
        cur.execute(sql)
        rows = [dict(r) for r in cur.fetchall()]
    done = failed = 0
    for r in rows:
        if embed_agent(r) is not None:
            done += 1
        else:
            failed += 1
    return done, failed


def _cosine(a, b):
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sa = sb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        sa += x * x
        sb += y * y
    if sa == 0 or sb == 0:
        return 0.0
    return dot / ((sa ** 0.5) * (sb ** 0.5))


def _all_vecs():
    """Pulls every agent with a bio_vec. Returns list of dicts with vec + bio."""
    with db_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT agent_id, display_name, bio_vec,
                   political_lean, toxicity_baseline, empathy_baseline,
                   openness_to_change, gender_presentation, age_bracket,
                   authority_level, common_phrases
              FROM kindness_agents
             WHERE bio_vec IS NOT NULL AND is_active = TRUE
        """)
        out = []
        for r in cur.fetchall():
            row = dict(r)
            row['vec'] = list(row.pop('bio_vec') or [])
            row['bio'] = render_bio(row)
            out.append(row)
        return out


def similar_to(agent_id, k=5):
    ensure_schema()
    rows = _all_vecs()
    target = next((r for r in rows if r['agent_id'] == agent_id), None)
    if not target or not target['vec']:
        return []
    scored = [
        {'agent_id': r['agent_id'], 'display_name': r['display_name'],
         'score': _cosine(target['vec'], r['vec']), 'bio': r['bio']}
        for r in rows if r['agent_id'] != agent_id
    ]
    scored.sort(key=lambda x: x['score'], reverse=True)
    return scored[:k]


def search(query, k=10):
    """Embed `query` (as search_query) and rank agents."""
    ensure_schema()
    try:
        qvecs, backend = _kumori_embed_text([query], input_type='search_query')
    except Exception as e:
        logger.warning(f"search: embed query failed: {e}")
        return [], None
    if not qvecs or not qvecs[0]:
        return [], None
    qvec = qvecs[0]
    rows = _all_vecs()
    scored = [
        {'agent_id': r['agent_id'], 'display_name': r['display_name'],
         'score': _cosine(qvec, r['vec']), 'bio': r['bio']}
        for r in rows
    ]
    scored.sort(key=lambda x: x['score'], reverse=True)
    return scored[:k], backend
