"""Cloudflare Workers AI adapter — flux-1-schnell + dreamshaper-8-lcm.

Source: https://developers.cloudflare.com/workers-ai/
"""
import base64
import json
import urllib.request


def generate(prompt: str, *, api_key: str, account_id: str,
             model: str = 'flux', steps: int = 4, width: int = 512, height: int = 512,
             timeout: int = 30, **_ignored) -> bytes | None:
    """Generate via Cloudflare Workers AI. Returns image bytes or None.

    Two model paths:
      model='flux'       -> @cf/black-forest-labs/flux-1-schnell (returns base64 JSON)
      model='dreamshaper' -> @cf/lykon/dreamshaper-8-lcm        (returns binary)

    Caller must pass api_key (KINDNESS_CLOUDFLARE_API_KEY) and account_id.
    """
    if model == 'dreamshaper':
        path = '@cf/lykon/dreamshaper-8-lcm'
        body = json.dumps({'prompt': prompt}).encode()
        binary_response = True
    else:
        path = '@cf/black-forest-labs/flux-1-schnell'
        body = json.dumps({'prompt': prompt, 'steps': steps}).encode()
        binary_response = False

    url = f'https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{path}'
    req = urllib.request.Request(
        url, data=body, method='POST',
        headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
        if binary_response:
            if len(raw) > 5000 and raw[:3] in (b'\xff\xd8\xff', b'\x89PN'):
                return raw
            return None
        payload = json.loads(raw.decode())
        img_b64 = (payload.get('result') or {}).get('image')
        if img_b64:
            return base64.b64decode(img_b64)
    except Exception:
        pass
    return None
