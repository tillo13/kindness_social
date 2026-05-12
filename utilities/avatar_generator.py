"""
Avatar Generator — generates cartoon profile photos for agents via kumori's
free imggen surface (HTTP). Single source of truth: GCS bucket
`kindness-io-avatars`. Post 2026-05-12 migration: no more static-file
fallback. Every avatar lives in GCS, resolved via `get_avatar_url(agent_id)`.
"""

import logging
import os
import time
import requests
from utilities.google_secret_utils import get_secret

logger = logging.getLogger(__name__)

GCS_BUCKET = 'kindness-io-avatars'
GCS_PUBLIC_URL = f'https://storage.googleapis.com/{GCS_BUCKET}'

REPLICATE_API = "https://api.replicate.com/v1"
_api_token = None
_gcs_client = None

APP_NAME = 'kindness_social'


def _async_describe(img_bytes: bytes, agent_id: str, mime: str = 'image/jpeg'):
    """Fire-and-forget describe in a background thread. Never blocks caller.

    Persists the description + backend used to kindness_agents so the agent
    profile page can show "Avatar described by X as 'foo'" — turns each new
    agent birth into a real-workload validation chain across modalities
    (chat → image-gen → image-describe), per the 2026-05-05 lifecycle plan.

    Goes through kumori_api_client over HTTP — kumori records its own usage
    stats (kumori_api_usage) so kindness no longer maintains a parallel
    kumori_image_health_samples write path.
    """
    import threading
    import base64

    def _run():
        try:
            from utilities import kumori_api_client
            from utilities.postgres_utils import db_cursor
            img_b64 = base64.b64encode(img_bytes).decode('ascii')
            r = kumori_api_client.describe_image(
                image_b64=img_b64, mime=mime,
                prompt='Describe this avatar in one short sentence.',
            )
            logger.info(f'avatar describe[{agent_id}]: '
                        f'{r.get("backend")}={(r.get("text") or "")[:80]}')
            try:
                with db_cursor(commit=True) as cur:
                    cur.execute("""
                        UPDATE kindness_agents
                           SET avatar_description = %s,
                               avatar_describe_backend = %s,
                               avatar_described_at = NOW()
                         WHERE agent_id = %s
                    """, (r.get('text', '')[:600], r.get('backend'), agent_id))
            except Exception as db_e:
                logger.warning(f'avatar describe[{agent_id}] db write failed: {db_e}')
        except Exception as e:
            logger.warning(f'avatar describe[{agent_id}] failed: {e}')

    threading.Thread(target=_run, daemon=True).start()


def _is_appengine():
    return os.environ.get('GAE_ENV', '').startswith('standard')


def _get_token():
    global _api_token
    if _api_token is None:
        _api_token = get_secret('KUMORI_REPLICATE_API_KEY')
        if not _api_token:
            raise RuntimeError("KUMORI_REPLICATE_API_KEY not found")
    return _api_token


def _headers():
    return {
        "Authorization": f"Bearer {_get_token()}",
        "Content-Type": "application/json",
    }


def _get_gcs_bucket():
    global _gcs_client
    if _gcs_client is None:
        from google.cloud import storage
        client = storage.Client(project='kindness-io')
        _gcs_client = client.bucket(GCS_BUCKET)
    return _gcs_client


def _upload_to_gcs(agent_id, image_bytes):
    """Upload avatar bytes to GCS. Returns public URL."""
    try:
        bucket = _get_gcs_bucket()
        blob = bucket.blob(f'{agent_id}.jpg')
        blob.upload_from_string(image_bytes, content_type='image/jpeg')
        # make_public() fails on uniform-bucket-level-access buckets — ignore;
        # the bucket should have allUsers:objectViewer set at the IAM level instead.
        try:
            blob.make_public()
        except Exception as e:
            logger.debug(f"make_public skipped for {agent_id} (likely UBLA bucket): {e}")
        url = f'{GCS_PUBLIC_URL}/{agent_id}.jpg'
        logger.info(f"Avatar uploaded to GCS: {url}")
        return url
    except Exception as e:
        logger.error(f"GCS upload failed for {agent_id}: {e}")
        return None


def avatar_exists(agent_id):
    """True if a GCS avatar exists for this agent. Static-file fallback was
    dropped 2026-05-12 after the seed-file → GCS migration; every avatar
    now lives in GCS as the single source of truth."""
    try:
        bucket = _get_gcs_bucket()
        return bucket.blob(f'{agent_id}.jpg').exists()
    except Exception as e:
        logger.warning(f"avatar_exists GCS check failed for {agent_id}: {e}")
        return False


