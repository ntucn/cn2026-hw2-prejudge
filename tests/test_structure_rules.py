"""T12 rule coverage without guessing source/library compliance or penalties."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT=Path(__file__).resolve().parents[1]


class StructureRuleTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        spec=importlib.util.spec_from_file_location('structure_rules',ROOT/'scripts/assets/submission_structure.py')
        self.checker=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.checker)
        for name in ['.gitignore','Dockerfile','docker-compose.yml','hw2/makefile','hw2/client.c','hw2/server.c',
                     'hw2/utils/base64.c','hw2/utils/base64.h','hw2/web/index.html','hw2/web/listf.rhtml',
                     'hw2/web/listv.rhtml','hw2/web/player.rhtml','hw2/web/uploadf.html','hw2/web/uploadv.html']:
            self.write(name,b'source or configuration\n')

    def write(self,name,content=b'fixture'):
        path=self.root/name
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(content)
        return path

    def inspect(self):
        return self.checker.inspect_submission(self.root)

    def test_every_required_file_is_checked_without_rejecting_other_files(self):
        self.assertEqual(self.inspect()['status'],'pass')
        for alternatives in self.checker.REQUIRED_FILES:
            path=self.root/alternatives[0]
            content=path.read_bytes()
            path.unlink()
            with self.subTest(path=path):
                report=self.inspect()
                self.assertEqual(report['status'],'needs_review')
                self.assertTrue(any(issue['path']==alternatives[0] and issue['rule']=='required_file' for issue in report['violations']))
                self.assertEqual(report['automatic_deduction'],0)
            path.write_bytes(content)

    def test_cpp_and_compose_filename_aliases_are_accepted(self):
        (self.root/'docker-compose.yml').rename(self.root/'compose.yaml')
        (self.root/'hw2/makefile').rename(self.root/'hw2/Makefile')
        for suffix in ['.cpp','.cc','.cxx','.C']:
            for stem in ['hw2/client','hw2/server','hw2/utils/base64']:
                (self.root/(stem+'.c')).rename(self.root/(stem+suffix))
            self.assertEqual(self.inspect()['status'],'pass')
            for stem in ['hw2/client','hw2/server','hw2/utils/base64']:
                (self.root/(stem+suffix)).rename(self.root/(stem+'.c'))

    def test_wrong_directory_and_required_file_types_are_reported(self):
        for path in ['hw2/utils','hw2/web']:
            for child in (self.root/path).iterdir(): child.unlink()
            (self.root/path).rmdir()
            (self.root/path).write_bytes(b'not a directory')
        report=self.inspect()
        self.assertEqual(report['status'],'needs_review')
        self.assertEqual({i['path'] for i in report['violations'] if i['rule']=='required_directory'}, {'hw2/utils','hw2/web'})

    def test_nested_reports_media_metadata_and_generated_directories_need_review(self):
        files=['hw2/nested/Student_HW2.PDF','docs/report.md','hw2/videos/clip.MP4','hw2/nested/frame.m4s',
               'hw2/nested/dash.mpd','hw2/nested/file.o','nested/.DS_Store','hw2/nested/other.pyc']
        for name in files: self.write(name)
        directories=['hw2/files','hw2/web/files','hw2/web/tmp','hw2/web/videos','nested/node_modules','nested/__pycache__']
        for name in directories: (self.root/name).mkdir(parents=True,exist_ok=True)
        report=self.inspect()
        self.assertTrue(set(files+directories)<={i['path'] for i in report['violations']})
        self.assertEqual(report['status'],'needs_review')
        self.assertEqual(report['automatic_deduction'],0)

    def test_renamed_compiled_binaries_are_detected_without_flagging_plain_mz_text(self):
        pe=b'MZ'+bytes(58)+(64).to_bytes(4,'little')+b'PE\x00\x00'
        for name,body in [('worker',b'\x7fELF'+bytes(80)),('darwin',b'\xcf\xfa\xed\xfe'+bytes(80)),('windows',pe)]:
            self.write('hw2/nested/'+name,body)
        self.write('hw2/web/plain.txt',b'MZ is just text here')
        report=self.inspect()
        binaries={i['path'] for i in report['violations'] if i['rule']=='compiled_binary'}
        self.assertEqual(binaries,{'hw2/nested/worker','hw2/nested/darwin','hw2/nested/windows'})

    def test_extra_source_and_bonus_assets_are_preserved_and_git_internals_skipped(self):
        for name in ['hw2/utils/helper.cpp','hw2/utils/helper.h','hw2/transcode.sh','hw2/web/progress.js',
                     'hw2/web/progress.css','hw2/web/progress.js.map','hw2/web/icon.svg','hw2/web/icon.png']:
            self.write(name,b'needed source or UI resource')
        self.write('.git/objects/an-object',b'\x7fELF'+bytes(64))
        report=self.inspect()
        self.assertEqual(report['status'],'pass')
        self.assertFalse(any(i['path'].startswith('.git/') for i in report['inventory']))
        self.assertIn('source review',report['scope'])

    def test_binary_inspection_io_failure_is_incomplete_and_other_rules_continue(self):
        self.write('hw2/secret',b'credential')
        original=self.checker.compiled_binary
        def fault(path):
            if path.name=='Dockerfile': raise PermissionError('injected header read fault')
            return original(path)
        with mock.patch.object(self.checker,'compiled_binary',fault): report=self.inspect()
        self.assertEqual(report['status'],'incomplete')
        self.assertIn('hw2/secret',[i['path'] for i in report['violations']])
        self.assertTrue(any('header read fault' in i['message'] for i in report['inspection_errors']))

    def test_directory_symlink_is_an_inspection_limit_without_an_undocumented_penalty(self):
        (self.root/'hw2/link').symlink_to('web',target_is_directory=True)
        report=self.inspect()
        self.assertEqual(report['status'],'incomplete')
        self.assertFalse(any(i['path']=='hw2/link' for i in report['violations']))
        self.assertTrue(any('symlink' in i['message'] for i in report['inspection_errors']))


if __name__=='__main__': unittest.main()
