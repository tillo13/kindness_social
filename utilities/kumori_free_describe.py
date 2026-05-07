"""
describe.py — single-call image → text across free vision LLMs.

Usage from any app:
    from kumori_free_image_describe.describe import describe_image
    result = describe_image("path/to/img.png")
    # {"text": "...", "backend": "groq-llama4-scout", "ms": 1873}

    # or pass raw bytes:
    result = describe_image(image_bytes, mime="image/jpeg")

    # or a custom prompt:
    result = describe_image(path, prompt="Is there a COLLECT button? Where?")

Fallback chain ordered by observed quality+speed. Each provider tried in
order; first non-error response wins. Raises RuntimeError only if ALL fail.

Secrets pulled from GCP Secret Manager (kumori-404602) on first call,
cached for process lifetime. Override by setting env vars directly.

Ranking (from 2026-04-19 bench on 1.5MB portrait, see test_vision.py):
  1. groq-llama4-scout           1.9s  generous free tier, great detail
  2. gemini-2.5-flash             2.8s  best prose, 250 req/day limit
  3. openrouter-gemma-4-26b       5.5s  richest detail (small objects)
  4. openrouter-gemma-4-31b       rate-limited often but high quality
  5. openrouter-nemotron-nano-vl  slow, can truncate
  6. nvidia-llama-3.2-90b-vision  flaky but highest param count
"""

import base64
import mimetypes
import os
import subprocess
import time

import requests


# ── Secrets ────────────────────────────────────────────────────────────────

_SECRET_MAP = {
    "GEMINI_API_KEY":        "KINDNESS_GEMINI_API_KEY",
    "GROQ_API_KEY":          "KINDNESS_GROQ_API_KEY",
    "OPENROUTER_API_KEY":    "KINDNESS_OPENROUTER_API_KEY",
    "NVIDIA_API_KEY":        "KINDNESS_NVIDIA_API_KEY",
}

_keys_cache = None
_get_secret_fn = None
_record_call_fn = None


def init(get_secret_fn=None, record_call_fn=None):
    """Inject Secret Manager fetcher + optional per-attempt callback.

    record_call_fn(provider: str, ok: bool, latency_ms: int, error: str|None)
    fires once per provider attempt inside describe_image()."""
    global _get_secret_fn, _record_call_fn, _keys_cache
    if get_secret_fn is not None:
        _get_secret_fn = get_secret_fn
        _keys_cache = None
    if record_call_fn is not None:
        _record_call_fn = record_call_fn


def _load_keys():
    global _keys_cache
    if _keys_cache is not None:
        return _keys_cache
    out = {}
    for env_name, secret_name in _SECRET_MAP.items():
        val = os.environ.get(env_name)
        if not val and _get_secret_fn:
            try:
                val = _get_secret_fn(secret_name)
            except Exception:
                val = None
        if not val:
            try:
                val = subprocess.check_output([
                    "gcloud", "secrets", "versions", "access", "latest",
                    f"--secret={secret_name}", "--project=kumori-404602",
                ], text=True, stderr=subprocess.DEVNULL, timeout=8).strip()
            except Exception:
                val = None
        out[env_name] = val or ""
    _keys_cache = out
    return out


# ── HTTP helper ────────────────────────────────────────────────────────────

def _post(url, headers, payload, timeout=90):
    last = None
    for attempt in range(3):
        try:
            with requests.Session() as s:
                r = s.post(url, json=payload,
                           headers={"Content-Type": "application/json", **headers},
                           timeout=timeout)
            if not r.ok:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
            return r.json()
        except requests.exceptions.SSLError as e:
            last = e
            time.sleep(0.5 * (attempt + 1))
    raise RuntimeError(f"SSL retries exhausted: {last}")


# ── Image prep ─────────────────────────────────────────────────────────────

