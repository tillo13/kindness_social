"""kumori_free_imggen — drop-in image generation router for kindness_social
(and any other consumer). Mirrors the kumori_free_llms.py shape: services
loaded from providers.json, in-process rate-limit tracking, fallback chain.

Public API:
    generate_image(prompt, width=512, height=512) -> bytes | None

The router walks providers in tier order, skipping any that are in cooldown
or violating their min_seconds_between_requests. First service to return
valid image bytes wins.

Caller can inject get_secret_fn for environment-specific Secret Manager
(App Engine SDK, gcloud CLI, env var, etc). Without it, falls back to
gcloud CLI (works locally, fails silently on App Engine).
"""
import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

logger = logging.getLogger('kumori_free_imggen')

_HERE = Path(__file__).resolve().parent
# Ensure adapters/ next to this file is importable regardless of where this
# module lives (works in the canonical _infrastructure dir AND when vendored
# into utilities/ via deploy.json shared_files).
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
_PROVIDERS = None  # loaded lazily

# In-process state for rate limiting. {service_name: timestamp_of_last_call}
_last_call: dict[str, float] = {}
_backoff_until: dict[str, float] = {}
_daily_count: dict[str, int] = {}
_daily_count_date = None
_lock = threading.Lock()

_get_secret_fn = None  # injected; falls back to gcloud subprocess


def init(get_secret_fn=None):
    """Inject a Secret Manager fetcher. Optional — falls back to gcloud CLI."""
    global _get_secret_fn
    _get_secret_fn = get_secret_fn


def _gcloud_secret(name: str, project: str = 'kumori-404602') -> str | None:
    if _get_secret_fn:
        try:
            return _get_secret_fn(name)
        except Exception:
            pass
    try:
        return subprocess.check_output(
            ['gcloud', 'secrets', 'versions', 'access', 'latest',
             '--secret', name, '--project', project],
            text=True, stderr=subprocess.DEVNULL, timeout=8,
        ).strip()
    except Exception:
        return None


def _providers() -> dict:
    global _PROVIDERS
    if _PROVIDERS is None:
        with open(_HERE / 'providers.json') as f:
            data = json.load(f)
        _PROVIDERS = {k: v for k, v in data.items() if not k.startswith('_')}
    return _PROVIDERS


def _reset_daily_if_needed():
    global _daily_count_date
    from datetime import date
    today = date.today()
    if _daily_count_date != today:
        _daily_count.clear()
        _daily_count_date = today


def _is_available(name: str, cfg: dict) -> tuple[bool, str]:
    """Return (ok, reason). reason populated only if not ok."""
    now = time.time()
    if _backoff_until.get(name, 0) > now:
        return False, f'in backoff for {int(_backoff_until[name] - now)}s'
    spacing = cfg.get('min_seconds_between_requests', 0)
    last = _last_call.get(name, 0)
    if now - last < spacing:
        return False, f'rate-spaced; {spacing - (now - last):.1f}s remaining'
    _reset_daily_if_needed()
    cap = cfg.get('daily_limit')
    if cap:
        # Shared pool aware: if shared_pool set, count across pool members
        pool = cfg.get('shared_pool')
        if pool:
            used = sum(_daily_count.get(n, 0) for n, c in _providers().items()
                       if c.get('shared_pool') == pool)
        else:
            used = _daily_count.get(name, 0)
        if used >= cap:
            return False, f'daily quota exhausted ({used}/{cap})'
    return True, ''


def _record_attempt(name: str, ok: bool, http_code: int | None = None):
    now = time.time()
    _last_call[name] = now
    if ok:
        _daily_count[name] = _daily_count.get(name, 0) + 1
        return
    cfg = _providers()[name]
    if http_code == 429:
        _backoff_until[name] = now + cfg.get('backoff_on_429_sec', 60)
    else:
        _backoff_until[name] = now + cfg.get('backoff_on_5xx_sec', 30)


def _call_pollinations(name: str, cfg: dict, prompt: str, w: int, h: int) -> bytes | None:
    from adapters import pollinations
    return pollinations.generate(
        prompt, width=w, height=h,
        model=cfg.get('default_model', 'flux'),
        timeout=cfg.get('timeout_sec', 60),
        user_agent=cfg.get('user_agent', ''),
    )


def _call_cloudflare(name: str, cfg: dict, prompt: str, w: int, h: int) -> bytes | None:
    from adapters import cloudflare
    api_key = _gcloud_secret(cfg['secret_name'])
    if not api_key:
        return None
    model = 'dreamshaper' if 'dreamshaper' in name else 'flux'
    return cloudflare.generate(
        prompt, api_key=api_key, account_id=cfg['account_id'],
        model=model, steps=cfg.get('default_steps', 4),
        width=w, height=h, timeout=cfg.get('timeout_sec', 30),
    )


def _call_horde(name: str, cfg: dict, prompt: str, w: int, h: int) -> bytes | None:
    from adapters import horde
    api_key = (_gcloud_secret(cfg['secret_name'])
               if cfg.get('secret_name') else cfg.get('anonymous_key', '0000000000'))
    return horde.generate(
        prompt, api_key=api_key or '0000000000',
        width=w, height=h,
        steps=cfg.get('default_steps', 20),
        sampler=cfg.get('default_sampler', 'k_euler'),
        cfg_scale=cfg.get('default_cfg_scale', 7.5),
        model=cfg.get('default_model', 'stable_diffusion'),
        poll_interval=cfg.get('poll_interval_sec', 3),
        max_poll_seconds=cfg.get('max_poll_seconds', 240),
    )


_DISPATCH = {
    'pollinations':           _call_pollinations,
    'cloudflare_flux':        _call_cloudflare,
    'cloudflare_dreamshaper': _call_cloudflare,
    'stable_horde':           _call_horde,
}


def generate_image(prompt: str, width: int = 512, height: int = 512) -> bytes | None:
    """Try each registered service in tier order. Return first success.

    Returns image bytes (JPEG or PNG) or None if every service failed/skipped.
    """
    services = sorted(_providers().items(), key=lambda kv: kv[1].get('tier', 99))
    for name, cfg in services:
        with _lock:
            ok, reason = _is_available(name, cfg)
        if not ok:
            logger.info(f'imggen: skip {name} ({reason})')
            continue
        if name not in _DISPATCH:
            continue
        t0 = time.time()
        img = _DISPATCH[name](name, cfg, prompt, width, height)
        ms = int((time.time() - t0) * 1000)
        with _lock:
            _record_attempt(name, ok=bool(img))
        if img:
            logger.info(f'imggen: {name} OK in {ms}ms ({len(img)}B)')
            return img
        logger.warning(f'imggen: {name} returned no image after {ms}ms — trying next')
    logger.error('imggen: every service failed')
    return None
