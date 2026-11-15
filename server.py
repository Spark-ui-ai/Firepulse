"""
FirePulse local server — serves the app and proxies NASA FIRMS API requests to avoid CORS issues.
Usage: python server.py
Then open http://localhost:8080/firepulse.html

Proxy behaviour for /firms-api/area/<json|csv>/<KEY>/<SOURCE>/<AREA>/<DAYS>/<DATE>:
  - Always queries FIRMS upstream as CSV (the Area API does not serve JSON).
  - Parses the CSV and returns a JSON array of objects with numeric fields cast.
  - Splits DAY_RANGE > 5 into 5-day chunks (fetched in parallel) and merges them.
  - Rejects discontinued sources (VIIRS_SNPP_NRT) with an empty result.
"""
import csv
import io
import json
import os
import ssl
import http.server
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

PORT = int(os.environ.get('PORT', 8080))
FIRMS_BASE = 'https://firms.modaps.eosdis.nasa.gov/api'
MAX_DAY_RANGE = 5

ALLOWED_SOURCES = {'MODIS_NRT', 'VIIRS_NOAA20_NRT', 'VIIRS_NOAA21_NRT'}
DISCONTINUED_SOURCES = {'VIIRS_SNPP_NRT'}  # Suomi NPP stopped delivering data (Nov 2026)

FLOAT_FIELDS = {'latitude', 'longitude', 'brightness', 'bright_t31',
                'bright_ti4', 'bright_ti5', 'scan', 'track', 'frp'}
# acq_time stays a string ("0412") so leading zeros survive.


class UpstreamError(Exception):
    def __init__(self, status, body):
        super().__init__(body)
        self.status = status
        self.body = body


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
            # MODIS: 0–100 number; VIIRS: 'l' / 'n' / 'h'
            out[k] = int(v) if v.isdigit() else v
        else:
            out[k] = v
    return out


def parse_csv(text):
    text = text.strip()
    if not text:
        return []
    header = text.splitlines()[0].lower()
    if 'latitude' not in header or 'longitude' not in header:
        # FIRMS returns plain-text errors (e.g. "Invalid MAP_KEY.") or HTML with 200
        raise UpstreamError(400, text[:500])
    return [cast_row(r) for r in csv.DictReader(io.StringIO(text))]


def fetch_upstream(url):
    # Do not log this URL: the FIRMS MAP_KEY is embedded in its path.
    ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers={'User-Agent': 'FirePulse/1.0'})
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=60) as resp:
            return resp.read().decode('utf-8', errors='replace')
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='replace')
        raise UpstreamError(e.code, body)
    except Exception as e:
        raise UpstreamError(502, f'Proxy error: {e}')


def split_range(start, days):
    """[(chunk_start, chunk_days), ...] with chunk_days <= MAX_DAY_RANGE."""
    chunks = []
    offset = 0
    while offset < days:
        n = min(MAX_DAY_RANGE, days - offset)
        chunks.append((start + timedelta(days=offset), n))
        offset += n
    return chunks


def fetch_area(key, source, area, days, start):
    chunks = split_range(start, days)
    urls = [f'{FIRMS_BASE}/area/csv/{key}/{source}/{area}/{n}/{d.isoformat()}'
            for d, n in chunks]

    def run(url):
        try:
            return parse_csv(fetch_upstream(url)), None
        except UpstreamError as e:
            return None, e

    with ThreadPoolExecutor(max_workers=len(urls)) as pool:
        results = list(pool.map(run, urls))

    rows, errors = [], []
    for data, err in results:
        if err:
            errors.append(err)
        else:
            rows.extend(data)

    if errors and len(errors) == len(results):
        raise errors[0]  # every chunk failed → surface the upstream error
    return rows, errors


class FirePulseHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/health':
            return self.send_text(200, 'ok')
        if self.path == '/':
            self.path = '/firepulse.html'
        if self.path.startswith('/firms-api/'):
            self.proxy_firms()
        else:
            super().do_GET()

    def send_text(self, status, text):
        self.send_response(status)
        self.send_header('Content-Type', 'text/plain; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(text.encode())

    def send_json(self, payload, extra_headers=None):
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Content-Length', str(len(body)))
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def proxy_firms(self):
        parts = self.path.split('?', 1)[0].replace('/firms-api/', '', 1).strip('/').split('/')

        # area/<fmt>/<KEY>/<SOURCE>/<AREA>/<DAYS>[/<DATE>]
        if len(parts) not in (6, 7) or parts[0] != 'area':
            return self.send_text(400, 'Expected /firms-api/area/<fmt>/<KEY>/<SOURCE>/<AREA>/<DAYS>/<DATE>')

        _, _fmt, key, source, area, days_s = parts[:6]
        date_s = parts[6] if len(parts) == 7 else None

        if source in DISCONTINUED_SOURCES:
            print(f"  [PROXY] ⚠ {source} is discontinued — returning []")
            return self.send_json([], {'X-FirePulse-Warning': f'{source} discontinued'})
        if source not in ALLOWED_SOURCES:
            return self.send_text(400, f'Unsupported source: {source}')

        try:
            days = int(days_s)
            if days < 1:
                raise ValueError
            start = date.fromisoformat(date_s) if date_s else date.today() - timedelta(days=days - 1)
        except ValueError:
            return self.send_text(400, 'Invalid DAY_RANGE or DATE (expected positive int and YYYY-MM-DD)')

        try:
            rows, errors = fetch_area(key, source, area, days, start)
        except UpstreamError as e:
            print(f"  [PROXY] ✗ HTTP {e.status}: {e.body[:200]}")
            return self.send_text(e.status, e.body)

        headers = {}
        if errors:
            headers['X-FirePulse-Partial'] = f'{len(errors)} chunk(s) failed'
            print(f"  [PROXY] ⚠ partial: {len(errors)} chunk(s) failed")
        print(f"  [PROXY] ✓ {len(rows)} hotspots ({source}, {days}d from {start})")
        self.send_json(rows, headers)

    def log_message(self, format, *args):
        msg = format % args
        if '/firms-api/' in msg:
            # Request paths contain the user's NASA FIRMS MAP_KEY; keep logs secret-free.
            print('  [FIRMS] API request proxied')
        elif '404' not in msg:
            print(f"  [FILE]  {msg}")


if __name__ == '__main__':
    print(f"""
╔═══════════════════════════════════════════╗
║         FirePulse Local Server            ║
╠═══════════════════════════════════════════╣
║{('  Open: http://localhost:' + str(PORT) + '/').ljust(43)}║
║  Press Ctrl+C to stop                     ║
╚═══════════════════════════════════════════╝
    """)

    # Threaded so parallel chunk requests / multiple browser fetches don't block each other
    with http.server.ThreadingHTTPServer(('', PORT), FirePulseHandler) as server:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nServer stopped.")