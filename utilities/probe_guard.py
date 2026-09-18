"""probe_guard: turn away secret-file scanners and per-IP floods before a request costs anything.

Why it exists: on 2026-09-17 02:30 PT and 2026-09-18 04:02 PT two credential scanners (66.187.5.19,
a Hostodo VPS; 45.148.10.21, RIPE/NL) hit kindness.social with 400+ requests a minute for /.git/config,
/.env*, /.aws/credentials and the like. Nothing leaked (every probe got a 404), but each 404 rendered a
38 KB page on one F1 instance running two threads, so App Engine queued real visitors until it gave up
("Request was aborted after waiting too long"): ~2,000 errors in two six-minute windows. The spoofed
Chrome user agent also slipped past visitor_logging's bot sampling, so every probe was a telemetry
row on the shared Cloud SQL.

Three cheap checks, run before every other before_request hook:
  1. a path that can only be a probe (a dot-directory other than /.well-known, or a server-script or
     config-file extension outside /static) gets a bare 404: no template, no DB, no visitor log;
  2. an IP that sends STRIKES_TO_BLOCK probes is refused for BLOCK_SECONDS, whatever it asks for;
  3. any IP past RATE_LIMIT requests in RATE_WINDOW seconds gets a 429 with Retry-After, through
     kumori's canonical token bucket (kumori/utilities/rate_limit.py), not a second limiter.
State is per process, which is exact for an app capped at one instance and a floor otherwise.

Wire a site in via deploy.json shared_files (spam_control/probe_guard.py -> utilities/probe_guard.py and
kumori/utilities/rate_limit.py -> utilities/rate_limit.py), then immediately after `app = Flask(__name__)`:

    from utilities.probe_guard import install as install_probe_guard
    install_probe_guard(app)
"""
import ipaddress
import logging
import re
import threading
import time

from flask import Response, request

from utilities.rate_limit import check as _bucket

logger = logging.getLogger(__name__)

RATE_LIMIT = 120          # requests per IP per window; a person reading pages makes a handful a minute
RATE_WINDOW = 60          # seconds
STRIKES_TO_BLOCK = 3      # probes before an IP is refused outright
BLOCK_SECONDS = 600
MAX_TRACKED_IPS = 20000   # rotating-IP scanners must not grow the strike table without bound

_DOT_SEGMENT = re.compile(r'(^|/)\.(?!well-known(/|$))[^/]')
_PROBE_EXT = re.compile(r'\.(php\d?|phtml|asp|aspx|jsp|cgi|pl|env|ini|conf|cfg|yml|yaml|sql|bak|old|orig|save|swp|'
                        r'log|pem|key|git-credentials)$', re.I)
_PROBE_WORDS = ('wp-admin', 'wp-login', 'wp-content', 'wp-includes', 'xmlrpc', 'phpmyadmin', 'cgi-bin',
                'vendor/phpunit', 'serviceaccount', 'aws/credentials', 'actuator/', 'server-status')
_EXEMPT_PREFIXES = ('/_ah/', '/static/')

_lock = threading.Lock()
_strikes = {}             # ip -> probe count
_blocked = {}             # ip -> unblock time


# Cloudflare's published edge ranges (cloudflare.com/ips and its /ips API agree, 15 v4 + 7 v6, fetched
# 2026-09-18). Behind the proxy every request arrives FROM one of these, so the visitor's own address is
# the CF-Connecting-IP header -- believed only when the request really came from Cloudflare, or anyone
# could forge it and dodge the limits. They change rarely; cloudflare_edge refreshes its own copy live.
_CLOUDFLARE_NETS = [ipaddress.ip_network(n) for n in (
    '173.245.48.0/20', '103.21.244.0/22', '103.22.200.0/22', '103.31.4.0/22', '141.101.64.0/18',
    '108.162.192.0/18', '190.93.240.0/20', '188.114.96.0/20', '197.234.240.0/22', '198.41.128.0/17',
    '162.158.0.0/15', '104.16.0.0/13', '104.24.0.0/14', '172.64.0.0/13', '131.0.72.0/22',
    '2400:cb00::/32', '2606:4700::/32', '2803:f800::/32', '2405:b500::/32', '2405:8100::/32',
    '2a06:98c0::/29', '2c0f:f248::/32')]


def from_cloudflare(ip):
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in net for net in _CLOUDFLARE_NETS)


def client_ip():
    # X-Appengine-User-Ip is written by Google's front end and cannot be forged by the client; the
    # first X-Forwarded-For entry can, so it is only the fallback off App Engine.
    edge = (request.headers.get('X-Appengine-User-Ip')
            or (request.headers.get('X-Forwarded-For') or '').split(',')[0].strip()
            or request.remote_addr or '')
    visitor = request.headers.get('CF-Connecting-IP')
    return visitor if visitor and from_cloudflare(edge) else edge


def is_probe(path):
    p = path.lower()
    if p.startswith(_EXEMPT_PREFIXES):
        return False
    return bool(_DOT_SEGMENT.search(p) or _PROBE_EXT.search(p) or any(w in p for w in _PROBE_WORDS))


def _prune(now):
    for ip in [ip for ip, until in _blocked.items() if until <= now]:
        del _blocked[ip]
        _strikes.pop(ip, None)
    if len(_strikes) > MAX_TRACKED_IPS:
        for ip in [ip for ip in _strikes if ip not in _blocked]:
            del _strikes[ip]


def check(path, ip, now=None):
    """(status, reason) to refuse with, or None to let the request through. `now` is wall-clock time
    for the probe/block bookkeeping; the per-IP cap runs on the token bucket's own clock."""
    now = now if now is not None else time.time()
    with _lock:
        if _blocked.get(ip, 0) > now:
            return 404, 'blocked'
        if is_probe(path):
            _strikes[ip] = _strikes.get(ip, 0) + 1
            if _strikes[ip] >= STRIKES_TO_BLOCK:
                _blocked[ip] = now + BLOCK_SECONDS
                logger.warning('probe_guard: blocking %s for %ss after %s probes (last %s)',
                               ip, BLOCK_SECONDS, _strikes[ip], path[:120])
            return 404, 'probe'
        _prune(now)
    ok, _retry = _bucket(f'probe_guard:{ip}', rate_per_sec=RATE_LIMIT / RATE_WINDOW, burst=RATE_LIMIT)
    if not ok:
        return 429, 'rate'
    return None


def reset():
    with _lock:
        _strikes.clear(); _blocked.clear()


def install(app):
    """Register the guard as the FIRST before_request hook, ahead of visitor logging and DB work."""
    def _probe_guard():
        if request.path.startswith('/_ah/') or request.headers.get('X-Appengine-Cron') == 'true':
            return None
        verdict = check(request.path, client_ip())
        if not verdict:
            return None
        status, _reason = verdict
        if status == 429:
            return Response('Too many requests\n', 429, {'Retry-After': str(RATE_WINDOW), 'Content-Type': 'text/plain'})
        return Response('Not found\n', 404, {'Content-Type': 'text/plain'})
    app.before_request_funcs.setdefault(None, []).insert(0, _probe_guard)
    return _probe_guard
