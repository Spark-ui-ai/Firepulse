"""
FirePulse server - serves the dashboard and proxies NASA FIRMS Area API requests (avoids CORS).
Usage: python server_3.py     then open http://localhost:8080/

Proxy contract (unchanged for the frontend):
  GET /firms-api/area/json/<KEY>/<SOURCE>/<west,south,east,north>/<DAYS>/<YYYY-MM-DD>
  -> JSON array of detections with numeric fields cast.

Step 1A hardening (see comments next to each constant):
  * Only /, /firepulse.html and /health are served; no other file is reachable.
  * Every path component is validated BEFORE it is placed in an upstream URL.
  * Upstream text is never echoed to clients or logs, so a MAP_KEY cannot leak that way.
  * Global cap on upstream concurrency, in-flight requests, response size and time.
  * A failed chunk fails the whole request (no silent gaps in the data).
"""
import csv
import io
import json
import os
import re
import socket
import ssl
import threading
import time
import http.server
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import date, datetime, timedelta, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get('PORT', 8080))
FIRMS_BASE = 'https://firms.modaps.eosdis.nasa.gov/api'

# ---- limits -----------------------------------------------------------------
MAX_DAY_RANGE = 5          # FIRMS Area API: max days per upstream call
MAX_REQUEST_DAYS = 10      # max days per browser request (frontend batches by 10)
MAX_WORKERS = 4            # GLOBAL cap on simultaneous upstream calls
MAX_HTTP_THREADS = 16      # cap on concurrent HTTP handler threads
MAX_INFLIGHT = 2           # proxy requests allowed to do work at once (memory guard)
ADMISSION_WAIT = 20        # seconds a request waits for a slot before getting 503
UPSTREAM_TIMEOUT = 30      # seconds, whole download of one chunk
REQUEST_DEADLINE = 45      # seconds, whole proxy request (all chunks)


def _env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


# Parsed rows cost roughly 1.2 KB of RAM each (measured locally), so keep this modest.
MAX_UPSTREAM_MB = min(8, max(1, _env_int('FIREPULSE_MAX_UPSTREAM_MB', 4)))
MAX_UPSTREAM_BYTES = MAX_UPSTREAM_MB * 1024 * 1024

ALLOWED_FORMATS = {'json'}                 # the dashboard only ever asks for json
ALLOWED_SOURCES = {'MODIS_NRT', 'VIIRS_NOAA20_NRT', 'VIIRS_NOAA21_NRT'}
DISCONTINUED_SOURCES = {'VIIRS_SNPP_NRT'}  # Suomi NPP delivery ended 2026-11-02

FLOAT_FIELDS = {'latitude', 'longitude', 'brightness', 'bright_t31',
                'bright_ti4', 'bright_ti5', 'scan', 'track', 'frp'}
# acq_time stays a string ("0412") so leading zeros survive.

# ASCII-only on purpose: \d would also match other Unicode digits.
KEY_RE = re.compile(r'[A-Za-z0-9]{1,64}')
NUM_RE = re.compile(r'-?[0-9]{1,3}(?:\.[0-9]{1,6})?')
DATE_RE = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}')
DAYS_RE = re.compile(r'[0-9]{1,4}')
MIN_DATE = date(2000, 1, 1)
# Matches the KEY segment of a FIRMS proxy path anywhere in a string.
KEY_IN_PATH_RE = re.compile(r'(firms-api/area/[^/\s]*/)[^/\s]+')


def _log(msg):
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:   # e.g. Windows console with a legacy code page
        print(msg.encode('ascii', 'replace').decode(), flush=True)


class BadRequest(Exception):
    """Client sent something invalid. Message is a fixed string we wrote."""


class UpstreamError(Exception):
    """status + a FIXED public message. Never put upstream text in here."""
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


# ---- validation ----------------------------------------------------------------
def validate_key(key):
    if not KEY_RE.fullmatch(key):
        raise BadRequest('Invalid API key format (letters and digits only, max 64 characters)')
    return key


