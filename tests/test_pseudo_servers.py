"""Execute peer startup with dependency doubles; these are not TCP tests."""
import contextlib
import json
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
FORMAL = (ROOT / 'scripts/pseudo-server').is_dir()
NODE_PATHS = ([ROOT / f'scripts/pseudo-server/{name}/app.js'
               for name in ('nodejs', 'nodejs-ka')] if FORMAL else
              [ROOT / 'scripts/assets/pseudo-server/app.js'])
FLASK_PATH = ROOT / ('scripts/pseudo-server/flask/web.py' if FORMAL else
                     'scripts/assets/pseudo-server/web.py')

NODE_HARNESS = r'''
const fs = require('fs'), vm = require('vm'), http = require('http');
const routes = [], servers = [], auth = [];
const app = function(req, res) {};
app.get = (path, ...handlers) => routes.push(['GET', path]);
app.post = (path, ...handlers) => routes.push(['POST', path]);
function createServer(listener) {
    const server = http.createServer(listener);
    server.listen = function(port) {
        this.listenPort = port;
        this.atListen = {keepAlive: this.keepAliveTimeout, headers: this.headersTimeout,
                         request: this.requestTimeout, idle: this.timeout};
        return this;
    };
    servers.push(server);
    return server;
}
app.listen = port => createServer(app).listen(port);
const multer = () => ({single: () => function() {}});
multer.diskStorage = value => value;
const dependencies = {
    express: () => app, http: {createServer}, multer,
    'express-basic-auth': options => {auth.push(options); return function() {};},
    fs: {existsSync: () => true}, path: require('path'), child_process: {}
};
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), {
    require: name => {if (!(name in dependencies)) throw Error(name); return dependencies[name];},
    process: {argv: ['node', 'app.js', '4312']}, console: {log: () => {}}
});
console.log(JSON.stringify({servers: servers.map(s => s.atListen),
                           ports: servers.map(s => s.listenPort), routes, auth}));
'''


class FakeFlask:
    def __init__(self, *args):
        self.config, self.routes, self.hooks, self.starts = {}, [], [], []

    def route(self, path, methods):
        def register(handler):
            self.routes.append((methods[0], path))
            return handler
        return register

    def after_request(self, handler):
        self.hooks.append(handler)
        return handler

    def run(self, **options):
        self.starts.append(options)


class FakeAuth:
    def verify_password(self, handler):
        return handler

    def login_required(self, handler):
        return handler


class PseudoServerTests(unittest.TestCase):
    def load_flask(self):
        app = FakeFlask()
        flask = types.ModuleType('flask')
        flask.Flask = lambda *a: app
        flask.request = types.SimpleNamespace(files={})
        flask.send_from_directory = lambda *a: None
        auth = types.ModuleType('flask_httpauth')
        auth.HTTPBasicAuth = FakeAuth
        with tempfile.TemporaryDirectory() as directory, contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.dict(sys.modules, {'flask': flask, 'flask_httpauth': auth}))
            stack.enter_context(mock.patch.object(sys, 'argv', [str(FLASK_PATH), '4312']))
            old_cwd = Path.cwd()
            import os
            try:
                os.chdir(directory)
                namespace = runpy.run_path(str(FLASK_PATH), run_name='__main__')
            finally:
                os.chdir(old_cwd)
        return app, namespace

    def test_node_timeouts_apply_to_real_http_server_before_listen(self):
        if not shutil.which('node'):
            self.skipTest('Node is required for the startup configuration test')
        for path in NODE_PATHS:
            with self.subTest(peer=path.parent.name):
                run = subprocess.run(['node', '-e', NODE_HARNESS, str(path)],
                                     capture_output=True, text=True, timeout=10)
                self.assertEqual(run.returncode, 0, run.stderr)
                result = json.loads(run.stdout)
                self.assertEqual(result['servers'], [dict(keepAlive=120000, headers=125000,
                                                         request=300000, idle=0)])
                self.assertEqual(result['ports'], [4312 if FORMAL else 4500])
                expected = {('GET', '/'), ('GET', '/upload/file'), ('GET', '/upload/video'),
                            ('GET', '/api/file/:path*'), ('POST', '/api/file')}
                if FORMAL:
                    expected.add(('POST', '/api/video'))
                self.assertEqual(set(map(tuple, result['routes'])), expected)
                self.assertTrue(result['auth'])
                self.assertTrue(all(a['users'] == {'demo': '123'} and a['challenge']
                                    for a in result['auth']))

    def test_flask_starts_once_without_reloader_and_preserves_contract(self):
        app, namespace = self.load_flask()
        self.assertEqual(app.starts, [dict(debug=False, use_reloader=False, load_dotenv=False,
                                          port=4312 if FORMAL else 2024, host='0.0.0.0')])
        self.assertIsInstance(app.starts[0]['port'], int)
        # T31: a 200 MiB file plus multipart overhead must still be accepted.
        self.assertGreaterEqual(app.config['MAX_CONTENT_LENGTH'], 200 * 1024 * 1024 + 64 * 1024)
        self.assertEqual(namespace['verify_password']('demo', '123'), 'demo')
        self.assertIsNone(namespace['verify_password']('demo', 'wrong'))
        expected = {('GET', '/'), ('GET', '/api/file/<path:filepath>'), ('POST', '/api/file')}
        if FORMAL:
            expected.add(('POST', '/api/video'))
        else:
            expected.update({('GET', '/upload/file'), ('GET', '/upload/video')})
        self.assertEqual(set(app.routes), expected)

    def test_flask_close_hook_preserves_success_and_error_responses(self):
        app, _ = self.load_flask()
        self.assertEqual(len(app.hooks), 1)
        for status in (200, 401, 404, 500):
            response = types.SimpleNamespace(status_code=status, content=b'binary\x00',
                                             headers={'Connection': 'keep-alive', 'Content-Length': '7'})
            self.assertIs(app.hooks[0](response), response)
            self.assertEqual(response.headers, {'Connection': 'close', 'Content-Length': '7'})
            self.assertEqual((response.status_code, response.content), (status, b'binary\x00'))


if __name__ == '__main__':
    unittest.main()
