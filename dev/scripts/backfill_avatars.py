#!/usr/bin/env python3
"""backfill_avatars.py — generate missing avatars for every agent that
doesn't already have one in GCS, using kumori_free_imggen's round-robin
across all 4 truly-keyless image services.

Run locally (uses gcloud secrets + your laptop's network):
    cd ~/Desktop/code/kindness_social
    python3 dev/scripts/backfill_avatars.py             # dry run (just lists missing)
    python3 dev/scripts/backfill_avatars.py --apply     # actually generate + upload
    python3 dev/scripts/backfill_avatars.py --apply --limit 5   # only do first 5

Cost: $0 — Pollinations and Stable Horde are keyless free, Cloudflare uses
the existing KINDNESS_CLOUDFLARE_API_KEY within its 10K-neuron-day free pool.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]   # kindness_social/
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'utilities'))   # for adapters

# Use the canonical infrastructure module directly (this script runs locally,
# not on App Engine, so we don't go through the vendored copy)
sys.path.insert(0, str(Path.home() / 'Desktop/code/_local_infrastructure/kumori_free_image_generations'))
import kumori_free_imggen as router  # noqa: E402

# Borrow the agent-aware prompt builder from avatar_generator
from utilities.avatar_generator import build_prompt, _upload_to_gcs, get_avatar_path  # noqa: E402


def _gcloud_secret(name: str) -> str:
    return subprocess.check_output(
        ['gcloud', 'secrets', 'versions', 'access', 'latest',
         '--secret', name, '--project', 'kumori-404602'],
        text=True, stderr=subprocess.DEVNULL, timeout=10,
    ).strip()


def find_missing_agents() -> list[dict]:
    """Return agent rows whose agent_id is NOT a key in the GCS bucket."""
    import psycopg2, psycopg2.extras
    conn = psycopg2.connect(
        host=_gcloud_secret('KUMORI_POSTGRES_IP'),
        dbname=_gcloud_secret('KUMORI_POSTGRES_DB_NAME'),
        user=_gcloud_secret('KUMORI_POSTGRES_USERNAME'),
        password=_gcloud_secret('KUMORI_POSTGRES_PASSWORD'),
        port=5432,
    )
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM kindness_agents ORDER BY created_at DESC")
    all_agents = [dict(r) for r in cur.fetchall()]
    cur.close()
    conn.close()

    # Get the set of agent_ids that already have an avatar in GCS
    print(f'Listing GCS bucket gs://kindness-io-avatars/ ...', flush=True)
    out = subprocess.check_output(
        ['gcloud', 'storage', 'ls', 'gs://kindness-io-avatars/',
         '--project', 'kindness-io'],
        text=True, stderr=subprocess.DEVNULL,
    )
    in_gcs = {
        line.replace('gs://kindness-io-avatars/', '').removesuffix('.jpg').strip()
        for line in out.splitlines() if line.strip().endswith('.jpg')
    }
    print(f'  {len(all_agents)} agents in DB, {len(in_gcs)} avatars in GCS', flush=True)

    # Also check local committed seed avatars
    seed_dir = ROOT / 'static/images/avatars'
    seed_ids = {f.stem for f in seed_dir.glob('*.jpg')} if seed_dir.exists() else set()
    print(f'  {len(seed_ids)} local seed avatars (won\'t backfill these either)',
          flush=True)

    missing = [a for a in all_agents
               if a['agent_id'] not in in_gcs and a['agent_id'] not in seed_ids]
    return missing


def backfill_one(agent: dict) -> tuple[bool, str, int]:
    """Generate + upload one agent's avatar via the round-robin router.
    Returns (ok, detail_msg, latency_ms)."""
    prompt = build_prompt(agent)
    t0 = time.time()
    img_bytes = router.generate_image(prompt, width=512, height=512, mode='roundrobin')
    ms = int((time.time() - t0) * 1000)
    if not img_bytes:
        return False, 'no provider returned an image', ms
    # Resize to 256x256 JPEG (matches existing pipeline)
    try:
        from PIL import Image
        img = Image.open(BytesIO(img_bytes)).convert('RGB')
        img = img.resize((256, 256), Image.LANCZOS)
        buf = BytesIO()
        img.save(buf, 'JPEG', quality=80, optimize=True)
        img_bytes = buf.getvalue()
    except Exception as e:
        return False, f'resize failed: {e}', ms
    # Save locally + upload to GCS
    path = get_avatar_path(agent['agent_id'])
    try:
        with open(path, 'wb') as f:
            f.write(img_bytes)
    except (OSError, IOError):
        pass
    url = _upload_to_gcs(agent['agent_id'], img_bytes)
    if not url:
        return False, 'GCS upload failed', ms
    return True, f'{len(img_bytes)}B → {url}', ms


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true', help='Actually generate (default: dry-run)')
    ap.add_argument('--limit', type=int, default=0, help='Only process first N (0 = all)')
    args = ap.parse_args()

    missing = find_missing_agents()
    if args.limit > 0:
        missing = missing[:args.limit]

    print(f'\n{len(missing)} agents need avatars:\n')
    for a in missing[:20]:
        print(f'  {a["agent_id"]}  ({a.get("llm_backend", "?")})')
    if len(missing) > 20:
        print(f'  ... and {len(missing) - 20} more')

    if not args.apply:
        print(f'\nDRY RUN. Pass --apply to generate.')
        return

    print(f'\n=== generating {len(missing)} avatars via round-robin ===\n', flush=True)
    t_start = time.time()
    ok_count = 0
    fail_count = 0
    for i, agent in enumerate(missing, 1):
        ok, detail, ms = backfill_one(agent)
        icon = '✅' if ok else '❌'
        print(f'  [{i}/{len(missing)}] {icon} {agent["agent_id"]:<48} {ms:>6}ms  {detail[:80]}',
              flush=True)
        if ok:
            ok_count += 1
        else:
            fail_count += 1

    elapsed = int(time.time() - t_start)
    print(f'\n=== done: {ok_count} ok, {fail_count} failed in {elapsed}s ===')
    print('Per-service usage:')
    for name, count in router._daily_count.items():
        print(f'  {name}: {count}')


if __name__ == '__main__':
    main()