def parse_bbox(area):
    parts = area.split(',')
    if len(parts) != 4 or not all(NUM_RE.fullmatch(p) for p in parts):
        raise BadRequest('AREA must be west,south,east,north in decimal degrees')
    west, south, east, north = (float(p) for p in parts)
    if not (-180 <= west < east <= 180) or not (-90 <= south < north <= 90):
        raise BadRequest('AREA out of range or inverted (need west<east and south<north)')
    return ','.join(parts)          # canonical, already regex-checked


def utc_today():
    return datetime.now(timezone.utc).date()


def parse_days_and_start(days_s, date_s, today=None):
    if not DAYS_RE.fullmatch(days_s):
        raise BadRequest('DAY_RANGE must be a whole number')
    days = int(days_s)
    if not 1 <= days <= MAX_REQUEST_DAYS:
        raise BadRequest(f'DAY_RANGE must be between 1 and {MAX_REQUEST_DAYS}')
    today = today or utc_today()
    if date_s is None:
        start = today - timedelta(days=days - 1)
    else:
        if not DATE_RE.fullmatch(date_s):
            raise BadRequest('DATE must be YYYY-MM-DD')
        try:
            start = date.fromisoformat(date_s)
        except ValueError:
            raise BadRequest('DATE is not a real calendar date')
    # FIRMS dates are UTC. Allow +1 day so users east of UTC (e.g. India) whose
    # local "today" is already tomorrow in UTC are not rejected.
    latest = today + timedelta(days=1)
    # Range-check `start` BEFORE any date arithmetic: 9999-12-30 + 9 days raises OverflowError.
    if start < MIN_DATE:
        raise BadRequest('DATE is too far in the past')
    if start > latest or start + timedelta(days=days - 1) > latest:
        raise BadRequest('Date range extends into the future')
    return days, start


# ---- CSV -> JSON ----------------------------------------------------------------
def cast_row(row):
    out = {}
    for k, v in row.items():
        if k is None:
            continue
        k = k.strip()
        v = (v or '').strip()
        if k in FLOAT_FIELDS:
            try:
                out[k] = float(v)
            except ValueError:
                out[k] = None
        elif k == 'confidence':
            # MODIS: 0-100 number; VIIRS: 'l' / 'n' / 'h'
            out[k] = int(v) if v.isdigit() else v
        else:
            out[k] = v
    return out


def parse_csv(text):
    """CSV text -> list of dicts. Non-CSV replies become an UpstreamError with a FIXED message."""
    text = text.strip()
    if not text:
        return []
    first = text.splitlines()[0].lower()
    if 'latitude' in first and 'longitude' in first:
        return [cast_row(r) for r in csv.DictReader(io.StringIO(text))]
    # FIRMS sends plain-text errors ("Invalid MAP_KEY.") or an HTML login page with HTTP 200.
    head = text[:2000].lower()
    if ('invalid' in head and 'key' in head) or 'earthdata' in head or 'login' in head:
        raise UpstreamError(401, 'Invalid API key (NASA FIRMS did not accept it)')
    if 'exceed' in head or 'limit' in head:
        raise UpstreamError(429, 'NASA FIRMS transaction limit reached; wait a few minutes and retry')
    raise UpstreamError(502, 'Unexpected response from NASA FIRMS')


# ---- upstream ---------------------------------------------------------------------
_SSL_CTX = ssl.create_default_context()


def classify_http_status(code):
    """Map an upstream HTTP status to (our status, fixed message). 403 is NOT assumed to be a bad key."""
    if code == 401:
        return 401, 'Invalid API key (rejected by NASA FIRMS)'
    if code == 403:
        return 403, 'NASA FIRMS refused access (permission or block - not necessarily a bad key)'
    if code == 429:
        return 429, 'NASA FIRMS rate limit reached; wait a few minutes and retry'
    if 400 <= code < 500:
        return 502, 'NASA FIRMS rejected the request'
    return 502, 'NASA FIRMS is unavailable or returned an error'


