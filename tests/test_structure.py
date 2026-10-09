"""Read-only CLI and IO-fault checks shared by formal and public graders."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


class StructureTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='cn structure ')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / 'submission with spaces'
        (self.root / 'hw2').mkdir(parents=True)
        for name in ['makefile', 'client.c', 'server.c']:
            (self.root / 'hw2' / name).write_text('source\n')
        for name in ['.gitignore', 'Dockerfile', 'docker-compose.yml']:
            (self.root / name).write_text('configuration\n')
        (self.root / 'hw2/utils').mkdir()
        for name in ['base64.c', 'base64.h']:
            (self.root / 'hw2/utils' / name).write_text('helper\n')
        (self.root / 'hw2/web').mkdir()
        for name in ['index.html','listf.rhtml','listv.rhtml','player.rhtml','uploadf.html','uploadv.html']:
            (self.root / 'hw2/web' / name).write_text('template\n')
        self.output = Path(temporary.name) / 'structure.json'
        spec = importlib.util.spec_from_file_location('submission_structure', ROOT / 'scripts/assets/submission_structure.py')
        self.checker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.checker)

    def run_cli(self, root=None, output=None):
        return subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/structure-checker.py'),
                               str(root or self.root), '--json-output', str(output or self.output)],
                              cwd=self.root.parent, capture_output=True, text=True, timeout=5)

    def test_cli_accepts_spaces_and_cpp_aliases_without_changing_submission(self):
        for source in ['client', 'server']:
            (self.root / 'hw2' / (source + '.c')).rename(self.root / 'hw2' / (source + '.cpp'))
        (self.root / 'hw2/makefile').rename(self.root / 'hw2/Makefile')
        before = {str(path.relative_to(self.root)):path.read_bytes() for path in self.root.rglob('*') if path.is_file()}
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(self.output.read_text())
        self.assertEqual((report['status'], report['automatic_deduction'], report['functional_tests']), ('pass', 0, 'continue'))
        self.assertEqual(before, {str(path.relative_to(self.root)):path.read_bytes() for path in self.root.rglob('*') if path.is_file()})

    def test_missing_or_wrong_type_source_is_review_not_an_exception(self):
        (self.root / 'hw2/client.c').unlink()
        (self.root / 'hw2/server.c').unlink()
        (self.root / 'hw2/server.c').mkdir()
        result = self.run_cli()
        self.assertEqual(result.returncode, 1)
        report = json.loads(self.output.read_text())
        self.assertEqual(report['status'], 'needs_review')
        self.assertEqual({issue['path'] for issue in report['violations']}, {'hw2/client.c','hw2/server.c'})
        self.assertTrue(report['manual_review_required'])
        self.assertEqual(report['automatic_deduction'], 0)

    def test_artifacts_are_recorded_before_clean_without_removing_them(self):
        for name in ['hw2/client', 'hw2/server', 'hw2/secret', 'hw2/nested/foo.o', 'nested/.DS_Store']:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'artifact')
        (self.root / 'hw2/web/videos').mkdir(parents=True)
        result = self.run_cli()
        self.assertEqual(result.returncode, 1)
        report = json.loads(self.output.read_text())
        self.assertTrue({'hw2/client','hw2/server','hw2/secret','hw2/nested/foo.o','nested/.DS_Store','hw2/web/videos'} <= {i['path'] for i in report['violations']})
        self.assertTrue((self.root / 'hw2/client').exists())
        self.assertTrue((self.root / 'hw2/web/videos').exists())

    def test_missing_root_and_invalid_argument_are_incomplete(self):
        result = self.run_cli(root=self.root / 'absent')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(self.output.read_text())['status'], 'incomplete')
        result = subprocess.run([sys.executable,'-B',str(ROOT/'scripts/structure-checker.py'),'--bad-option'],capture_output=True,text=True)
        self.assertEqual(result.returncode, 2)
        self.assertNotIn('Traceback',result.stderr)

    def test_scan_io_failure_keeps_other_inspection_running(self):
        (self.root / 'unreadable').mkdir()
        (self.root / 'hw2/secret').write_text('secret')
        scan = self.checker.os.scandir
        def failing(directory):
            if Path(directory).name == 'unreadable':
                raise PermissionError('injected scan error')
            return scan(directory)
        with mock.patch.object(self.checker.os, 'scandir', failing):
            report = self.checker.inspect_submission(self.root)
        self.assertEqual(report['status'], 'incomplete')
        self.assertIn('hw2/secret', [issue['path'] for issue in report['violations']])
        self.assertIn('injected scan error', report['inspection_errors'][0]['message'])

    def test_required_file_stat_io_failure_is_incomplete(self):
        original = Path.stat
        def failing(path, *args, **kwargs):
            if path == self.root / 'hw2/client.c':
                raise PermissionError('injected stat error')
            return original(path, *args, **kwargs)
        with mock.patch.object(Path, 'stat', failing):
            report = self.checker.inspect_submission(self.root)
        self.assertEqual(report['status'], 'incomplete')
        self.assertTrue(any('stat error' in issue['message'] for issue in report['inspection_errors']))

    def test_report_write_failure_is_incomplete_and_cleans_temporary_file(self):
        result = self.run_cli(output=self.root.parent / 'absent/report.json')
        self.assertEqual(result.returncode, 2)
        self.assertIn('Cannot save structure report',result.stderr)
        self.output.write_text('old evidence')
        with mock.patch.object(self.checker.os, 'replace', side_effect=OSError('injected write failure')):
            with self.assertRaises(OSError):
                self.checker.save_report(self.checker.inspect_submission(self.root),self.output)
        self.assertEqual(self.output.read_text(),'old evidence')
        self.assertEqual(list(self.output.parent.glob('.structure-*')),[])


if __name__ == '__main__':
    unittest.main()