def get_avatar_url(agent_id):
    """Get the web URL for an agent's avatar. Always GCS post-migration —
    no static-file fallback, no listdir cache, no 404 round-trips."""
    return f"{GCS_PUBLIC_URL}/{agent_id}.jpg"


def build_prompt(agent):
    """Build a Flux prompt from the agent's full identity profile."""
    tox = agent.get('current_toxicity', 5)
    emp = agent.get('current_empathy', 5)
    humor = agent.get('humor', 5)
    patience = agent.get('patience', 5)
    gender = agent.get('gender_presentation', 'unspecified')
    age = agent.get('age_bracket', 'middle_aged')
    authority = agent.get('authority_level', 'medium')
    color = agent.get('color_hex', '#6B7280')

    # Gender presentation
    if gender == 'male':
        gender_desc = "man"
    elif gender == 'female':
        gender_desc = "woman"
    elif gender == 'nonbinary':
        gender_desc = "androgynous person"
    else:
        gender_desc = "person"

    # Age
    age_map = {
        'young_adult': f"young {gender_desc} in their early 20s",
        'middle_aged': f"middle-aged {gender_desc} in their 40s",
        'senior': f"older {gender_desc} in their late 60s with gray hair",
    }
    person_desc = age_map.get(age, f"middle-aged {gender_desc}")

    # Expression from personality
    if tox >= 7:
        expression = "scowling, furrowed brows, intense angry expression, clenched jaw"
    elif tox >= 5:
        expression = "slightly annoyed, skeptical raised eyebrow, tight lips"
    elif emp >= 8 and patience >= 8:
        expression = "serene peaceful smile, calm wise eyes, gentle warmth"
    elif emp >= 7:
        expression = "warm genuine smile, kind open eyes, approachable"
    elif humor >= 7:
        expression = "playful smirk, mischievous bright eyes, amused"
    else:
        expression = "neutral thoughtful expression, attentive eyes"

    # Authority in clothing/posture
    if authority == 'high':
        style_hint = "wearing professional attire, confident posture"
    elif authority == 'low':
        style_hint = "wearing casual clothes, relaxed posture"
    else:
        style_hint = "wearing everyday clothes"

    prompt = (
        f"Stylized cartoon portrait headshot of a {person_desc}, "
        f"{expression}, {style_hint}. "
        f"Bold clean outlines, soft cel-shading, rounded features. "
        f"Subtle accent color {color} in clothing or background element. "
        f"Warm neutral background with soft gradient. "
        f"Profile photo style, centered face, shoulders visible. "
        f"Pixar-meets-editorial-illustration style, professional but approachable. "
        f"High quality, clean vector-like rendering."
    )
    return prompt


