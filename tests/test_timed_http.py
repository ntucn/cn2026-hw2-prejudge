"""Total-deadline HTTP helper, shared with the official judge (T35/T36)."""
import http.server
from pathlib import Path
import socket
import sys
import threading
import time
import unittest
from urllib.parse import parse_qs, urlsplit

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from assets import timed_http


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def trickle(self, data):
        try:
            for byte in data:
                self.wfile.write(bytes([byte]))
                self.wfile.flush()
                time.sleep(.05)
        except OSError:
            pass

    def paced(self, total, rate, write=True):
        """Send or receive `total` bytes at `rate` bytes per second."""
        started, done = time.monotonic(), 0
        try:
            while done < total:
                size = min(64 * 1024, total - done)
                if write:
                    self.wfile.write(b'x' * size)
                    self.wfile.flush()
                else:
                    size = len(self.rfile.read(size))
                    if not size:
                        return
                done += size
                time.sleep(max(0, started + done / rate - time.monotonic()))
        except OSError:
            pass

    def do_GET(self):
        url = urlsplit(self.path)
        if url.path == '/rate':
            query = {key: float(value[0]) for key, value in parse_qs(url.query).items()}
            self.send_response(200)
            self.send_header('Content-Length', str(int(query['bytes'])))
            self.end_headers()
            self.paced(int(query['bytes']), query['rate'])
        elif self.path == '/slow-body':
            self.send_response(200)
            self.send_header('Content-Length', '100')
            self.end_headers()
            self.trickle(b'x' * 100)
        elif self.path == '/slow-close-delimited':
            self.wfile.write(b'HTTP/1.1 200 OK\r\nConnection: close\r\n\r\n')
            self.trickle(b'x' * 100)
            self.close_connection = True
        elif self.path == '/slow-headers':
            self.trickle(b'HTTP/1.1 200 OK\r\n' + b'X-Slow: ' + b'y' * 80 + b'\r\n\r\n')
        else:
            self.send_response(200)
            self.send_header('Content-Length', '5')
            self.send_header('X-A', '1')
            self.send_header('x-a', '2')
            self.end_headers()
            self.wfile.write(b'hello')

    def do_POST(self):
        url = urlsplit(self.path)
        if url.path == '/rate':
            # Read the upload at a fixed rate, then answer.
            self.paced(int(self.headers['Content-Length']), float(parse_qs(url.query)['rate'][0]), write=False)
            self.send_response(200)
            self.send_header('Content-Length', '14')
            self.end_headers()
            self.wfile.write(b'File Uploaded\n')
            return
        if self.path == '/early':
            # Answer without reading the body, like a server rejecting credentials.
            self.send_response(401)
            self.send_header('Content-Length', '13')
            self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.write(b'Unauthorized\n')
            self.wfile.flush()
            self.connection.shutdown(socket.SHUT_RDWR)
            return
        data = self.rfile.read(int(self.headers['Content-Length']))
        self.send_response(200)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class TimedHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.server.daemon_threads = True
        cls.url = f'http://127.0.0.1:{cls.server.server_address[1]}'
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_response_matches_requests(self):
        mine = timed_http.get(self.url + '/', 2)
        theirs = requests.get(self.url + '/', timeout=2)
        self.assertEqual((mine.status_code, mine.content, mine.text), (200, b'hello', 'hello'))
        self.assertEqual(mine.headers['x-a'], theirs.headers['x-a'])

    def test_multipart_upload_and_auth_are_prepared_by_requests(self):
        payload = b'z' * 300000
        response = timed_http.post(self.url + '/echo', 5, auth=('demo', '123'),
                                   files={'upfile': ('a b.txt', payload, 'application/octet-stream')})
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'filename="a b.txt"', response.content)
        self.assertIn(payload, response.content)
        self.assertEqual(response.request.headers['Authorization'], 'Basic ZGVtbzoxMjM=')

    def test_early_answer_is_read_although_the_body_was_not(self):
        response = timed_http.post(self.url + '/early', 5,
                                   files={'upfile': ('big', b'z' * (30 * 1024 * 1024), 'application/octet-stream')})
        self.assertEqual((response.status_code, response.content), (401, b'Unauthorized\n'))

    def test_trickling_peer_cannot_extend_the_total_deadline(self):
        for path in ['/slow-body', '/slow-close-delimited', '/slow-headers']:
            with self.subTest(path=path):
                started = time.monotonic()
                with self.assertRaises(requests.exceptions.Timeout):
                    timed_http.get(self.url + path, 1)
                self.assertLess(time.monotonic() - started, 1.8)

    def test_transfer_must_keep_at_least_one_mib_per_second(self):
        # Limit = 0.5 s + 2 MiB / 1 MiB/s = 2.5 s: 1.6 MiB/s takes about 1.25 s,
        # 0.6 MiB/s would need about 3.3 s.
        size = 2 * timed_http.MIB
        limit = timed_http.transfer_timeout(.5, size)
        download = self.url + f'/rate?bytes={size}&rate='
        upload = self.url + '/rate?rate='
        body = {'upfile': ('f', b'z' * size, 'application/octet-stream')}
        self.assertEqual(len(timed_http.get(download + str(1.6 * timed_http.MIB), limit).content), size)
        self.assertEqual(timed_http.post(upload + str(1.6 * timed_http.MIB), limit, files=body).status_code, 200)
        for call in (lambda: timed_http.get(download + str(.6 * timed_http.MIB), limit),
                     lambda: timed_http.post(upload + str(.6 * timed_http.MIB), limit, files=body)):
            started = time.monotonic()
            with self.assertRaises(requests.exceptions.Timeout):
                call()
            self.assertLess(time.monotonic() - started, limit + .8)

    def test_refused_connection_is_a_connection_error(self):
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        with self.assertRaises(requests.exceptions.ConnectionError):
            timed_http.get(f'http://127.0.0.1:{port}/', 1)

    def test_transfer_limit_is_base_plus_size_at_one_mib_per_second(self):
        self.assertEqual(timed_http.transfer_timeout(10, '100M'), 110)
        self.assertEqual(timed_http.transfer_timeout(3, '150M'), 153)
        self.assertEqual(timed_http.transfer_timeout(3, 1024), 3 + 1 / 1024)


if __name__ == '__main__':
    unittest.main()
