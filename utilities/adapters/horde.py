"""Stable Horde adapter — anonymous community-run free GPU pool.

Source: https://stablehorde.net/api/
"""
import json
import time
import urllib.request


def generate(prompt: str, *, api_key: str = '0000000000',
             width: int = 512, height: int = 512, steps: int = 20,
             sampler: str = 'k_euler', cfg_scale: float = 7.5,
             model: str = 'stable_diffusion',
             poll_interval: int = 3, max_poll_seconds: int = 240,
             **_ignored) -> bytes | None:
    """Submit job, poll until done, fetch result. Returns image bytes or None."""
    headers = {'apikey': api_key, 'Content-Type': 'application/json'}
    submit = json.dumps({
        'prompt': prompt,
        'params': {
            'sampler_name': sampler, 'cfg_scale': cfg_scale,
            'width': width, 'height': height, 'steps': steps,
        },
        'nsfw': False, 'censor_nsfw': True,
        'models': [model], 'r2': True,
    }).encode()
    try:
        req = urllib.request.Request(
            'https://stablehorde.net/api/v2/generate/async',
            data=submit, headers=headers, method='POST',
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            sub = json.loads(r.read().decode())
        job_id = sub.get('id')
        if not job_id:
            return None
        # Poll
        deadline = time.time() + max_poll_seconds
        while time.time() < deadline:
            time.sleep(poll_interval)
            try:
                with urllib.request.urlopen(
                    f'https://stablehorde.net/api/v2/generate/check/{job_id}', timeout=10,
                ) as r:
                    ck = json.loads(r.read().decode())
                if ck.get('done'):
                    break
            except Exception:
                continue
        # Fetch final
        with urllib.request.urlopen(
            f'https://stablehorde.net/api/v2/generate/status/{job_id}', timeout=10,
        ) as r:
            res = json.loads(r.read().decode())
        gens = res.get('generations', [])
        if gens and gens[0].get('img'):
            with urllib.request.urlopen(gens[0]['img'], timeout=30) as r:
                return r.read()
    except Exception:
        pass
    return None