def _prepare(image, mime):
    if isinstance(image, (bytes, bytearray)):
        data = bytes(image)
        if not mime:
            raise ValueError("mime required when passing raw bytes")
    else:
        with open(image, "rb") as f:
            data = f.read()
        mime = mime or mimetypes.guess_type(image)[0] or "image/png"
    return base64.b64encode(data).decode(), mime


# ── Provider callables (each returns text or raises) ───────────────────────

def _openai_compatible(url, key, model, prompt, b64, mime, extra_headers=None):
    headers = {"Authorization": f"Bearer {key}"}
    if extra_headers:
        headers.update(extra_headers)
    payload = {
        "model": model,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
            ],
        }],
        "max_tokens": 600,
    }
    resp = _post(url, headers, payload)
    msg = resp["choices"][0]["message"]
    text = msg.get("content") or msg.get("reasoning")
    if not text:
        raise RuntimeError(f"empty content: {str(msg)[:200]}")
    return text.strip()


def _gemini(key, model, prompt, b64, mime):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
    gen_cfg = {"maxOutputTokens": 800}
    if model.startswith("gemini-2.5"):
        gen_cfg["thinkingConfig"] = {"thinkingBudget": 0}
    payload = {
        "contents": [{"parts": [
            {"text": prompt},
            {"inline_data": {"mime_type": mime, "data": b64}},
        ]}],
        "generationConfig": gen_cfg,
    }
    resp = _post(url, {}, payload)
    cands = resp.get("candidates") or []
    if not cands:
        raise RuntimeError(f"no candidates: {str(resp)[:200]}")
    parts = cands[0].get("content", {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts).strip()
    if not text:
        raise RuntimeError(f"empty text: {str(resp)[:200]}")
    return text


OPENROUTER_HEADERS = {"HTTP-Referer": "https://kumori.ai",
                      "X-Title": "kumori-image-describe"}


def _build_providers(keys):
    return [
        ("groq-llama4-scout",
         lambda p, b, m: _openai_compatible(
             "https://api.groq.com/openai/v1/chat/completions",
             keys["GROQ_API_KEY"],
             "meta-llama/llama-4-scout-17b-16e-instruct", p, b, m)),
        ("gemini-2.5-flash",
         lambda p, b, m: _gemini(keys["GEMINI_API_KEY"], "gemini-2.5-flash", p, b, m)),
        ("openrouter-gemma-4-26b",
         lambda p, b, m: _openai_compatible(
             "https://openrouter.ai/api/v1/chat/completions",
             keys["OPENROUTER_API_KEY"],
             "google/gemma-4-26b-a4b-it:free", p, b, m,
             extra_headers=OPENROUTER_HEADERS)),
        ("openrouter-gemma-4-31b",
         lambda p, b, m: _openai_compatible(
             "https://openrouter.ai/api/v1/chat/completions",
             keys["OPENROUTER_API_KEY"],
             "google/gemma-4-31b-it:free", p, b, m,
             extra_headers=OPENROUTER_HEADERS)),
        ("openrouter-nemotron-nano-vl",
         lambda p, b, m: _openai_compatible(
             "https://openrouter.ai/api/v1/chat/completions",
             keys["OPENROUTER_API_KEY"],
             "nvidia/nemotron-nano-12b-v2-vl:free", p, b, m,
             extra_headers=OPENROUTER_HEADERS)),
        ("nvidia-llama-3.2-90b-vision",
         lambda p, b, m: _openai_compatible(
             "https://integrate.api.nvidia.com/v1/chat/completions",
             keys["NVIDIA_API_KEY"],
             "meta/llama-3.2-90b-vision-instruct", p, b, m)),
    ]


DEFAULT_PROMPT = (
    "Describe this image in detail. Include who or what you see, setting, "
    "colors, mood, and any text visible."
)


def describe_image(image, prompt=None, mime=None, skip=None):
    """
    image:  filesystem path OR raw bytes
    prompt: question / instruction (defaults to general description)
    mime:   required when passing bytes (e.g. 'image/jpeg')
    skip:   list of backend names to skip (e.g. ['gemini-2.5-flash'])

    Returns: {"text": str, "backend": str, "ms": int, "attempts": [...]}
    Raises:  RuntimeError if every provider fails.
    """
    prompt = prompt or DEFAULT_PROMPT
    skip = set(skip or [])
    b64, mime = _prepare(image, mime)
    keys = _load_keys()

    attempts = []
    for name, fn in _build_providers(keys):
        if name in skip:
            continue
        t0 = time.monotonic()
        try:
            text = fn(prompt, b64, mime)
            ms = int((time.monotonic() - t0) * 1000)
            attempts.append({"backend": name, "ok": True, "ms": ms})
            if _record_call_fn:
                try:
                    _record_call_fn(name, True, ms, None)
                except Exception:
                    pass
            return {"text": text, "backend": name, "ms": ms, "attempts": attempts}
        except Exception as e:
            ms = int((time.monotonic() - t0) * 1000)
            attempts.append({"backend": name, "ok": False, "ms": ms, "error": str(e)[:200]})
            if _record_call_fn:
                try:
                    _record_call_fn(name, False, ms, str(e)[:200])
                except Exception:
                    pass

    raise RuntimeError("all providers failed:\n" +
                       "\n".join(f"  {a['backend']}: {a.get('error')}" for a in attempts))


def list_providers() -> list[str]:
    """Names of every configured describe provider. Doesn't require keys."""
    return ["groq-llama4-scout", "gemini-2.5-flash",
            "openrouter-gemma-4-26b", "openrouter-gemma-4-31b",
            "openrouter-nemotron-nano-vl", "nvidia-llama-3.2-90b-vision"]


def probe_provider(name: str, image, mime=None,
                   prompt: str = "What color is dominant in this image? One word.") -> dict:
    """Probe a single describe provider. Returns:
        {'provider': name, 'ok': bool, 'latency_ms': int,
         'response_chars': int|None, 'error': str|None}
    """
    keys = _load_keys()
    providers = dict(_build_providers(keys))
    if name not in providers:
        return {'provider': name, 'ok': False, 'latency_ms': 0,
                'response_chars': None, 'error': 'not configured'}
    b64, mime = _prepare(image, mime)
    t0 = time.monotonic()
    try:
        text = providers[name](prompt, b64, mime)
        ms = int((time.monotonic() - t0) * 1000)
        if text and text.strip():
            return {'provider': name, 'ok': True, 'latency_ms': ms,
                    'response_chars': len(text), 'error': None}
        return {'provider': name, 'ok': False, 'latency_ms': ms,
                'response_chars': 0, 'error': 'empty response'}
    except Exception as e:
        ms = int((time.monotonic() - t0) * 1000)
        err_text = type(e).__name__ + ': ' + str(e)[:180]
        # alive_proven: HTTP 429 means the upstream model server returned a
        # real response — endpoint is alive and reachable, just throttled.
        # Same semantic as kumori canary's HTTP-429-is-pass rule (see memory
        # feedback_429_is_alive_proven). Marks ok=True so health rates and
        # sample-table aggregations don't false-fail throttled providers
        # during normal free-tier cap usage.
        is_throttled = 'HTTP 429' in str(e)
        return {'provider': name, 'ok': is_throttled,
                'latency_ms': ms,
                'response_chars': 0 if is_throttled else None,
                'error': err_text}


if __name__ == "__main__":
    import argparse, json
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--prompt")
    ap.add_argument("--skip", help="comma-separated backend names to skip")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    skip = args.skip.split(",") if args.skip else None
    r = describe_image(args.image, prompt=args.prompt, skip=skip)
    if args.json:
        print(json.dumps(r, indent=2))
    else:
        print(f"[{r['backend']} · {r['ms']}ms]")
        if len(r["attempts"]) > 1:
            failed = [a for a in r["attempts"] if not a["ok"]]
            print(f"(fell past {len(failed)}: {', '.join(a['backend'] for a in failed)})")
        print()
        print(r["text"])
