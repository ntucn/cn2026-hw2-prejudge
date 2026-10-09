"""Exercise the real shell runner with local processes replacing student/tools."""

import contextlib
import csv
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from runner_harness import FORMAL_PORTS, PUBLIC_PORTS, busy_ports, listener_source, stop_recorded


ROOT = Path(__file__).resolve().parents[1]


class RunnerTests(unittest.TestCase):
    def test_failure_and_incomplete_status_do_not_stop_later_scripts(self):
        formal = (ROOT / 'scripts/check-results.py').exists()
        busy = busy_ports(FORMAL_PORTS if formal else PUBLIC_PORTS)
        if busy:
            self.skipTest(f'runner ports already in use: {busy}')
        with tempfile.TemporaryDirectory() as directory, contextlib.ExitStack() as cleanup:
            layout = Path(directory)
            commands = layout / 'commands'
            commands.mkdir()
            calls = layout / 'calls.txt'
            pids = layout / 'pids'
            pids.mkdir()
            # Stop owned stand-ins before the directory is removed.
            cleanup.callback(stop_recorded, pids)

            def executable(name, body):
                path = commands / name
                path.write_text(body)
                path.chmod(0o755)

            for name in ['killall', 'chown', 'npm', 'dos2unix']:
                executable(name, '#!/bin/sh\nexit 0\n')
            # Services must really listen: the runner waits for that (T15).
            executable('node', listener_source())
            executable('listener.py', listener_source())
            executable('sudo', '#!/bin/sh\nif [ "$1" = chown ]; then\n  exit 0\nfi\nexec "$@"\n')
            executable('sleep', '#!/bin/sh\n/bin/sleep 0.2\n')
            executable('make', f'#!{sys.executable}\nimport os, shutil, sys\nfrom pathlib import Path\n'
                       'name = sys.argv[1]\n'
                       'if name == "server":\n'
                       '    shutil.copy(os.environ["TEST_LISTENER"], name)\n'
                       'elif name == "client":\n'
                       '    Path(name).write_text("#!/bin/sh\\nexit 0\\n")\n'
                       'if name in ("server", "client"):\n'
                       '    Path(name).chmod(0o755)\n')
            executable('python', f'#!{sys.executable}\n' + r'''
import ast, csv, os
from pathlib import Path
import runpy, sys
script_path = sys.argv[1]
script = Path(script_path).name
args = sys.argv[2:]
if script == "structure-checker.py":
    with open(os.environ["TEST_CALLED_LOG"], "a") as output:
        output.write(script + "\n")
    sys.path.insert(0, str(Path(script_path).resolve().parent))
    sys.argv = [script_path] + args
    runpy.run_path(script_path, run_name="__main__")
    sys.exit(0)
if script == 'check-results.py':
    sys.argv = [script] + args
    runpy.run_path(script, run_name='__main__')
    sys.exit(0)
if script == 'web.py':
    os.execv(sys.executable, [sys.executable, os.environ['TEST_LISTENER'], 'web.py'])
if script == 'service_readiness.py':
    sys.argv = [script_path] + args
    runpy.run_path(script_path, run_name='__main__')
with open(os.environ['TEST_CALLED_LOG'], 'a') as output:
    output.write(script + '\n')
if os.environ['TEST_RUNNER_KIND'] == 'public':
    sys.exit({'server-1.py': 2, 'server-0.py': 1}.get(script, 0))
if script == 'server-3-endpoint-videoplayer.py':
    sys.exit(1)  # Unexpected setup error: missing CSV must be incomplete.
data = ast.parse(Path(script).read_text())
begins = sorted((n for n in ast.walk(data) if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and n.func.attr == 'testcase_begin'), key=lambda n: n.lineno)
options = dict(zip(args[::2], args[1::2]))
path = Path(options['--output-dir']) / (Path(script).stem + '-' + options['--id'] + '.csv')
incomplete = script == 'server-2-endpoint-list.py'
failed = script == 'server-6-connection.py'
with path.open('w', newline='') as output:
    writer = csv.writer(output)
    writer.writerow(['name', 'expect', 'score', 'got', 'result', 'comment'])
    for node in begins:
        name, score = ast.literal_eval(node.args[0]), ast.literal_eval(node.args[1])
        writer.writerow([name, '', score, '' if incomplete else 0 if failed else score,
                         'incomplete' if incomplete else 'failed' if failed else 'pass',
                         'injected fixture failure' if incomplete else ''])
sys.exit(2 if incomplete else 1 if failed or script == 'server-7-multiclients.py' else 0)
''')
            env = dict(os.environ, PATH=f'{commands}:{os.environ["PATH"]}',
                       TEST_CALLED_LOG=str(calls),
                       TEST_PID_DIR=str(pids), TEST_LISTENER=str(commands / 'listener.py'),
                       TEST_RUNNER_KIND='formal' if formal else 'public',
                       PYTHONDONTWRITEBYTECODE='1')
            source = (ROOT / 'run.sh').read_text()
            if formal:
                mounts = layout / 'mnt'
                hw2 = mounts / 'hw2'
                (hw2 / 'web').mkdir(parents=True)
                shutil.copytree(ROOT / 'scripts', mounts / 'judger/scripts')
                for name in ['index.html', 'uploadf.html', 'uploadv.html', 'listf.rhtml', 'listv.rhtml', 'player.rhtml']:
                    shutil.copyfile(ROOT / 'scripts/assets' / name, hw2 / 'web' / name)
                (mounts / 'output').mkdir()
                source = re.sub(r'/judge(?=/|\b)', str(layout / 'judge'), source)
                source = source.replace('/mnt/', str(mounts) + '/')
                workdir = layout
                argument = 'b00000000'
            else:
                workdir = layout / 'prejudge'
                shutil.copytree(ROOT / 'scripts', workdir / 'scripts')
                hw2 = layout / 'submission/hw2'
                (hw2 / 'web').mkdir(parents=True)
                shutil.copyfile(ROOT / 'scripts/assets/index.html', hw2 / 'web/index.html')
                argument = str(hw2.parent)
            script = workdir / 'run.sh'
            script.write_text(source)
            result = subprocess.run(['bash', str(script), argument], cwd=workdir,
                                    env=env, text=True, capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            called = calls.read_text().splitlines()
            if formal:
                self.assertEqual(len(called), 12)
                self.assertEqual(called[-1], 'client-5-command-mix-closeeverytime.py')
                with (mounts / 'output/test-status-b00000000.tsv').open() as output:
                    rows = list(csv.DictReader(output, delimiter='\t'))
                self.assertEqual(len(rows), 12)
                statuses = {row['test']: row['result'] for row in rows}
                self.assertEqual(statuses['server-2-endpoint-list'], 'incomplete')
                self.assertEqual(statuses['server-3-endpoint-videoplayer'], 'incomplete')
                self.assertEqual(statuses['server-6-connection'], 'failed')
                self.assertEqual(statuses['server-7-multiclients'], 'incomplete')
                self.assertEqual(statuses['client-5-command-mix-closeeverytime'], 'pass')
            else:
                self.assertEqual(called, ['structure-checker.py', 'server-0.py', 'server-1.py',
                                          'client-0.py', 'client-1.py', 'client-0.py'])
                self.assertIn('incomplete', result.stdout)
                self.assertIn('-----summary-----', result.stdout)


if __name__ == '__main__':
    unittest.main()
