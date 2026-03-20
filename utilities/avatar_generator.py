"""
Avatar Generator - Creates cartoon profile photos for agents via Flux Kontext Pro.
~$0.05-0.10 per image. Generated once, stored in static/images/avatars/.
"""

import logging
import os
import time
import requests
from utilities.google_secret_utils import get_secret

logger = logging.getLogger(__name__)

AVATAR_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'static', 'images', 'avatars')
os.makedirs(AVATAR_DIR, exist_ok=True)

REPLICATE_API = "https://api.replicate.com/v1"
_api_token = None


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


def get_avatar_path(agent_id):
    """Get the local file path for an agent's avatar."""
    return os.path.join(AVATAR_DIR, f"{agent_id}.jpg")


def get_avatar_url(agent_id):
    """Get the web URL for an agent's avatar. Returns None if not generated."""
    path = get_avatar_path(agent_id)
    if os.path.exists(path):
        return f"/static/images/avatars/{agent_id}.jpg"
    return None


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
    Generate a cartoon avatar for an agent using Flux.
    Returns the local file path, or None on failure.
    Skips if avatar already exists (unless force=True).
    """
    agent_id = agent.get('agent_id', 'unknown')
    path = get_avatar_path(agent_id)

    if os.path.exists(path) and not force:
        logger.info(f"Avatar already exists for {agent_id}, skipping")
        return path

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
                    img.save(path, 'JPEG', quality=80, optimize=True)
                    logger.info(f"Avatar saved: {path} ({os.path.getsize(path)} bytes)")
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
