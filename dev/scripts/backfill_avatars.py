#!/usr/bin/env python3
"""backfill_avatars.py — generate missing avatars for every agent that
doesn't already have one in GCS, via kumori's free image-gen surface.

Post-2026-05-12 migration: this script no longer imports the in-process
router. It goes through `kumori_api_client.imggen_generate()` over HTTP,
same path as the live `utilities/avatar_generator.py`. Kumori handles the
round-robin across free providers (Pollinations + Stable Horde +
Cloudflare Workers AI) and records every call in `kumori_api_usage`.

Run locally:
    cd ~/Desktop/code/kindness_social
    python3 dev/scripts/backfill_avatars.py             # dry run (just lists missing)
    python3 dev/scripts/backfill_avatars.py --apply     # actually generate + upload
    python3 dev/scripts/backfill_avatars.py --apply --limit 5   # only do first 5

Cost: $0 — all backends are free-tier; kumori manages the daily caps.
Requires: `KUMORI_API_KEY` env var, OR `KINDNESS_KUMORI_API_KEY` accessible
via gcloud secrets (auto-resolved by kumori_api_client at startup).
"""
import argparse
import base64
import os
import subprocess
import sys
import time
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]   # kindness_social/
sys.path.insert(0, str(ROOT))

from utilities import kumori_api_client  # noqa: E402
from utilities.avatar_generator import build_prompt, _upload_to_gcs, get_avatar_path  # noqa: E402


def _gcloud_secret(name: str) -> str:
    return subprocess.check_output(
        ['gcloud', 'secrets', 'versions', 'access', 'latest',
         '--secret', name, '--project', 'kumori-404602'],
        text=True, stderr=subprocess.DEVNULL, timeout=10,
    ).strip()


def _init_api_client():
    """Resolve a kumori API key for the HTTP client. Tries env var first
    (useful for local override), then KINDNESS_KUMORI_API_KEY in Secret
    Manager via gcloud."""
    if os.environ.get('KUMORI_API_KEY'):
        return  # kumori_api_client picks this up automatically
    try:
        key = _gcloud_secret('KINDNESS_KUMORI_API_KEY')
        os.environ['KUMORI_API_KEY'] = key
    except Exception as e:
        sys.exit(f'ERR: could not load KINDNESS_KUMORI_API_KEY from Secret Manager: {e}')


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
    """Generate + upload one agent's avatar via kumori's HTTP imggen surface.
    Returns (ok, detail_msg, latency_ms)."""
    prompt = build_prompt(agent)
    t0 = time.time()
    result = kumori_api_client.imggen_generate(
        prompt, width=512, height=512, mode='roundrobin',
        feature='avatar.backfill', verbiage=prompt[:500],
        caller_user_id=agent['agent_id'],
    )
    ms = int((time.time() - t0) * 1000)
    if not result or not result.get('ok') or not result.get('image_b64'):
        err = (result or {}).get('error', 'no_response')
        return False, f'imggen failed: {err}', ms
    try:
        img_bytes = base64.b64decode(result['image_b64'])
    except Exception as e:
        return False, f'image_b64 decode failed: {e}', ms
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
    return True, f'{len(img_bytes)}B → {url} via {result.get("provider", "?")}', ms


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true', help='Actually generate (default: dry-run)')
    ap.add_argument('--limit', type=int, default=0, help='Only process first N (0 = all)')
    args = ap.parse_args()

    _init_api_client()

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

    print(f'\n=== generating {len(missing)} avatars via kumori_api_client ===\n', flush=True)
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
    print('Per-provider usage: see https://kumori.ai/admin/api-costs (filtered by feature=avatar.backfill).')


if __name__ == '__main__':
    main()