def fetch_upstream(url):
    """Download one chunk as text. The URL contains the MAP_KEY: never log or echo it or its errors."""
    req = urllib.request.Request(url, headers={'User-Agent': 'FirePulse/1.0'})
    deadline = time.monotonic() + UPSTREAM_TIMEOUT
    try:
        with urllib.request.urlopen(req, context=_SSL_CTX, timeout=UPSTREAM_TIMEOUT) as resp:
            buf = bytearray()
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise UpstreamError(504, 'NASA FIRMS took too long to respond')
                # Tighten the socket timeout on every read; urllib's default is an
                # inactivity timeout and can otherwise permit a slow trickle.
                sock = getattr(getattr(getattr(resp, 'fp', None), 'raw', None), '_sock', None)
                if sock is not None:
                    sock.settimeout(remaining)
                piece = resp.read(65536)
                if not piece:
                    break
                buf.extend(piece)
                if len(buf) > MAX_UPSTREAM_BYTES:
                    raise UpstreamError(413, 'NASA FIRMS response too large; choose a smaller area or shorter date range')
            return bytes(buf).decode('utf-8', errors='replace')
    except UpstreamError:
        raise
    except urllib.error.HTTPError as e:     # must precede URLError (it is a subclass)
        status, message = classify_http_status(e.code)
        e.close()                           # body is deliberately never read or echoed
        raise UpstreamError(status, message)
    except (socket.timeout, TimeoutError):
        raise UpstreamError(504, 'NASA FIRMS took too long to respond')
    except urllib.error.URLError as e:
        if isinstance(e.reason, (socket.timeout, TimeoutError)):
            raise UpstreamError(504, 'NASA FIRMS took too long to respond')
        raise UpstreamError(502, 'Could not reach NASA FIRMS')
    except Exception as e:
        # Exception text can contain the URL (and key). Log the type only.
        _log(f'  [UPSTREAM] unexpected {type(e).__name__}')
        raise UpstreamError(502, 'Unexpected error contacting NASA FIRMS')


def split_range(start, days):
    """[(chunk_start, chunk_days), ...] with chunk_days <= MAX_DAY_RANGE."""
    chunks, offset = [], 0
    while offset < days:
        n = min(MAX_DAY_RANGE, days - offset)
        chunks.append((start + timedelta(days=offset), n))
        offset += n
    return chunks


# One shared pool for the whole process = a GLOBAL cap on upstream concurrency.
_EXECUTOR = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix='firms')
# Limits how many requests may hold parsed rows in memory at once.
_ADMISSION = threading.BoundedSemaphore(MAX_INFLIGHT)


def fetch_area(key, source, area, days, start):
    """All chunks or an UpstreamError - never a silently incomplete result."""
    urls = [f'{FIRMS_BASE}/area/csv/{key}/{source}/{area}/{n}/{d.isoformat()}'
            for d, n in split_range(start, days)]

    def run(url):
        return parse_csv(fetch_upstream(url))

    futures = [_EXECUTOR.submit(run, u) for u in urls]
    deadline = time.monotonic() + REQUEST_DEADLINE
    rows = []
    try:
        for f in futures:
            rows.extend(f.result(timeout=max(0.0, deadline - time.monotonic())))
    except FutureTimeout:
        raise UpstreamError(504, 'NASA FIRMS took too long to respond')
    finally:
        for f in futures:
            f.cancel()          # drops chunks still queued; running ones end at their own deadline
    return rows


