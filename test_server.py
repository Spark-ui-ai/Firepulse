"""
Regression tests for FirePulse server_3.py (Step 1A). Standard library only; no NASA key needed.
Run:  python -m unittest -v test_server
All upstream traffic is mocked. Nothing here touches the network.
"""
import http.client
import http.server
import io
import re
import socket
import threading
import time
import unittest
import urllib.error
from datetime import date, timedelta
from unittest import mock

import server_3 as server

KEY = 'SENTINELKEY0123456789abcdef0123'      # distinctive, so any leak is detectable
BBOX = '95,-44,155,-10'                       # Australia, corrected order
CSV = ('latitude,longitude,brightness,scan,track,acq_date,acq_time,satellite,confidence,'
       'version,bright_t31,frp,daynight\n'
       '10.1,20.2,330.5,1.0,1.0,2026-11-10,0412,N,n,2.0NRT,300.1,5.5,D\n')


def day(offset=-3):
    return (server.utc_today() + timedelta(days=offset)).isoformat()


def proxy_path(key=KEY, source='MODIS_NRT', area=BBOX, days=5, when=None, fmt='json'):
    # MAP_KEY is supplied separately as X-FIRMS-MAP-KEY by get().
    return f'/firms-api/area/{fmt}/{source}/{area}/{days}/{when or day(-7)}'


class ServerTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = http.server.ThreadingHTTPServer(('127.0.0.1', 0), server.FirePulseHandler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def setUp(self):
        self.logs = []
        p = mock.patch.object(server, '_log', self.logs.append)   # list.append is thread-safe
        p.start()
        self.addCleanup(p.stop)

    # -- helpers --
    def get(self, path, method='GET', headers=None):
        c = http.client.HTTPConnection('127.0.0.1', self.port, timeout=20)
        try:
            if headers is None:
                headers = {'X-FIRMS-MAP-KEY': KEY} if path.startswith('/firms-api/') else {}
            c.request(method, path, headers=headers)
            r = c.getresponse()
            return r.status, dict(r.getheaders()), r.read()
        finally:
            c.close()

    def raw(self, path_bytes):
        """Send bytes exactly as given (http.client refuses malformed paths)."""
        s = socket.create_connection(('127.0.0.1', self.port), timeout=15)
        s.sendall(b'GET ' + path_bytes + b' HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n')
        data = b''
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            data += chunk
        s.close()
        head, _, body = data.partition(b'\r\n\r\n')
        return int(head.split()[1]), body

    def upstream_ok(self):
        calls = []
        def fake(url):
            calls.append(url)
            return CSV
        return mock.patch.object(server, 'fetch_upstream', fake), calls

    def assertNoKeyLeak(self, *bodies):
        for b in bodies:
            self.assertNotIn(KEY.encode(), b if isinstance(b, bytes) else b.encode())
        self.assertFalse([l for l in self.logs if KEY in l], 'key found in logs: %r' % self.logs)


class TestRoutesAndStatic(ServerTestCase):
    def test_health(self):
        s, h, b = self.get('/health')
        self.assertEqual((s, b), (200, b'ok'))

    def test_root_and_dashboard_served(self):
        for p in ('/', '/firepulse.html', '/?utm_source=x'):
            s, h, b = self.get(p)
            self.assertEqual(s, 200, p)
            self.assertIn(b'FirePulse', b)
            self.assertTrue(h['Content-Type'].startswith('text/html'))

    def test_head_works_without_body(self):
        s, h, b = self.get('/', 'HEAD')
        self.assertEqual((s, b), (200, b''))
        s, h, b = self.get('/health', 'HEAD')
        self.assertEqual(s, 200)

    def test_unapproved_paths_are_404(self):
        for p in ('/server.py', '/render.yaml', '/README.md', '/SUBMISSION.md', '/requirements.txt',
                  '/.gitignore', '/.git/HEAD', '/test_server.py', '/../server.py', '/%2e%2e/server.py',
                  '/firepulse.html/', '/index.html', '/static/', '/firms-api', '/FIREPULSE.HTML'):
            s, h, b = self.get(p)
            self.assertEqual(s, 404, p)

    def test_head_on_proxy_not_allowed(self):
        s, h, b = self.get(proxy_path(), 'HEAD')
        self.assertEqual(s, 405)
        self.assertEqual(b, b'')

    def test_http_thread_admission_rejects_overload(self):
        httpd = server.BoundedThreadingHTTPServer(('127.0.0.1', 0), server.FirePulseHandler)
        httpd._request_slots = threading.BoundedSemaphore(0)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            conn = http.client.HTTPConnection('127.0.0.1', httpd.server_address[1], timeout=5)
            conn.request('GET', '/health')
            response = conn.getresponse()
            self.assertEqual(response.status, 503)
            self.assertEqual(response.getheader('Retry-After'), '5')
            response.read()
            conn.close()
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(5)


class TestHeaderAuthentication(ServerTestCase):
    def test_missing_key_header_is_rejected(self):
        s, h, b = self.get(proxy_path(), headers={})
        self.assertEqual(s, 400)
        self.assertIn(b'Missing NASA FIRMS API key header', b)
        self.assertNotIn(KEY.encode(), b)

    def test_invalid_key_header_is_rejected_without_leak(self):
        for key in ('', 'bad key', 'a' * 65, 'ab.cd'):
            with self.subTest(key=key):
                s, h, b = self.get(proxy_path(), headers={'X-FIRMS-MAP-KEY': key})
                self.assertEqual(s, 400)
                if key:
                    self.assertNotIn(key.encode(), b)
                self.assertNoKeyLeak(b)

    def test_valid_key_header_reaches_upstream_but_not_url_or_logs(self):
        patcher, calls = self.upstream_ok()
        with patcher:
            s, h, b = self.get(proxy_path())
        self.assertEqual(s, 200)
        self.assertEqual(len(calls), 1)
        self.assertNotIn(KEY, proxy_path())
        self.assertIn('/' + KEY + '/MODIS_NRT/', calls[0])
        self.assertNoKeyLeak(b)
        self.assertFalse(any(KEY in line for line in self.logs))

    def test_legacy_key_in_path_is_rejected(self):
        legacy = f'/firms-api/area/json/{KEY}/MODIS_NRT/{BBOX}/5/{day(-7)}'
        s, h, b = self.get(legacy, headers={})
        self.assertEqual(s, 400)
        self.assertNotIn(KEY.encode(), b)
        self.assertFalse(any(KEY in line for line in self.logs))


class TestValidators(unittest.TestCase):
    def test_key_validator(self):
        for good in ('a', 'A1' * 16, 'x' * 64):
            self.assertEqual(server.validate_key(good), good)
        for bad in ('', 'x' * 65, 'abc def', 'abc;def', 'abc%20def', 'k\u00e9y', '\u0661\u0662\u0663',
                    'abc\x01def', 'abc\n', '../x', 'a.b', 'a-b', "a'b", '\ud83d\udd25'):
            with self.assertRaises(server.BadRequest, msg=repr(bad)):
                server.validate_key(bad)

    def test_bbox_validator(self):
        self.assertEqual(server.parse_bbox('95,-44,155,-10'), '95,-44,155,-10')
        self.assertEqual(server.parse_bbox('-125.123,24,-66,49.5'), '-125.123,24,-66,49.5')
        for bad in ('95,-10,155,-44',            # south > north (the old Australia preset)
                    '10,0,10,5', '0,5,10,5',     # zero width / height
                    '-181,0,10,5', '0,0,181,5', '0,-91,10,5', '0,0,10,91',
                    '', 'abc', '1,2,3', '1,2,3,4,5', '..', 'nan,0,1,1', 'inf,0,1,1', '1e2,0,200,5',
                    '0,0,1,+1', ' 0,0,1,1', '0,0,1,1 ', '1.,0,2,1', '1,0,2,1\n'):
            with self.assertRaises(server.BadRequest, msg=repr(bad)):
                server.parse_bbox(bad)

    def test_date_and_days_validator(self):
        today = date(2026, 11, 15)
        days, start = server.parse_days_and_start('5', '2026-11-10', today)
        self.assertEqual((days, start), (5, date(2026, 11, 10)))
        # +1 day tolerance for users east of UTC
        self.assertEqual(server.parse_days_and_start('1', '2026-11-16', today)[1], date(2026, 11, 16))
        self.assertEqual(server.parse_days_and_start('3', None, today)[1], date(2026, 11, 13))
        bad_cases = [('0', '2026-11-10'), ('11', '2026-11-10'), ('1500', '2026-11-10'), ('-1', '2026-11-10'),
                     ('abc', '2026-11-10'), ('5.0', '2026-11-10'), ('\u0665', '2026-11-10'),
                     ('5', '2026-13-45'), ('5', '2026-02-30'), ('5', 'abc'), ('5', '2026-1-1'),
                     ('5', '20261110'), ('5', '2026-11-10T00:00'), ('5', '0000-01-01'),
                     ('1', '2026-11-17'),                      # future (beyond +1 tolerance)
                     ('5', '2026-11-14'),                      # range ends in the future
                     ('5', '2030-01-01'), ('5', '9999-12-30'), ('5', '1999-12-31')]
        for d, s in bad_cases:
            with self.assertRaises(server.BadRequest, msg=(d, s)):
                server.parse_days_and_start(d, s, today)

    def test_split_range(self):
        c = server.split_range(date(2026, 11, 1), 10)
        self.assertEqual([(d.isoformat(), n) for d, n in c], [('2026-11-01', 5), ('2026-11-06', 5)])
        c = server.split_range(date(2026, 11, 1), 7)
        self.assertEqual([n for _, n in c], [5, 2])


class TestProxyValidationOverHttp(ServerTestCase):
    def test_valid_request_returns_frontend_contract(self):
        patch, calls = self.upstream_ok()
        with patch:
            s, h, b = self.get(proxy_path())
        self.assertEqual(s, 200)
        import json
        rows = json.loads(b)
        self.assertEqual(len(rows), 1)
        r = rows[0]
        for field in ('latitude', 'longitude', 'frp', 'confidence', 'acq_date', 'acq_time'):
            self.assertIn(field, r)
        self.assertIsInstance(r['frp'], float)
        self.assertEqual(r['acq_time'], '0412')        # leading zero preserved
        self.assertEqual(r['confidence'], 'n')
        self.assertEqual(len(calls), 1)
        self.assertIn('/area/csv/', calls[0])          # upstream is always CSV
        self.assertNoKeyLeak(b)

    def test_invalid_inputs_rejected_before_any_upstream_call(self):
        patch, calls = self.upstream_ok()
        with patch:
            cases = {
                'csv format': proxy_path(fmt='csv'),
                'xml format': proxy_path(fmt='xml'),
                'bad sensor': proxy_path(source='BOGUS'),
                'inverted bbox': proxy_path(area='95,-10,155,-44'),
                'garbage bbox': proxy_path(area='abc'),
                'bad date': proxy_path(when='2026-13-45'),
                'future date': proxy_path(when='2030-01-01'),
                'days 11': proxy_path(days=11),
                'days 1500': proxy_path(days=1500),
                'days 0': proxy_path(days=0),
                'overflow date': proxy_path(when='9999-12-30', days=10),
                'too few parts': '/firms-api/area/json/KEY/MODIS_NRT',
                'wrong root': '/firms-api/nope/json/MODIS_NRT/1,2,3,4/5/2026-11-10',
                'empty segment': f'/firms-api/area/json//MODIS_NRT/{BBOX}/5/{day(-7)}',
            }
            for name, path in cases.items():
                s, h, b = self.get(path)
                self.assertEqual(s, 400, f'{name}: got {s} {b!r}')
        self.assertEqual(calls, [], 'upstream must never be called for invalid input')

    def test_unicode_and_control_char_keys_rejected_safely(self):
        for key in ('K\\u00c9Y12345', 'abc\\x01def'):
            with self.subTest(key=repr(key)):
                with self.assertRaises(server.BadRequest):
                    server.validate_key(key)

    def test_discontinued_sensor_returns_empty_with_warning(self):
        patch, calls = self.upstream_ok()
        with patch:
            s, h, b = self.get(proxy_path(source='VIIRS_SNPP_NRT'))
        self.assertEqual((s, b), (200, b'[]'))
        self.assertIn('X-FirePulse-Warning', h)
        self.assertEqual(calls, [])

    def test_future_tolerance_one_day_accepted(self):
        patch, calls = self.upstream_ok()
        with patch:
            s, h, b = self.get(proxy_path(days=1, when=day(+1)))
        self.assertEqual(s, 200)


class TestUpstreamErrorHandling(ServerTestCase):
    def fail_with(self, exc):
        def fake(url):
            raise exc
        return mock.patch.object(server, 'fetch_upstream', fake)

    def test_http_status_classification(self):
        self.assertEqual(server.classify_http_status(401)[0], 401)
        self.assertIn('Invalid API key', server.classify_http_status(401)[1])
        s, m = server.classify_http_status(403)
        self.assertEqual(s, 403)
        self.assertNotIn('Invalid API key', m)         # 403 must NOT be called a bad key
        self.assertEqual(server.classify_http_status(429)[0], 429)
        self.assertEqual(server.classify_http_status(404)[0], 502)
        self.assertEqual(server.classify_http_status(500)[0], 502)
        self.assertEqual(server.classify_http_status(503)[0], 502)

    def test_real_fetch_maps_http_errors_and_never_echoes_body(self):
        for code, want in ((401, 401), (403, 403), (429, 429), (500, 502)):
            def fake_urlopen(req, context=None, timeout=None, code=code):
                raise urllib.error.HTTPError(req.full_url, code, 'x', {}, io.BytesIO(('body ' + KEY).encode()))
            with mock.patch.object(server.urllib.request, 'urlopen', fake_urlopen):
                with self.assertRaises(server.UpstreamError) as cm:
                    server.fetch_upstream(f'https://example.invalid/{KEY}')
            self.assertEqual(cm.exception.status, want)
            self.assertNotIn(KEY, cm.exception.message)

    def test_200_text_bodies_classified(self):
        for body, status in (('Invalid MAP_KEY.', 401),
                             ('<html><body>Earthdata Login</body></html>', 401),
                             ('You have exceeded your transaction limit', 429),
                             ('something odd ' + KEY, 502)):
            with self.assertRaises(server.UpstreamError, msg=body) as cm:
                server.parse_csv(body)
            self.assertEqual(cm.exception.status, status, body)
            self.assertNotIn(KEY, cm.exception.message)
        self.assertEqual(server.parse_csv(''), [])

    def test_errors_over_http_keep_frontend_key_phrase_and_hide_key(self):
        with self.fail_with(server.UpstreamError(401, 'Invalid API key (rejected by NASA FIRMS)')):
            s, h, b = self.get(proxy_path())
        self.assertEqual(s, 401)
        self.assertIn(b'Invalid API key', b)           # firepulse.html looks for this exact phrase
        self.assertNoKeyLeak(b)
        with self.fail_with(server.UpstreamError(403, server.classify_http_status(403)[1])):
            s, h, b = self.get(proxy_path())
        self.assertEqual(s, 403)
        self.assertNotIn(b'Invalid API key', b)

    def test_unexpected_exception_text_never_leaks_key(self):
        with self.fail_with(RuntimeError(f'boom https://x/{KEY}/MODIS')):
            s, h, b = self.get(proxy_path())
        self.assertEqual(s, 500)
        self.assertNoKeyLeak(b)

    def test_urllib_exception_containing_url_never_leaks_key(self):
        def fake_urlopen(req, context=None, timeout=None):
            raise http.client.InvalidURL(f"URL can't contain control characters. {req.full_url!r}")
        with mock.patch.object(server.urllib.request, 'urlopen', fake_urlopen):
            s, h, b = self.get(proxy_path())
        self.assertEqual(s, 502)
        self.assertNoKeyLeak(b)

    def test_timeouts(self):
        for exc in (socket.timeout('timed out'), TimeoutError('t'), urllib.error.URLError(socket.timeout('t'))):
            def fake_urlopen(req, context=None, timeout=None, exc=exc):
                raise exc
            with mock.patch.object(server.urllib.request, 'urlopen', fake_urlopen):
                with self.assertRaises(server.UpstreamError) as cm:
                    server.fetch_upstream('https://example.invalid/x')
            self.assertEqual(cm.exception.status, 504)

    def test_request_deadline_returns_504(self):
        def slow(url):
            time.sleep(1.0)
            return CSV
        with mock.patch.object(server, 'fetch_upstream', slow), mock.patch.object(server, 'REQUEST_DEADLINE', 0.2):
            t0 = time.monotonic()
            s, h, b = self.get(proxy_path())
        self.assertEqual(s, 504)
        self.assertLess(time.monotonic() - t0, 0.9)

    def test_oversized_upstream_response_stops_reading(self):
        class Endless:
            total = 0
            def read(self, n=-1):
                n = 65536 if n is None or n < 0 else n
                Endless.total += n
                return b'a' * n
            def __enter__(self): return self
            def __exit__(self, *a): return False
        with mock.patch.object(server.urllib.request, 'urlopen', lambda *a, **k: Endless()), \
                mock.patch.object(server, 'MAX_UPSTREAM_BYTES', 200_000):
            with self.assertRaises(server.UpstreamError) as cm:
                server.fetch_upstream('https://example.invalid/x')
        self.assertEqual(cm.exception.status, 413)
        self.assertLessEqual(Endless.total, 200_000 + 65536)

    def test_slow_download_hits_total_deadline(self):
        class Drip:
            def read(self, n=-1):
                time.sleep(0.05)
                return b'a'
            def __enter__(self): return self
            def __exit__(self, *a): return False
        with mock.patch.object(server.urllib.request, 'urlopen', lambda *a, **k: Drip()), \
                mock.patch.object(server, 'UPSTREAM_TIMEOUT', 0.3):
            with self.assertRaises(server.UpstreamError) as cm:
                server.fetch_upstream('https://example.invalid/x')
        self.assertEqual(cm.exception.status, 504)


class TestPartialFailureAndConcurrency(ServerTestCase):
    def test_one_failed_chunk_fails_whole_request_deterministically(self):
        first = day(-9)
        second = (date.fromisoformat(first) + timedelta(days=5)).isoformat()
        def by_chunk(url):                  # keyed on the chunk's date, not on call order
            if f'/{second}' in url:
                raise server.UpstreamError(502, 'NASA FIRMS is unavailable or returned an error')
            return CSV
        for _ in range(5):                  # repeat: result must never vary
            with mock.patch.object(server, 'fetch_upstream', by_chunk):
                s, h, b = self.get(proxy_path(days=10, when=first))
            self.assertEqual(s, 502)
            self.assertNotIn(b'latitude', b)

    def test_ten_day_request_makes_two_five_day_calls_and_merges(self):
        patch, calls = self.upstream_ok()
        with patch:
            s, h, b = self.get(proxy_path(days=10, when=day(-12)))
        self.assertEqual(s, 200)
        self.assertEqual(len(calls), 2)
        self.assertEqual([c.split('/')[-2] for c in sorted(calls)], ['5', '5'])
        import json
        self.assertEqual(len(json.loads(b)), 2)

    def test_global_upstream_concurrency_never_exceeds_worker_cap(self):
        lock = threading.Lock()
        state = {'active': 0, 'peak': 0, 'calls': 0}
        def fake(url):
            with lock:
                state['active'] += 1
                state['calls'] += 1
                state['peak'] = max(state['peak'], state['active'])
            time.sleep(0.1)
            with lock:
                state['active'] -= 1
            return CSV
        results = []
        def one():
            results.append(self.get(proxy_path(days=10, when=day(-12)))[0])
        with mock.patch.object(server, 'fetch_upstream', fake), \
                mock.patch.object(server, '_ADMISSION', threading.BoundedSemaphore(10)):
            threads = [threading.Thread(target=one) for _ in range(6)]   # 12 chunks in total
            for t in threads: t.start()
            for t in threads: t.join(30)
        self.assertEqual(results, [200] * 6)
        self.assertEqual(state['calls'], 12)
        self.assertLessEqual(state['peak'], server.MAX_WORKERS)

    def test_admission_control_returns_503_when_busy(self):
        gate, started = threading.Event(), threading.Event()
        def blocking(url):
            started.set()
            gate.wait(10)
            return CSV
        out = {}
        with mock.patch.object(server, 'fetch_upstream', blocking), \
                mock.patch.object(server, '_ADMISSION', threading.BoundedSemaphore(1)), \
                mock.patch.object(server, 'ADMISSION_WAIT', 0.2):
            t = threading.Thread(target=lambda: out.update(a=self.get(proxy_path(days=1))))
            t.start()
            self.assertTrue(started.wait(5))
            s, h, b = self.get(proxy_path(days=1))
            self.assertEqual(s, 503)
            self.assertEqual(h.get('Retry-After'), '5')
            gate.set()
            t.join(10)
        self.assertEqual(out['a'][0], 200)

    def test_admission_slot_released_after_failure(self):
        sem = threading.BoundedSemaphore(1)
        def boom(url):
            raise server.UpstreamError(502, 'x')
        with mock.patch.object(server, 'fetch_upstream', boom), mock.patch.object(server, '_ADMISSION', sem):
            for _ in range(3):
                self.assertEqual(self.get(proxy_path(days=1))[0], 502)
        self.assertTrue(sem.acquire(blocking=False), 'slot leaked after failed requests')


class TestLogging(ServerTestCase):
    def test_key_never_appears_in_logs_across_scenarios(self):
        patch, _ = self.upstream_ok()
        with patch:
            self.get(proxy_path())
            self.get(proxy_path(area='bad'))
            self.get(proxy_path(source='VIIRS_SNPP_NRT'))
            self.get(f'//firms-api/area/json/{KEY}/MODIS_NRT/{BBOX}/5/{day(-7)}', headers={})   # malformed legacy URL, still redacted
        def boom(url):
            raise server.UpstreamError(401, 'Invalid API key (rejected by NASA FIRMS)')
        with mock.patch.object(server, 'fetch_upstream', boom):
            self.get(proxy_path())
        joined = '\n'.join(self.logs)
        self.assertGreater(len(self.logs), 0)
        self.assertNotIn(KEY, joined)
        self.assertIn('<redacted>', joined)

    def test_redaction_handles_short_keys(self):
        out = server.KEY_IN_PATH_RE.sub(r'\1<redacted>', 'GET /firms-api/area/json/a/MODIS_NRT/1,2,3,4/5/2026-11-10')
        self.assertNotIn('/a/', out)


class TestFrontendCompatibility(unittest.TestCase):
    """Reads the patched firepulse.html next to server_3.py and checks the contract both ways."""
    @classmethod
    def setUpClass(cls):
        with open(server.BASE_DIR + '/firepulse.html', encoding='utf-8') as fh:
            cls.html = fh.read()

    def test_every_preset_passes_backend_bbox_validation(self):
        presets = re.findall(r'setPreset\((-?\d+),(-?\d+),(-?\d+),(-?\d+)\)', self.html)
        self.assertEqual(len(presets), 5)
        for w, s, e, n in presets:
            server.parse_bbox(f'{w},{s},{e},{n}')   # raises BadRequest on the old Australia preset

    def test_frontend_sensors_are_known_to_backend(self):
        sensors = set(re.findall(r'data-sensor="([A-Z0-9_]+)"', self.html))
        self.assertTrue(sensors)
        self.assertLessEqual(sensors, server.ALLOWED_SOURCES, 'frontend offers a sensor the backend rejects')

    def test_frontend_sends_key_in_header_not_url(self):
        self.assertIn("headers: { 'X-FIRMS-MAP-KEY': apiKey }", self.html)
        self.assertIn('/firms-api/area/json/${sensor}/', self.html)
        self.assertNotIn('/firms-api/area/json/${apiKey}/', self.html)
        self.assertIn("includes('Invalid API key')", self.html)
        self.assertIn('Invalid API key', server.classify_http_status(401)[1])


if __name__ == '__main__':
    unittest.main(verbosity=2)
