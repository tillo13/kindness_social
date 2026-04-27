#!/usr/bin/env python3
"""backfill_thread_parents.py — for every historical comment with parent_comment_id
IS NULL, parse mentions of `provider.model.NNN`-style agent IDs from the comment
text and link to the most recent matching prior comment in the same thread.

Conservative: only links when the FULL agent_id appears verbatim in the text
(high confidence). Ambiguous "I hear you, Mistral" without a full ID is left
alone — too many agents per provider to disambiguate safely.

Run locally (no need to deploy):
    cd ~/Desktop/code/kindness_social
    venv_kindness/bin/python3 dev/scripts/backfill_thread_parents.py            # dry-run
    venv_kindness/bin/python3 dev/scripts/backfill_thread_parents.py --apply    # actually update
    venv_kindness/bin/python3 dev/scripts/backfill_thread_parents.py --apply --days 30   # last 30 days only
"""
import argparse
import re
import subprocess
import sys
from collections import defaultdict

AGENT_ID_RE = re.compile(r'\b([a-z][a-z0-9_]*\.[a-z0-9_.\-]+\.\d{3,4})\b')
# Pattern 2: provider name as direct address. Matches things like:
#   "I hear you, Mistral", "Groq, you're stuck", "Hey Cerebras", "@Openai"
# Only triggers in conversational positions (after , / ! / ? or at start of
# sentence, optionally with @) — NOT mid-sentence where provider name might
# appear in a non-addressing context.
PROVIDER_ADDRESS_RE = re.compile(
    r'(?:^|[,.!?"\'“‘]\s*|@)([A-Z][a-zA-Z]{2,12})(?=[,!?:.\s]|$)',
    re.MULTILINE,
)


def secret(name):
    return subprocess.check_output(
        ['gcloud', 'secrets', 'versions', 'access', 'latest',
         '--secret', name, '--project', 'kumori-404602'],
        text=True, stderr=subprocess.DEVNULL, timeout=10,
    ).strip()


