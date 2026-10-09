"""Readiness helper edge cases with owned processes (T15/T17)."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from assets import service_readiness as readiness  # noqa: E402


class ReadinessTests(unittest.TestCase):
    def setUp(self):
        readiness._unreadable_since.clear()

    def test_transient_permission_error_is_skipped_but_a_persistent_one_fails(self):
        real = os.listdir
        state = {'calls': 0}

        def flaky(path):
            if str(path).endswith('/fd') and state['calls'] < 1:
                state['calls'] += 1
                raise PermissionError('exiting process')
            return real(path)

        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0)); listener.listen()
            port = listener.getsockname()[1]
            with mock.patch.object(readiness.os, 'listdir', flaky):
                results = readiness.wait([('self', port, os.getpid())], 2)
            self.assertEqual(results['self'][0], 'ready')
            with mock.patch.object(readiness.os, 'listdir', side_effect=PermissionError('denied')), \
                    mock.patch.object(readiness, 'UNREADABLE_GRACE', 0.2):
                with self.assertRaises(readiness.ObservationError):
                    readiness.wait([('self', port + 0, os.getpid())], 1)

    def test_vanishing_stat_is_treated_as_gone(self):
        with mock.patch.object(readiness.Path, 'read_text', return_value=''):
            self.assertIsNone(readiness.process_state(os.getpid()))

    def test_child_listener_counts_and_exit_is_reported(self):
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0)); port = probe.getsockname()[1]
        code = ('import socket,subprocess,sys,time\n'
                f'subprocess.Popen([sys.executable,"-c","import socket,time;s=socket.socket();s.bind((\\"127.0.0.1\\",{port}));s.listen();time.sleep(20)"])\n'
                'time.sleep(20)\n')
        parent = subprocess.Popen([sys.executable, '-c', code])
        exited = subprocess.Popen([sys.executable, '-c', 'raise SystemExit(4)'])
        exited.wait()
        try:
            results = readiness.wait([('forking', port, parent.pid), ('gone', 1, exited.pid)], 5)
        finally:
            for child in readiness.descendants(parent.pid)[1:]:
                os.kill(child, 9)
            parent.kill(); parent.wait()
        self.assertEqual(results['forking'][0], 'ready')
        self.assertEqual(results['gone'][0], 'exited')


if __name__ == '__main__':
    unittest.main()
