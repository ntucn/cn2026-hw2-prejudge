"""Public run.sh build, startup and time-limit stages with owned processes (T15/T16).

The real Bash runner and readiness helper run on a private copy; checks are
stand-ins, and services are owned stand-ins or a real compiled C server.
"""

import contextlib
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import types
import unittest

from runner_harness import PUBLIC_PORTS, busy_ports, listener_source, stop_recorded


ROOT = Path(__file__).resolve().parents[1]
CHECKS = ['server-0.py', 'server-1.py', 'client-0.py', 'client-1.py', 'client-0.py']

SERVER_C = r'''
#include <netinet/in.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/socket.h>
#include <unistd.h>

int main(int argc, char **argv) {
    if (argc != 2) { fprintf(stderr, "Usage: ./server [port]\n"); return -1; }
    alarm(60);
    const char *dir = getenv("TEST_PID_DIR");
    if (dir) {
        char path[512];
        snprintf(path, sizeof path, "%s/%d", dir, (int)getpid());
        FILE *file = fopen(path, "w");
        if (file) { fputs("server\n", file); fclose(file); }
    }
    int fd = socket(AF_INET, SOCK_STREAM, 0), one = 1;
    setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
    struct sockaddr_in addr = {0};
    addr.sin_family = AF_INET;
    addr.sin_port = htons(atoi(argv[1]));
    addr.sin_addr.s_addr = htonl(INADDR_ANY);
    if (bind(fd, (struct sockaddr *)&addr, sizeof addr) || listen(fd, 16)) { perror("listen"); return 1; }
    for (;;) { int client = accept(fd, NULL, NULL); if (client >= 0) close(client); }
}
'''
CLIENT_C = 'int main(void) { return 0; }\n'
MAKEFILE = '''.PHONY: all clean
all: server client
server: server.c
\tgcc -Wall -o server server.c
client: client.c
\tgcc -Wall -o client client.c
clean:
\t@rm -rf server client
'''

ALL_ONLY = """.PHONY: all clean
all:
\tgcc -Wall -o server srv.c
\tgcc -Wall -o client cli.c
clean:
\t@rm -rf server client
"""

PYTHON_STUB = r'''
import os, runpy, signal, sys, time
from pathlib import Path
script_path = sys.argv[1]; script = Path(script_path).name; args = sys.argv[2:]
if script == 'structure-checker.py':
    Path(args[args.index('--json-output') + 1]).write_text('{"status": "pass"}\n')
    sys.exit(0)
if script == 'web.py':
    os.execv(sys.executable, [sys.executable, os.environ['TEST_LISTENER'], 'web.py'])
if script == 'service_readiness.py' and args[0] == 'wait' and os.environ.get('TEST_READY_FAULT'):
    sys.exit(2)
if script == 'service_readiness.py':
    sys.argv = [script_path] + args
    runpy.run_path(script_path, run_name='__main__')
with open(os.environ['TEST_CALLED_LOG'], 'a') as output:
    output.write(script + '\n')
modes = dict(item.split('=') for item in os.environ.get('TEST_SCRIPT_MODES', '').split(',') if item)
if modes.get(script) in ('ignore-term', 'hang'):
    if modes[script] == 'ignore-term':
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    Path(os.environ['TEST_PID_DIR'], str(os.getpid())).write_text(script + '\n')
    time.sleep(60)
sys.exit(0)
'''


def alive(pid):
    try:
        state = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[0]
    except (FileNotFoundError, ProcessLookupError):
        return False
    return state not in ('Z', 'X')


