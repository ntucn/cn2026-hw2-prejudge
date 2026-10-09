import contextlib
import importlib
import io
from pathlib import Path
import runpy
import shutil
import sys
import tempfile
import types
import unittest
from unittest import mock
from urllib.parse import urlparse
from student_stub import write_student


SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'


class PublicChecksTests(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(SCRIPTS))
        self.addCleanup(sys.path.remove, str(SCRIPTS))
        self.utils = importlib.import_module('assets.utils')
        self.fixtures = importlib.import_module('assets.fixtures')

    def execute(self, filename, failed_name='*', stream_mode=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / 'submission'
            (repo / 'hw2').mkdir(parents=True)
            shutil.copytree(SCRIPTS / 'assets', root / 'assets',
                            ignore=shutil.ignore_patterns('*.py', 'pseudo-server'))
            backend = root / 'assets/pseudo-server/files-nodejs'
            backend.mkdir(parents=True)
            self.fixtures.genFile(backend / 'server.bin', '1K')
            command_log = root / 'commands.txt'
            if stream_mode is not None:
                write_student(repo / 'hw2', backend, backend, command_log, stream_mode)
            commands = []
            suites = []
            original_checks = self.utils.Checks

            class RecordingChecks(original_checks):
                def __init__(self):
                    super().__init__()
                    suites.append(self)

            class Process:

                def __init__(self, command, cwd, **kwargs):
                    self.cwd = Path(cwd)
                    self.closed = False
                    import shlex
                    args = shlex.split(command) if isinstance(command, str) else command
                    self.valid_auth = args[-1:] != ['demo:12345']
                    self.reply = ('Usage: ./client [host] [port] [username:password]\n'
                                  if len(args) not in (3, 4) else '')

                def command(self, command, timeout=5, stream='stdout'):
                    if self.closed:
                        raise EOFError
                    commands.append(command)
                    verb, _, name = command.partition(' ')
                    if verb == 'quit':
                        self.closed = True
                        self.reply = 'Bye.\n'
                    elif verb == 'auth':
                        self.reply = 'Command succeeded.\n' if name else 'Usage: auth [username:password]\n'
                        if name:
                            self.valid_auth = True
                    elif verb in {'put', 'putv', 'get'}:
                        if not name:
                            self.reply = f'Usage: {verb} [file]\n'
                        else:
                            source = backend / name if verb == 'get' else self.cwd / name
                            if not source.exists():
                                place = 'server' if verb == 'get' else 'local'
                                self.reply = f'Command failed. File not found on {place}.\n'
                            elif verb != 'get' and not self.valid_auth:
                                self.reply = 'Command failed. Invalid user or wrong password.\n'
                            else:
                                target = self.cwd / 'files' / name if verb == 'get' else backend / name
                                target.parent.mkdir(parents=True, exist_ok=True)
                                shutil.copyfile(source, target)
                                self.reply = 'Command succeeded.\n'
                    else:
                        self.reply = 'Command Not Found.\n'
                    return self.reply.encode()

                def startup_usage(self, **kwargs):
                    return self.reply.encode()

                def close(self):
                    self.closed = True

            def generate(path, size='1M'):
                if failed_name == '*' or Path(path).name == failed_name:
                    return self.fixtures.markFixtureError(path, OSError('injected preparation failure'))
                return self.fixtures.genFile(path, '1K')

            def get(url, **kwargs):
                name = 'index.html' if urlparse(url).path == '/' else 'listv.rhtml'
                body = (root / 'assets' / name).read_text()
                body = body.replace(r'([\w\W]+)', 'Student').replace(r'([\w\W]*)', '')
                return types.SimpleNamespace(status_code=200, content=body.encode())

            pwn = types.ModuleType('pwn')
            pwn.process = Process
            requests = importlib.import_module('requests')
            old_cwd = Path.cwd()
            captured = io.StringIO()
            with contextlib.ExitStack() as stack:
                stack.enter_context(mock.patch.dict(sys.modules, {'pwn': pwn}))
                if stream_mode is None:
                    stack.enter_context(mock.patch.object(self.utils, 'ClientProcess', Process))
                stack.enter_context(mock.patch.object(self.utils, 'genFile', generate))
                stack.enter_context(mock.patch.object(self.utils, 'Checks', RecordingChecks))
                stack.enter_context(mock.patch.object(requests, 'get', side_effect=get))
                stack.enter_context(mock.patch.object(requests, 'post', side_effect=AssertionError('unexpected upload')))
                stack.enter_context(mock.patch.object(sys, 'argv', [filename, '8080', str(repo), 'nodejs']))
                stack.enter_context(contextlib.redirect_stdout(captured))
                stack.enter_context(contextlib.redirect_stderr(captured))
                try:
                    import os
                    os.chdir(root)
                    with self.assertRaises(SystemExit) as stopped:
                        runpy.run_path(str(SCRIPTS / filename), run_name='__main__')
                finally:
                    os.chdir(old_cwd)
            if stream_mode is not None:
                commands = command_log.read_text().splitlines()
            return suites[0].results, stopped.exception.code, commands, captured.getvalue()

    def test_real_stream_violations_fail_but_public_checks_continue(self):
        results, status, _, _ = self.execute('client-0.py', failed_name=None, stream_mode='correct')
        self.assertEqual(status, 0)
        self.assertTrue(all(result == 'pass' for _, result in results))
        results, status, commands, messages = self.execute('client-0.py', failed_name=None,
                                                         stream_mode='errors-on-stdout')
        self.assertEqual(status, 1)
        self.assertEqual(len(results), 15)
        for name in ['startup usage', 'put usage', 'auth usage', 'wrong credentials',
                     'get missing file', 'unknown command']:
            self.assertEqual(dict(results)[name], 'failed')
        self.assertEqual(dict(results)['put file'], 'pass')
        self.assertEqual(dict(results)['quit'], 'pass')
        self.assertIn('unexpected stdout', messages)
        self.assertIn('quit', commands)
        results, status, commands, messages = self.execute('client-1.py', failed_name=None,
                                                         stream_mode='success-on-stderr')
        self.assertEqual(status, 1)
        self.assertEqual(len(results), 5)
        self.assertTrue(all(result == 'failed' for _, result in results[1:]))
        self.assertIn('unexpected stderr', messages)
        self.assertIn('quit', commands)

    def test_invalid_startup_exit_and_unknown_spelling_fail_but_continue(self):
        for mode, failed in [('bad-startup-exit', 'startup usage'),
                             ('unknown-lowercase', 'unknown command')]:
            with self.subTest(mode=mode):
                results, status, commands, _ = self.execute('client-0.py', failed_name=None, stream_mode=mode)
                self.assertEqual(status, 1)
                self.assertEqual(dict(results)[failed], 'failed')
                self.assertEqual(dict(results)['put file'], 'pass')
                self.assertEqual(dict(results)['quit'], 'pass')
                self.assertIn('quit', commands)

    def test_client_basic_continues_to_auth_missing_file_and_quit(self):
        results, status, commands, messages = self.execute('client-0.py')
        self.assertEqual(status, 2)
        self.assertEqual(len(results), 15)
        self.assertEqual(dict(results)['put file'], 'incomplete')
        self.assertEqual(dict(results)['get file'], 'incomplete')
        self.assertEqual(dict(results)['get missing file'], 'pass')
        self.assertEqual(dict(results)['quit'], 'pass')
        self.assertIn('auth demo:123', commands)
        self.assertIn('injected preparation failure', messages)

    def test_public_client_checks_pass_when_preparation_succeeds(self):
        for filename in ['client-0.py', 'client-1.py']:
            with self.subTest(filename=filename):
                results, status, _, _ = self.execute(filename, failed_name=None)
                self.assertEqual(status, 0)
                self.assertTrue(all(result == 'pass' for _, result in results))

    def test_large_file_failure_does_not_block_other_transfers(self):
        results, status, commands, _ = self.execute('client-1.py', 'L4RGebUtNoT7o01ArgE')
        self.assertEqual(status, 2)
        self.assertEqual(len(results), 5)
        self.assertEqual(results[1][1], 'incomplete')
        self.assertTrue(all(result == 'pass' for _, result in results[2:]))
        self.assertIn('put iS7H@TL3g4L?', commands)
        self.assertIn('quit', commands)

    def test_server_fixture_failures_leave_independent_checks_running(self):
        results, status, _, _ = self.execute('server-1.py')
        self.assertEqual(status, 2)
        self.assertEqual(len(results), 11)
        self.assertEqual(dict(results)['homepage'], 'pass')
        self.assertEqual(dict(results)['empty video list'], 'pass')
        self.assertEqual(dict(results)['download special filename'], 'incomplete')


if __name__ == '__main__':
    unittest.main()
