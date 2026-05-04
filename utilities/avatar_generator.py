"""
Avatar Generator - Creates cartoon profile photos for agents via Flux Schnell (Replicate).
Local dev: saves to static/images/avatars/ (deployed with code).
App Engine: uploads to GCS bucket (served via public URL).
Both paths checked when resolving avatar URLs.
"""

import logging
import os
import time
import requests
from utilities.google_secret_utils import get_secret

logger = logging.getLogger(__name__)

AVATAR_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'static', 'images', 'avatars')
os.makedirs(AVATAR_DIR, exist_ok=True)

GCS_BUCKET = 'kindness-io-avatars'
GCS_PUBLIC_URL = f'https://storage.googleapis.com/{GCS_BUCKET}'

REPLICATE_API = "https://api.replicate.com/v1"
_api_token = None
_gcs_client = None

_image_stats_wired = False
APP_NAME = 'kindness_social'


def _record_image_call(provider: str, ok: bool, latency_ms: int,
                       error: str | None, kind: str):
    """DI callback for kumori_free_imggen + kumori_free_describe.
    Writes successes to kumori_image_daily_caps and every attempt to
    kumori_image_health_samples. Catches all errors — must never break gen."""
    try:
        from utilities.postgres_utils import get_db_connection
        conn = get_db_connection()
        cur = conn.cursor()
        if ok:
            cur.execute("""
                INSERT INTO kumori_image_daily_caps
                    (usage_date, provider, app_name, kind, call_count)
                VALUES (CURRENT_DATE, %s, %s, %s, 1)
                ON CONFLICT (usage_date, provider, app_name, kind)
                DO UPDATE SET call_count = kumori_image_daily_caps.call_count + 1
            """, (provider, APP_NAME, kind))
        cur.execute("""
            INSERT INTO kumori_image_health_samples
                (provider, kind, status, latency_ms, error_excerpt, probe_source)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (provider, kind, 'ok' if ok else 'fail',
              latency_ms, error, f'real_traffic:{APP_NAME}'))
        conn.commit()
        cur.close(); conn.close()
    except Exception as e:
        logger.debug(f'_record_image_call swallowed: {e}')


def _ensure_image_stats_wired():
    """Idempotently inject our DI callback into both image libs."""
    global _image_stats_wired
    if _image_stats_wired:
        return
    try:
        from utilities import kumori_free_imggen
        from utilities.google_secret_utils import get_secret as _gs
        kumori_free_imggen.init(
            get_secret_fn=_gs,
            record_call_fn=lambda p, ok, ms, err: _record_image_call(p, ok, ms, err, 'gen'),
        )
    except Exception as e:
        logger.warning(f'imggen wiring skipped: {e}')
    try:
        from utilities import kumori_free_describe
        from utilities.google_secret_utils import get_secret as _gs
        kumori_free_describe.init(
            get_secret_fn=_gs,
            record_call_fn=lambda p, ok, ms, err: _record_image_call(p, ok, ms, err, 'describe'),
        )
    except Exception as e:
        logger.warning(f'describe wiring skipped: {e}')
    _image_stats_wired = True


def _async_describe(img_bytes: bytes, agent_id: str, mime: str = 'image/jpeg'):
    """Fire-and-forget describe in a background thread. Never blocks caller."""
    import threading

    def _run():
        try:
            from utilities import kumori_free_describe
            r = kumori_free_describe.describe_image(
                img_bytes, mime=mime,
                prompt='Describe this avatar in one short sentence.',
            )
            logger.info(f'avatar describe[{agent_id}]: '
                        f'{r["backend"]}={r["text"][:80]}')
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


def get_avatar_path(agent_id):
    """Get the local file path for an agent's avatar."""
    return os.path.join(AVATAR_DIR, f"{agent_id}.jpg")


def avatar_exists(agent_id):
    """True if a usable avatar exists either locally or in GCS."""
    if os.path.exists(get_avatar_path(agent_id)):
        return True
    try:
        bucket = _get_gcs_bucket()
        return bucket.blob(f'{agent_id}.jpg').exists()
    except Exception as e:
        logger.warning(f"avatar_exists GCS check failed for {agent_id}: {e}")
        return False


_LOCAL_AVATAR_IDS = None
def _local_avatar_ids():
    """Memoize the set of agent_ids that ship with a committed local JPG.
    On App Engine the static dir is read-only, so this set never changes
    after process start — safe to compute once."""
    global _LOCAL_AVATAR_IDS
    if _LOCAL_AVATAR_IDS is None:
        try:
            _LOCAL_AVATAR_IDS = {
                f[:-4] for f in os.listdir(AVATAR_DIR) if f.endswith('.jpg')
            }
        except OSError:
            _LOCAL_AVATAR_IDS = set()
    return _LOCAL_AVATAR_IDS


def get_avatar_url(agent_id):
    """Get the web URL for an agent's avatar. Returns local for committed
    seed agents, GCS for everything else. No 404 round-trips."""
    if agent_id in _local_avatar_ids():
        return f"/static/images/avatars/{agent_id}.jpg"
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
    Generate a cartoon avatar for an agent using the kumori_free_imggen
    router (truly-free providers only — Pollinations + Stable Horde with
    Cloudflare Workers AI as bearer-keyed fallback).

    Returns the local file path, or None on failure.
    Skips if avatar already exists (unless force=True).

    Re-enabled 2026-04-27. The original Replicate-paid path is preserved
    below as `_generate_avatar_via_replicate()` for reference but is no
    longer called.
    """
    agent_id = agent.get('agent_id', 'unknown')
    path = get_avatar_path(agent_id)

    if os.path.exists(path) and not force:
        logger.info(f"Avatar already exists for {agent_id}, skipping")
        return path

    prompt = build_prompt(agent)
    logger.info(f"avatar_generator: requesting via kumori_free_imggen for {agent_id}")
    try:
        from utilities import kumori_free_imggen
    except Exception as e:
        logger.error(f"kumori_free_imggen import failed: {e}")
        return None

    # One-time DI: route gen + describe attempts to the shared image stats tables.
    _ensure_image_stats_wired()

    img_bytes = kumori_free_imggen.generate_image(prompt, width=512, height=512)
    if not img_bytes:
        logger.warning(f"avatar_generator: no provider returned an image for {agent_id}")
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
    # describe pipeline still works. Results land in kumori_image_health_samples
    # via the DI callback wired in _ensure_image_stats_wired().
    _async_describe(img_bytes, agent_id, mime='image/jpeg' if is_jpeg else 'image/png')

    # Save locally if writable; always upload to GCS for App Engine + cross-instance use
    try:
        with open(path, 'wb') as f:
            f.write(img_bytes)
        logger.info(f"Avatar saved locally: {path} ({len(img_bytes)} bytes)")
    except (OSError, IOError):
        logger.info(f"Local save failed (read-only fs), using GCS only")
    _upload_to_gcs(agent_id, img_bytes)
    return path

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
        # Check local
        if os.path.exists(get_avatar_path(aid)):
            continue
        # Check GCS — quick HEAD request
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