# ---- HTTP handler -----------------------------------------------------------------
class FirePulseHandler(http.server.BaseHTTPRequestHandler):
    # BaseHTTPRequestHandler (not SimpleHTTPRequestHandler): there is no generic file
    # serving to defend, so only the routes below can ever return anything.
    server_version = 'FirePulse'
    sys_version = ''
    timeout = 30                    # drop clients that stall mid-request

    def do_GET(self):
        self._dispatch(send_body=True)

    def do_HEAD(self):              # uptime monitors often use HEAD
        self._dispatch(send_body=False)

    def _dispatch(self, send_body):
        path = self.path.split('?', 1)[0].split('#', 1)[0]
        if path == '/health':
            return self._send(200, b'ok', 'text/plain; charset=utf-8', send_body)
        if path in ('/', '/firepulse.html'):
            return self._serve_dashboard(send_body)
        if path.startswith('/firms-api/'):
            if not send_body:
                return self._send_text(405, 'Method not allowed', send_body)
            return self.proxy_firms(path)
        return self._send_text(404, 'Not found', send_body)

    def _serve_dashboard(self, send_body):
        try:
            with open(os.path.join(BASE_DIR, 'firepulse.html'), 'rb') as fh:
                body = fh.read()
        except OSError:
            return self._send_text(500, 'Dashboard file missing', send_body)
        self._send(200, body, 'text/html; charset=utf-8', send_body, {'Cache-Control': 'no-cache'})

    def _send(self, status, body, ctype, send_body=True, extra=None):
        self.send_response(status)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('X-Content-Type-Options', 'nosniff')
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if send_body:
            self.wfile.write(body)

    def _send_text(self, status, text, send_body=True, extra=None):
        self._send(status, text.encode('utf-8'), 'text/plain; charset=utf-8', send_body, extra)

    def _send_json(self, payload, extra=None):
        self._send(200, json.dumps(payload).encode('utf-8'), 'application/json', True,
                   dict({'Cache-Control': 'no-store'}, **(extra or {})))

    def proxy_firms(self, path):
        # area/<fmt>/<KEY>/<SOURCE>/<AREA>/<DAYS>[/<DATE>]
        parts = path[len('/firms-api/'):].strip('/').split('/')
        if len(parts) not in (6, 7) or parts[0] != 'area':
            return self._send_text(400, 'Expected /firms-api/area/json/<KEY>/<SOURCE>/<AREA>/<DAYS>/<DATE>')
        _, fmt, key, source, area, days_s = parts[:6]
        date_s = parts[6] if len(parts) == 7 else None

        try:
            if fmt not in ALLOWED_FORMATS:
                raise BadRequest('Only the json format is supported')
            validate_key(key)
            if source not in ALLOWED_SOURCES and source not in DISCONTINUED_SOURCES:
                raise BadRequest('Unsupported sensor')
            area = parse_bbox(area)
            days, start = parse_days_and_start(days_s, date_s)
        except BadRequest as e:
            return self._send_text(400, str(e))

        if source in DISCONTINUED_SOURCES:
            _log(f'  [PROXY] {source} is discontinued - returning []')
            return self._send_json([], {'X-FirePulse-Warning': f'{source} discontinued'})

        if not _ADMISSION.acquire(timeout=ADMISSION_WAIT):
            return self._send_text(503, 'Server busy; retry in a few seconds', True, {'Retry-After': '5'})
        try:
            rows = fetch_area(key, source, area, days, start)
        except UpstreamError as e:
            _log(f'  [PROXY] FAIL {e.status} ({source}, {days}d from {start})')
            return self._send_text(e.status, e.message)
        except Exception as e:      # bug safety net; type only - the message could hold the URL
            _log(f'  [PROXY] internal error {type(e).__name__}')
            return self._send_text(500, 'Internal server error')
        finally:
            _ADMISSION.release()

        _log(f'  [PROXY] OK {len(rows)} hotspots ({source}, {days}d from {start})')
        self._send_json(rows)

    def log_message(self, format, *args):
        # Request lines contain the MAP_KEY for proxy calls; redact it everywhere it can appear.
        _log('  [HTTP] ' + KEY_IN_PATH_RE.sub(r'\1<redacted>', format % args))


class BoundedThreadingHTTPServer(http.server.ThreadingHTTPServer):
    """Thread-per-request server with a hard cap on live handler threads."""
    daemon_threads = True
    block_on_close = False
    request_queue_size = 32

    def __init__(self, *args, **kwargs):
        self._request_slots = threading.BoundedSemaphore(MAX_HTTP_THREADS)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        if not self._request_slots.acquire(blocking=False):
            try:
                request.sendall(b'HTTP/1.1 503 Service Unavailable\r\nConnection: close\r\nRetry-After: 5\r\nContent-Length: 0\r\n\r\n')
            except OSError:
                pass
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._request_slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._request_slots.release()

def main():
    _log(f"""
+-------------------------------------------+
|          FirePulse Server                 |
|{('  Open: http://localhost:' + str(PORT) + '/').ljust(43)}|
|  Press Ctrl+C to stop                     |
+-------------------------------------------+
""")
    with BoundedThreadingHTTPServer(('', PORT), FirePulseHandler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            _log('\nServer stopped.')


if __name__ == '__main__':
    main()
