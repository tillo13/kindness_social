"""Pollinations.ai adapter — keyless, 1 req/15s anonymous tier.

Source: https://raw.githubusercontent.com/pollinations/pollinations/master/APIDOCS.md
"""
import time
import urllib.parse
import urllib.request


def generate(prompt: str, *, width: int = 512, height: int = 512,
             model: str = 'flux', timeout: int = 60, user_agent: str = '',
             **_ignored) -> bytes | None:
    """Return image bytes (JPEG) or None on error.

    The caller (router) is responsible for honoring the 15-second spacing.
    This adapter just makes the single HTTP call.
    """
    enc = urllib.parse.quote(prompt)
    url = (f'https://image.pollinations.ai/prompt/{enc}'
           f'?width={width}&height={height}&model={model}'
           f'&nologo=true&seed={int(time.time())}')
    headers = {}
    if user_agent:
        headers['User-Agent'] = user_agent
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
        if len(body) > 5000 and body[:3] in (b'\xff\xd8\xff', b'\x89PN'):
            return body
    except Exception:
        pass
    return None