def generate_avatar(agent, force=False):
    """
    Generate a cartoon avatar for an agent via kumori_api_client over HTTP
    (routes through kumori's /api/v1/imggen/generate — same free-provider
    fallback chain: Pollinations + Stable Horde + Cloudflare Workers AI).

    Returns the local file path, or None on failure.
    Skips if avatar already exists (unless force=True).

    Re-enabled 2026-04-27. The original Replicate-paid path is preserved
    below as `_generate_avatar_via_replicate()` for reference but is no
    longer called.
    """
    agent_id = agent.get('agent_id', 'unknown')

    if not force and avatar_exists(agent_id):
        logger.info(f"Avatar already exists for {agent_id}, skipping")
        return f"{GCS_PUBLIC_URL}/{agent_id}.jpg"

    prompt = build_prompt(agent)
    logger.info(f"avatar_generator: requesting via kumori_api_client for {agent_id}")
    try:
        from utilities import kumori_api_client
    except Exception as e:
        logger.error(f"kumori_api_client import failed: {e}")
        return None

    import base64
    result = kumori_api_client.imggen_generate(
        prompt, width=512, height=512, mode='roundrobin',
        feature='avatar', verbiage=prompt[:500],
        caller_user_id=agent_id,
    )
    if not result or not result.get('ok') or not result.get('image_b64'):
        logger.warning(f"avatar_generator: no provider returned an image for {agent_id} "
                       f"(err={result.get('error') if result else 'no_response'})")
        return None
    try:
        img_bytes = base64.b64decode(result['image_b64'])
    except Exception as e:
        logger.error(f"avatar_generator: image_b64 decode failed for {agent_id}: {e}")
        return None

    # Normalize to 256x256 JPEG (matches the old Replicate path's storage shape)
    is_jpeg = False
    try:
        from PIL import Image
        from io import BytesIO
        img = Image.open(BytesIO(img_bytes)).convert('RGB')
        img = img.resize((256, 256), Image.LANCZOS)
        buf = BytesIO()
        img.save(buf, 'JPEG', quality=80, optimize=True)
        img_bytes = buf.getvalue()
        is_jpeg = True
    except Exception as e:
        logger.warning(f"avatar resize failed for {agent_id}, saving raw bytes: {e}")

    # Free QA layer: fire-and-forget describe of the avatar we just made.
    # Validates that (a) imggen returned coherent image bytes, (b) the
    # describe pipeline still works. Goes through kumori_api_client; kumori
    # records its own usage row in kumori_api_usage.
    _async_describe(img_bytes, agent_id, mime='image/jpeg' if is_jpeg else 'image/png')

    url = _upload_to_gcs(agent_id, img_bytes)
    return url

    # ── unreachable — preserved for the day Andy re-enables ─────────────────
    prompt = build_prompt(agent)
    logger.info(f"Generating avatar for {agent_id}: {prompt[:80]}...")

    try:
        # Create prediction via Replicate HTTP API (no SDK needed)
        resp = requests.post(
            f"{REPLICATE_API}/models/black-forest-labs/flux-schnell/predictions",
            headers=_headers(),
            json={
                "input": {
                    "prompt": prompt,
                    "num_outputs": 1,
                    "aspect_ratio": "1:1",
                    "output_format": "png",
                    "output_quality": 90,
                }
            },
            timeout=30,
        )
        resp.raise_for_status()
        prediction = resp.json()

        # Poll for completion
        poll_url = prediction.get('urls', {}).get('get', f"{REPLICATE_API}/predictions/{prediction['id']}")
        for _ in range(60):  # Max 60 seconds
            time.sleep(1)
            poll_resp = requests.get(poll_url, headers=_headers(), timeout=10)
            poll_data = poll_resp.json()
            status = poll_data.get('status')

            if status == 'succeeded':
                output = poll_data.get('output', [])
                if output:
                    img_url = output[0] if isinstance(output, list) else output
                    img_resp = requests.get(str(img_url), timeout=30)
                    img_resp.raise_for_status()
                    # Compress to 256x256 JPG for fast loading
                    from PIL import Image
                    from io import BytesIO
                    img = Image.open(BytesIO(img_resp.content)).convert('RGB')
                    img = img.resize((256, 256), Image.LANCZOS)
                    buf = BytesIO()
                    img.save(buf, 'JPEG', quality=80, optimize=True)
                    img_bytes = buf.getvalue()

                    # Save locally if possible, always upload to GCS
                    try:
                        with open(path, 'wb') as f:
                            f.write(img_bytes)
                        logger.info(f"Avatar saved locally: {path} ({len(img_bytes)} bytes)")
                    except (OSError, IOError):
                        logger.info(f"Local save failed (read-only fs), using GCS only")

                    _upload_to_gcs(agent_id, img_bytes)
                    return path
                break
            elif status == 'failed':
                logger.error(f"Flux prediction failed for {agent_id}: {poll_data.get('error')}")
                break

        return None

    except Exception as e:
        logger.error(f"Avatar generation failed for {agent_id}: {e}")
        return None


def generate_all_avatars(agents, force=False):
    """Generate avatars for a list of agents. Returns count of generated."""
    count = 0
    for agent in agents:
        result = generate_avatar(agent, force=force)
        if result:
            count += 1
    return count


def backfill_missing_avatars(max_per_run=10):
    """
    Find active agents with no avatar (local or GCS) and generate them.
    Called by cron to self-heal after generation failures.
    Returns dict with counts.
    """
    from utilities.postgres_utils import get_db_connection

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT agent_id, current_toxicity, current_empathy, humor, patience,
               curiosity, defensiveness, agreeableness, gender_presentation,
               age_bracket, authority_level, color_hex
        FROM kindness_agents WHERE is_active = true
    """)
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    conn.close()

    missing = []
    for row in rows:
        agent = dict(zip(cols, row))
        aid = agent['agent_id']
        # Single source of truth: GCS
        try:
            resp = requests.head(f"{GCS_PUBLIC_URL}/{aid}.jpg", timeout=5)
            if resp.status_code == 200:
                continue
        except Exception:
            pass
        missing.append(agent)

    if not missing:
        return {'missing': 0, 'generated': 0, 'failed': 0}

    generated = 0
    failed = 0
    for agent in missing[:max_per_run]:
        result = generate_avatar(agent)
        if result:
            generated += 1
        else:
            failed += 1

    logger.info(f"Avatar backfill: {len(missing)} missing, {generated} generated, {failed} failed (cap {max_per_run})")
    return {'missing': len(missing), 'generated': generated, 'failed': failed}