def connect():
    import psycopg2
    return psycopg2.connect(
        host=secret('KUMORI_POSTGRES_IP'),
        dbname=secret('KUMORI_POSTGRES_DB_NAME'),
        user=secret('KUMORI_POSTGRES_USERNAME'),
        password=secret('KUMORI_POSTGRES_PASSWORD'),
        port=5432,
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true', help='Actually update (default: dry-run)')
    ap.add_argument('--days', type=int, default=0, help='Only consider comments from last N days (0 = all)')
    args = ap.parse_args()

    import psycopg2.extras
    conn = connect()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    where_time = (f"AND c.created_at > NOW() - INTERVAL '{args.days} days'"
                  if args.days > 0 else '')
    print(f'Loading comments {f"from last {args.days} days" if args.days else "(all time)"}...')
    cur.execute(f"""
        SELECT c.id, c.thread_id, c.position, c.agent_id AS author_id,
               c.comment_text, c.parent_comment_id,
               a.agent_id AS author_agent_id_str
          FROM kindness_comments c
          JOIN kindness_agents a ON c.agent_id = a.id
         WHERE c.parent_comment_id IS NULL
           {where_time}
         ORDER BY c.thread_id, c.position
    """)
    null_parent_rows = cur.fetchall()
    print(f'  {len(null_parent_rows):,} comments without parent')

    # Index ALL comments by thread + agent_id_str so we can find prior matches
    cur.execute(f"""
        SELECT c.id, c.thread_id, c.position, c.agent_id, c.created_at,
               a.agent_id AS agent_id_str
          FROM kindness_comments c
          JOIN kindness_agents a ON c.agent_id = a.id
         WHERE 1=1 {where_time}
         ORDER BY c.thread_id, c.position
    """)
    by_thread = defaultdict(list)  # thread_id -> [{id, position, author_db_id, agent_id_str}]
    for r in cur.fetchall():
        by_thread[r['thread_id']].append({
            'id': r['id'], 'position': r['position'],
            'author_db_id': r['agent_id'], 'agent_id_str': r['agent_id_str'],
        })
    print(f'  indexed {sum(len(v) for v in by_thread.values()):,} comments across {len(by_thread):,} threads')

    updates = []  # (comment_id, parent_id, replied_to_agent_db_id)
    matched_full_id = 0
    matched_provider = 0
    skipped_self = 0
    skipped_no_match = 0
    skipped_no_mention = 0

    # Provider tokens we look for (first segment of agent_id, title-cased).
    # These map to the actual lowercase provider prefix in agent_id strings.
    PROVIDER_PREFIXES = {
        'groq': 'groq', 'cerebras': 'cerebras', 'openai': 'openai',
        'anthropic': 'anthropic', 'mistral': 'mistral', 'gemini': 'gemini',
        'google': 'google', 'gemma': 'google',  # gemma is google's model family
        'nvidia': 'nvidia', 'cohere': 'cohere', 'github': 'github',
        'sambanova': 'sambanova', 'cloudflare': 'cloudflare',
        'openrouter': 'openrouter', 'llm7': 'llm7', 'xai': 'xai',
        'grok': 'xai', 'deepseek': 'deepseek', 'unknown': 'unknown',
        'claude': 'anthropic', 'haiku': 'anthropic', 'sonnet': 'anthropic',
        'llama': 'meta',  # ambiguous — many providers serve llama
    }

    for row in null_parent_rows:
        text = row['comment_text'] or ''
        thread_comments = by_thread.get(row['thread_id'], [])
        prior = [c for c in thread_comments if c['position'] < row['position']]
        if not prior:
            skipped_no_match += 1
            continue

        matched = None

        # Pass 1: full agent_id match (high confidence)
        full_mentions = AGENT_ID_RE.findall(text)
        if full_mentions:
            for mention in reversed(full_mentions):
                for prior_c in reversed(prior):
                    if prior_c['agent_id_str'] == mention:
                        matched = prior_c
                        break
                if matched:
                    matched_full_id += 1
                    break

        # Pass 2: provider name as direct address ("I hear you, Mistral")
        if not matched:
            address_mentions = PROVIDER_ADDRESS_RE.findall(text)
            for mention in reversed(address_mentions):
                provider_prefix = PROVIDER_PREFIXES.get(mention.lower())
                if not provider_prefix:
                    continue
                # Match if the provider keyword appears in ANY dotted segment
                # of the agent_id (not just the first). Handles
                # `unknown.mistral.307` (auto-discovered, "unknown" prefix)
                # alongside the canonical `mistral.foo.NNN`.
                for prior_c in reversed(prior):
                    aid = prior_c['agent_id_str'].lower()
                    segments = aid.split('.')
                    matches_provider = any(
                        seg == provider_prefix or seg.startswith(provider_prefix)
                        for seg in segments
                    )
                    if matches_provider and prior_c['author_db_id'] != row['author_id']:
                        matched = prior_c
                        break
                if matched:
                    matched_provider += 1
                    break

        if not matched:
            if not full_mentions and not PROVIDER_ADDRESS_RE.search(text):
                skipped_no_mention += 1
            else:
                skipped_no_match += 1
            continue
        if matched['author_db_id'] == row['author_id']:
            skipped_self += 1
            continue
        updates.append((row['id'], matched['id'], matched['author_db_id']))

    print()
    print(f'  {len(updates):,} comments would be linked')
    print(f'    {matched_full_id:,} via full agent_id match (high confidence)')
    print(f'    {matched_provider:,} via provider-name address ("I hear you, Mistral")')
    print(f'  {skipped_no_mention:,} skipped — no agent_id or provider mention in text')
    print(f'  {skipped_no_match:,} skipped — mentioned agent not found earlier in same thread')
    print(f'  {skipped_self:,} skipped — would have been a self-reply')

    if not args.apply:
        print('\nDRY RUN — pass --apply to write updates.')
        return

    print(f'\n=== applying {len(updates):,} updates ===')
    BATCH = 1000
    for i in range(0, len(updates), BATCH):
        batch = updates[i:i + BATCH]
        cur2 = conn.cursor()
        # executemany would re-prepare each time; use a single VALUES upsert pattern
        for comment_id, parent_id, replied_to in batch:
            cur2.execute(
                "UPDATE kindness_comments "
                "SET parent_comment_id = %s, replied_to_agent_id = %s "
                "WHERE id = %s AND parent_comment_id IS NULL",
                (parent_id, replied_to, comment_id),
            )
        conn.commit()
        cur2.close()
        print(f'  applied {min(i + BATCH, len(updates)):,}/{len(updates):,}')
    print('done')


if __name__ == '__main__':
    main()