class PublicRunnerStageTests(unittest.TestCase):
    def setUp(self):
        busy = busy_ports(PUBLIC_PORTS)
        if busy:
            self.skipTest(f'runner ports already in use: {busy}')

    def run_public(self, *, build='real', sources=None, makefile=MAKEFILE, env=None,
                   patches=(), occupy=(), npm_status=0, interrupt_after=None):
        with tempfile.TemporaryDirectory(prefix='cn-t15-public-') as directory, \
                contextlib.ExitStack() as cleanup:
            layout = Path(directory)
            pids = layout / 'pids'
            pids.mkdir()
            cleanup.callback(stop_recorded, pids)
            commands = layout / 'commands'
            commands.mkdir()

            def executable(name, body):
                path = commands / name
                path.write_text(body)
                path.chmod(0o755)

            executable('dos2unix', '#!/bin/sh\nexit 0\n')
            # T17: the runner must never fall back to name-based killing.
            executable('killall', f'#!/bin/sh\necho "$@" >> {layout}/killall-called\nexit 0\n')
            executable('npm', f'#!/bin/sh\nexit {npm_status}\n')
            executable('node', listener_source())
            executable('listener.py', listener_source())
            executable('python', f'#!{sys.executable}\n' + PYTHON_STUB)
            if build != 'real':
                executable('make', f'#!{sys.executable}\nimport os, shutil, sys, time\n'
                           'from pathlib import Path\n'
                           'name = sys.argv[1]\n'
                           'if name == "clean": sys.exit(0)\n'
                           f'time.sleep({build})\n'
                           'if name == "server":\n'
                           '    shutil.copy(os.environ["TEST_LISTENER"], name)\n'
                           'else:\n'
                           '    Path(name).write_text("#!/bin/sh\\nexit 0\\n")\n'
                           'Path(name).chmod(0o755)\n')
            submission = layout / 'submission'
            hw2 = submission / 'hw2'
            (hw2 / 'web').mkdir(parents=True)
            for name, text in {'server.c': SERVER_C, 'client.c': CLIENT_C, **(sources or {})}.items():
                if text is not None:
                    (hw2 / name).write_text(text)
            (hw2 / 'makefile').write_text(makefile)
            workdir = layout / 'prejudge'
            shutil.copytree(ROOT / 'scripts', workdir / 'scripts')
            source = (ROOT / 'run.sh').read_text()
            for old, new in patches:
                self.assertIn(old, source)
                source = source.replace(old, new)
            (workdir / 'run.sh').write_text(source)
            output = layout / 'output'
            for port in occupy:
                holder = socket.socket()
                holder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                holder.bind(('', port))
                holder.listen()
                cleanup.callback(holder.close)
            calls = layout / 'calls.txt'
            calls.touch()
            variables = dict(os.environ, PATH=f'{commands}{os.pathsep}{os.environ["PATH"]}',
                             TEST_CALLED_LOG=str(calls), TEST_PID_DIR=str(pids),
                             TEST_LISTENER=str(commands / 'listener.py'),
                             PYTHONDONTWRITEBYTECODE='1', **(env or {}))
            started = time.monotonic()
            process = subprocess.Popen(['bash', str(workdir / 'run.sh'), str(submission), str(output)],
                                       cwd=layout, env=variables, text=True,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            cleanup.callback(lambda: process.poll() is None and process.kill())
            if interrupt_after:
                deadline = time.monotonic() + 60
                while interrupt_after not in calls.read_text() and time.monotonic() < deadline:
                    time.sleep(.05)
                time.sleep(.5)
                started = time.monotonic()
                process.terminate()
            stdout, stderr = process.communicate(timeout=120)
            result = types.SimpleNamespace(returncode=process.returncode, stdout=stdout, stderr=stderr)
            elapsed = time.monotonic() - started
            leftovers = [path.read_text().strip() for path in pids.iterdir() if alive(int(path.name))]
            work_copies = list(Path('/tmp').glob('cn-hw2-prejudge.*'))
            summary = result.stdout.split('-----summary-----')[-1]
            logs = {path.name: path.read_text(errors='replace') for path in output.glob('*.log')}
            return types.SimpleNamespace(code=result.returncode, summary=summary, elapsed=elapsed,
                                         leftovers=leftovers, killall=(layout / 'killall-called').exists(),
                                         work_copies=work_copies,
                                         called=calls.read_text().splitlines(), logs=logs,
                                         debug=result.stdout[-4000:] + result.stderr[-4000:])

    def test_only_processes_started_by_this_run_are_stopped(self):
        bystanders = []
        with tempfile.TemporaryDirectory() as directory:
            for name in ['server', 'node', 'python']:
                path = Path(directory) / name
                shutil.copy('/bin/sleep', path)
                bystanders.append(subprocess.Popen([str(path), '120']))
            try:
                run = self.run_public(build=0)
                self.assertEqual(run.code, 0, run.debug)
                self.assertFalse(run.killall)
                self.assertEqual(run.leftovers, [])
                self.assertTrue(all(process.poll() is None for process in bystanders))
            finally:
                for process in bystanders:
                    process.kill()
                    process.wait()

    def test_interrupt_stops_services_and_removes_the_work_copy(self):
        before = set(Path('/tmp').glob('cn-hw2-prejudge.*'))
        run = self.run_public(build=0, interrupt_after='server-1.py',
                              env={'TEST_SCRIPT_MODES': 'server-1.py=hang'})
        self.assertEqual(run.code, 130, run.debug)
        self.assertLess(run.elapsed, 15)
        self.assertEqual(run.leftovers, [])
        self.assertEqual(set(run.work_copies) - before, set())

    def test_real_build_and_listening_server_pass(self):
        run = self.run_public()
        self.assertEqual(run.code, 0, run.debug)
        self.assertIn('build                       (server):   pass', run.summary)
        self.assertEqual(run.called, CHECKS)

    def test_real_compile_error_reports_failed_build_and_skips_server_checks(self):
        run = self.run_public(sources={'server.c': 'int main(void) { return missing; }\n'})
        self.assertEqual(run.code, 1, run.debug)
        self.assertIn('build                       (server): failed (compile failed)', run.summary)
        self.assertIn('server-test                 (server-0.py): failed (not run: server compile_failed)',
                      run.summary)
        self.assertIn('COMPILE FAILED', run.logs['make-server.log'])
        self.assertEqual(run.called, CHECKS[2:])

    def test_makefile_without_targets_falls_back_to_plain_make_once(self):
        run = self.run_public(sources={'server.c': None, 'client.c': None, 'srv.c': SERVER_C, 'cli.c': CLIENT_C}, makefile=ALL_ONLY)
        self.assertEqual(run.code, 0, run.debug)
        self.assertIn('NOTE: no server target; running plain make', run.logs['make-server.log'])
        self.assertEqual(run.called, CHECKS)

    def test_make_error_with_produced_binary_needs_review(self):
        makefile = MAKEFILE.replace('\tgcc -Wall -o server server.c\n',
                                    '\tgcc -Wall -o server server.c\n\tfalse\n')
        run = self.run_public(makefile=makefile)
        self.assertEqual(run.code, 1, run.debug)
        self.assertIn('needs_review (make failed but produced ./server)', run.summary)
        self.assertEqual(run.called, CHECKS)

    def test_build_deadline_is_incomplete(self):
        run = self.run_public(build=60, patches=[('BUILD_LIMIT=120', 'BUILD_LIMIT=1')])
        self.assertEqual(run.code, 2, run.debug)
        self.assertLess(run.elapsed, 30)
        self.assertIn('did not finish (exit 124', run.logs['make-server.log'])
        self.assertEqual(run.called, [])

    def test_occupied_port_and_exited_server_are_classified(self):
        occupied = self.run_public(build=0, occupy=[8080])
        self.assertEqual(occupied.code, 2, occupied.debug)
        self.assertIn('port 8080 is already in use', occupied.debug)
        self.assertEqual(occupied.called, CHECKS[1:])
        exited = self.run_public(build=0, env={'TEST_LISTEN_MODE': 'exit'})
        # The student's server is still checked; real requests would fail.
        self.assertEqual(exited.code, 2, exited.debug)  # pseudo-servers exited too
        self.assertIn('server-8080: process exited before listening', exited.debug)
        self.assertEqual(exited.called, ['server-0.py', 'server-1.py'])

    def test_npm_failure_and_readiness_fault_are_incomplete(self):
        npm = self.run_public(build=0, npm_status=1)
        self.assertEqual(npm.code, 2, npm.debug)
        self.assertEqual(npm.called, ['server-0.py', 'server-1.py', 'client-0.py'])
        fault = self.run_public(build=0, env={'TEST_READY_FAULT': '1'},
                                patches=[('SERVER_READY_LIMIT=10', 'SERVER_READY_LIMIT=1'),
                                         ('PSEUDO_READY_LIMIT=30', 'PSEUDO_READY_LIMIT=1')])
        self.assertEqual(fault.code, 2, fault.debug)
        self.assertIn('readiness could not be observed', fault.debug)
        self.assertEqual(fault.called, CHECKS)

    def test_preinstalled_pseudo_server_modules_skip_npm(self):
        run = self.run_public(build=0, npm_status=1, env={'TEST_NODE_MODULES': '0'})
        self.assertEqual(run.code, 0, run.debug)
        self.assertEqual(run.called, CHECKS)

    def test_check_time_limit_and_kill_grace_continue_to_later_checks(self):
        run = self.run_public(build=0, env={'TEST_SCRIPT_MODES': 'server-0.py=ignore-term'},
                              patches=[('runCheck 110 server-0.py', 'runCheck 2 server-0.py'),
                                       ('KILL_GRACE=10', 'KILL_GRACE=1')])
        self.assertEqual(run.code, 2, run.debug)
        self.assertIn('INCOMPLETE: server-0.py exited with status 137', run.debug)
        self.assertEqual(run.called, CHECKS)
        self.assertLess(run.elapsed, 40)


if __name__ == '__main__':
    unittest.main()
