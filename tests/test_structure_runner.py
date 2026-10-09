"""Actual shell runners and checker, with owned stand-ins for build/HTTP stages."""
import contextlib
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

from runner_harness import FORMAL_PORTS, PUBLIC_PORTS, busy_ports, listener_source, stop_recorded


ROOT = Path(__file__).resolve().parents[1]
FORMAL = (ROOT / 'scripts/check-results.py').exists()


def snapshot(root):
    return {str(path.relative_to(root)): (stat.S_IMODE(path.lstat().st_mode),
                os.readlink(path) if path.is_symlink() else
                hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else 'directory')
            for path in root.rglob('*')}


class StructureRunnerTests(unittest.TestCase):
    def exercise(self, mode, late_status=0):
        busy = busy_ports(FORMAL_PORTS if FORMAL else PUBLIC_PORTS)
        if busy:
            self.skipTest(f'runner ports already in use: {busy}')
        with tempfile.TemporaryDirectory(prefix='cn-structure-runner-') as directory, \
                contextlib.ExitStack() as cleanup:
            layout = Path(directory)
            pids = layout / 'pids'
            pids.mkdir()
            # Stop owned stand-ins before the next run or directory removal.
            cleanup.callback(stop_recorded, pids)
            submission = layout / 'submission with spaces'
            hw2 = submission / 'hw2'
            (hw2 / 'web').mkdir(parents=True)
            (hw2 / 'utils').mkdir()
            for name in ['.gitignore', 'Dockerfile', 'docker-compose.yml']:
                (submission / name).write_text('fixture\n')
            for name in ['makefile', 'client.c', 'server.c']:
                (hw2 / name).write_text('source\n')
            for name in ['base64.c', 'base64.h']:
                (hw2 / 'utils' / name).write_text('helper\n')
            for name in ['index.html','uploadf.html','uploadv.html','listf.rhtml','listv.rhtml','player.rhtml']:
                (hw2 / 'web' / name).write_text('original template\n')
            # A file symlink must be copied as data, so writes cannot hit the original.
            (hw2 / 'upload-form-template.html').write_text('original form\n')
            (hw2 / 'web/uploadf.html').unlink()
            (hw2 / 'web/uploadf.html').symlink_to('../upload-form-template.html')
            if mode == 'review':
                (hw2 / 'client').write_text('original executable\n')
                (hw2 / 'secret').write_text('original secret\n')
                (hw2 / 'nested').mkdir()
                (hw2 / 'nested/leftover.o').write_bytes(b'object')
            # Model a readonly original, including files copied with readonly modes.
            for path in submission.rglob('*'):
                if not path.is_symlink():
                    path.chmod(0o555 if path.is_dir() else 0o444)
            before = snapshot(submission)
            commands = layout / 'commands'
            commands.mkdir()
            events = layout / 'events.log'
            def executable(name, source):
                path = commands / name
                path.write_text(source)
                path.chmod(0o755)
            for name in ['killall','chown','npm','dos2unix']:
                executable(name,'#!/bin/sh\nexit 0\n')
            # Services must really listen: the runners wait for that (T15).
            executable('node',listener_source())
            executable('listener.py',listener_source())
            executable('sleep','#!/bin/sh\n/bin/sleep 0.2\n')
            executable('sudo','#!/bin/sh\nif [ "$1" = chown ]; then exit 0; fi\nexec "$@"\n')
            executable('make',f'#!{sys.executable}\n'+r'''
import os,sys
from pathlib import Path
with open(os.environ['EVENTS'],'a') as output: output.write('make:'+','.join(sys.argv[1:])+'\n')
if 'clean' in sys.argv:
    for name in ['client','server','secret']: Path(name).unlink(missing_ok=True)
    sys.exit(0)
import shutil
shutil.copy(os.environ['TEST_LISTENER'],'server'); Path('server').chmod(0o755)
p=Path('client'); p.write_text('#!/bin/sh\nexit 0\n'); p.chmod(0o755)
Path('web/uploadf.html').write_text('changed only in work copy\n')
''')
            executable('python',f'#!{sys.executable}\n'+r'''
import ast,csv,os,runpy,sys
from pathlib import Path
script_path=sys.argv[1];script=Path(script_path).name;args=sys.argv[2:]
if script=='bonus-check.py': sys.exit(0)
if sys.argv[1]=='-c': sys.exit(1)
if script=='web.py': os.execv(sys.executable,[sys.executable,os.environ['TEST_LISTENER'],'web.py'])
if script=='service_readiness.py':
    sys.argv=[script_path]+args;runpy.run_path(script_path,run_name='__main__')
if script=='check-results.py':
    sys.argv=[script_path]+args;runpy.run_path(script_path,run_name='__main__');sys.exit(0)
with open(os.environ['EVENTS'],'a') as output: output.write(script+'\n')
if script=='structure-checker.py':
    if os.environ['STRUCTURE_MODE'] in ('crash','stale'): sys.exit(1)
    sys.path.insert(0,str(Path(script_path).resolve().parent))
    if os.environ['STRUCTURE_MODE']=='fault':
        from assets import submission_structure
        submission_structure.os.scandir=lambda *a: (_ for _ in ()).throw(PermissionError('injected checker IO failure'))
    sys.argv=[script_path]+args;runpy.run_path(script_path,run_name='__main__');sys.exit(0)
late=int(os.environ['LATE_STATUS'])
if os.environ['RUNNER_KIND']=='public':
    sys.exit(late if script=='client-0.py' and args[0]=='2024' else 0)
options=dict(zip(args[::2],args[1::2]))
tree=ast.parse(Path(script_path).read_text())
begins=sorted((n for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=='testcase_begin'),key=lambda n:n.lineno)
status=late if script=='client-5-command-mix-closeeverytime.py' else 0
with (Path(options['--output-dir'])/(Path(script).stem+'-'+options['--id']+'.csv')).open('w',newline='') as output:
    writer=csv.writer(output);writer.writerow(['name','expect','score','got','result','comment'])
    for node in begins:
        name,score=ast.literal_eval(node.args[0]),ast.literal_eval(node.args[1])
        writer.writerow([name,'',score,'' if status==2 else 0 if status==1 else score,'incomplete' if status==2 else 'failed' if status==1 else 'pass','injected late failure' if status else ''])
sys.exit(status)
''')
            env=dict(os.environ,PATH=str(commands)+os.pathsep+os.environ['PATH'],EVENTS=str(events),
                     TEST_PID_DIR=str(pids),TEST_LISTENER=str(commands/'listener.py'),
                     STRUCTURE_MODE=mode,LATE_STATUS=str(late_status),RUNNER_KIND='formal' if FORMAL else 'public',
                     PYTHONDONTWRITEBYTECODE='1')
            source=(ROOT/'run.sh').read_text()
            output=layout/'output'
            output.mkdir()
            if FORMAL:
                mounts=layout/'mnt';mounts.mkdir()
                (mounts/'hw2').symlink_to(hw2,target_is_directory=True)
                (mounts/'submission').symlink_to(submission,target_is_directory=True)
                (mounts/'output').symlink_to(output,target_is_directory=True)
                shutil.copytree(ROOT/'scripts',mounts/'judger/scripts')
                source=re.sub(r'/judge(?=/|\b)',str(layout/'judge'),source)
                source=source.replace('/mnt/',str(mounts)+'/')
                workdir=layout
                args=['b00000000']
            else:
                workdir=layout/'prejudge';workdir.mkdir()
                shutil.copytree(ROOT/'scripts',workdir/'scripts')
                args=[str(submission),str(output)]
            if mode=='stale':
                (output/('structure-b00000000.json' if FORMAL else 'structure.json')).write_text('{"status":"pass","old_run":true}')
                executable('rm',f'#!{sys.executable}\n'+r"""
import os,sys
if any(arg.endswith(('structure.json','structure-b00000000.json')) for arg in sys.argv[1:]):
    sys.exit(1)  # Reproduce inability to invalidate the previous report.
os.execv('/bin/rm',['rm']+sys.argv[1:])
""")
            runner=workdir/'run.sh';runner.write_text(source)
            result=subprocess.run(['bash',str(runner)]+args,cwd=layout,env=env,text=True,capture_output=True,timeout=20)
            expected=max(2 if mode in ('fault','crash','stale') else 1 if mode=='review' else 0,late_status)
            self.assertEqual(result.returncode,expected,result.stdout+result.stderr)
            report_file=output/('structure-b00000000.json' if FORMAL else 'structure.json')
            if mode in ('crash','stale'):
                if mode=='stale':
                    self.assertEqual(json.loads(report_file.read_text()),{'status':'pass','old_run':True})
                else:
                    self.assertFalse(report_file.exists())
                report=None
            else:
                report=json.loads(report_file.read_text())
                self.assertEqual(report['status'],{'pass':'pass','review':'needs_review','fault':'incomplete'}[mode])
                self.assertEqual(report['automatic_deduction'],0)
            called=events.read_text().splitlines()
            self.assertEqual(called[0],'structure-checker.py')
            self.assertTrue(any(call.startswith('make:') for call in called[1:]))
            if FORMAL:
                functional=[call for call in called if call.endswith('.py') and call!='structure-checker.py']
                self.assertEqual(len(functional),13)
                self.assertEqual(functional[-1],'client-5-command-mix-closeeverytime.py')
                rows=[]
                for path in output.glob('*.csv'):
                    with path.open() as src: rows.extend(csv.DictReader(src))
                self.assertEqual(len(rows),61)  # incl. three 0-point T32 rows
                self.assertEqual(sum(int(row['got']) for row in rows if row['got']),85 if not late_status else 84)
            else:
                self.assertEqual([call for call in called if call.endswith('.py')],
                    ['structure-checker.py','server-0.py','server-1.py','client-0.py','client-1.py','client-0.py'])
                self.assertIn('-----summary-----',result.stdout)
                self.assertEqual(called[1],'make:clean')
            if mode=='review':
                self.assertIn('hw2/client',[issue['path'] for issue in report['violations']])
                self.assertTrue((hw2/'client').exists())
            self.assertEqual(snapshot(submission),before)
            # Restore only owned fixture modes so temporary cleanup is reliable.
            for path in submission.rglob('*'):
                if not path.is_symlink(): path.chmod(0o755 if path.is_dir() else 0o644)

    def test_clean_submission_continues_all_functional_tests(self):
        self.exercise('pass')

    def test_structure_review_does_not_deduct_or_stop_functional_tests(self):
        self.exercise('review')

    def test_checker_io_failure_still_runs_all_functional_tests(self):
        self.exercise('fault')

    def test_checker_crash_without_report_is_incomplete_and_continues(self):
        self.exercise('crash')

    def test_old_report_cannot_hide_cleanup_and_checker_failure(self):
        self.exercise('stale')

    def test_late_functional_incomplete_overrides_structure_review(self):
        self.exercise('review',2)


if __name__=='__main__':
    unittest.main()
