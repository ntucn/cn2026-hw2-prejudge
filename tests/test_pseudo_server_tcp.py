"""Real peer tests. Skips mean incomplete acceptance, never a network pass."""
import base64
import http.client
import importlib.util
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from test_pseudo_servers import FORMAL, NODE_PATHS, FLASK_PATH


class PseudoServerTCPTests(unittest.TestCase):
    def setUp(self):
        try:
            with socket.socket() as probe:
                probe.bind(('127.0.0.1', 0))
                probe.listen()
        except OSError as error:
            self.skipTest(f'live TCP unavailable: {error}')
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def start(self, path, command, port=None):
        directory = self.root / path.parent.name
        directory.mkdir()
        shutil.copyfile(path, directory / path.name)
        if port is None:
            with socket.socket() as probe:
                probe.bind(('127.0.0.1', 0))
                port = probe.getsockname()[1]
        log = (directory / 'peer.log').open('wb')
        self.addCleanup(log.close)
        env = os.environ.copy()
        env['NODE_PATH'] = os.pathsep.join(filter(None, [str(path.parent / 'node_modules'),
                                                       env.get('NODE_PATH', '')]))
        proc = subprocess.Popen(command + ([str(port)] if FORMAL else []),
                                cwd=directory, env=env, stdout=log, stderr=log)

        def stop():
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=3)
        self.addCleanup(stop)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                self.fail(f'peer exited {proc.returncode}: {(directory / "peer.log").read_text()}')
            conn = http.client.HTTPConnection('127.0.0.1', port, timeout=0.2)
            try:
                conn.request('GET', '/')
                res = conn.getresponse()
                self.assertEqual(res.read(), b'It works!')
                return port
            except OSError:
                time.sleep(0.05)
            finally:
                conn.close()
        self.fail('peer readiness timed out')

    def transfer(self, conn):
        boundary = 'cn-test-boundary'
        payload = bytes(range(256)) * 4
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="upfile"; '
                'filename="transfer.bin"\r\nContent-Type: application/octet-stream\r\n\r\n').encode()
        body += payload + f'\r\n--{boundary}--\r\n'.encode()
        headers = {'Content-Type': f'multipart/form-data; boundary={boundary}',
                   'Authorization': 'Basic ' + base64.b64encode(b'demo:123').decode()}
        conn.request('POST', '/api/file', body=body, headers=headers)
        response = conn.getresponse()
        self.assertEqual(response.status, 200)
        response.read()
        conn.request('GET', '/api/file/transfer.bin')
        response = conn.getresponse()
        self.assertEqual(response.status, 200)
        self.assertEqual(response.read(), payload)

    def assert_closes(self, response, observer):
        # Connection is a token list; Werkzeug can append another valid close token.
        tokens = {token.strip().lower()
                  for token in (response.getheader('Connection') or '').split(',')}
        self.assertIn('close', tokens)
        self.assertNotIn('keep-alive', tokens)
        response.read()
        # http.client closes its own descriptor after reading Connection: close.
        # Observe peer EOF through a duplicate descriptor instead.
        observer.settimeout(3)
        self.assertEqual(observer.recv(1), b'')

    def test_node_keeps_connection_beyond_old_default_and_honors_close(self):
        if not shutil.which('node'):
            self.skipTest('Node not installed')
        for path in NODE_PATHS:
            probe = subprocess.run(['node', '-e', "require('express'); require('multer'); require('express-basic-auth');"],
                                   cwd=path.parent, capture_output=True, timeout=5)
            if probe.returncode:
                self.skipTest(f'Node dependencies unavailable for {path.parent.name}')
        for path in NODE_PATHS:
            with self.subTest(peer=path.parent.name):
                port = self.start(path, ['node', path.name], None if FORMAL else 4500)
                conn = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
                self.addCleanup(conn.close)
                conn.request('GET', '/')
                self.assertEqual(conn.getresponse().read(), b'It works!')
                original = conn.sock
                time.sleep(6)  # The previous Node default closes idle sockets after 5 seconds.
                self.transfer(conn)
                self.assertIs(conn.sock, original)
                conn.request('POST', '/api/file', body=b'', headers={'Connection': 'close'})
                with original.dup() as observer:
                    response = conn.getresponse()
                    self.assertEqual(response.status, 401)
                    self.assert_closes(response, observer)
                conn.close()

    def test_flask_closes_success_and_auth_error_without_reloader(self):
        if any(importlib.util.find_spec(name) is None for name in ('flask', 'flask_httpauth')):
            self.skipTest('Flask / Flask-HTTPAuth not installed')
        port = self.start(FLASK_PATH, [sys.executable, FLASK_PATH.name], None if FORMAL else 2024)
        conn = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
        self.addCleanup(conn.close)
        for method, route in [('GET', '/'), ('POST', '/api/file')]:
            conn.connect()
            original = conn.sock
            conn.request(method, route, body=b'', headers={'Connection': 'keep-alive'})
            with original.dup() as observer:
                response = conn.getresponse()
                self.assertEqual(response.status, 200 if method == 'GET' else 401)
                self.assert_closes(response, observer)
        self.transfer(conn)


if __name__ == '__main__':
    unittest.main()
