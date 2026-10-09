import contextlib
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from unittest import mock


def load_fixtures():
    path = Path(__file__).resolve().parents[1] / 'scripts/assets/fixtures.py'
    spec = importlib.util.spec_from_file_location('fixture_test_module', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FixtureTests(unittest.TestCase):
    def setUp(self):
        self.fixtures = load_fixtures()
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_exact_sizes_and_literal_filenames(self):
        name = self.root / 'a space 中文 $() " quote.bin'
        self.assertTrue(self.fixtures.genFile(name, '2M'))
        self.assertEqual(name.stat().st_size, 2 * 1024 * 1024)
        self.assertEqual(self.fixtures.fixtureErrors([name]), [])
        text = self.root / 'text.txt'
        self.assertTrue(self.fixtures.genText(text, 1543))
        self.assertEqual(len(text.read_bytes()), 1543)
        text.read_text(encoding='ascii')
        self.assertTrue(self.fixtures.genFile(self.root / 'empty', 0))

    def test_disk_error_preserves_old_file_and_next_generation_runs(self):
        path = self.root / 'stale.bin'
        path.write_bytes(b'old')
        messages = io.StringIO()
        with mock.patch.object(self.fixtures.os, 'urandom', side_effect=OSError('disk full')):
            with contextlib.redirect_stderr(messages):
                self.assertFalse(self.fixtures.genFile(path, '1K'))
        self.assertIn('FIXTURE ERROR', messages.getvalue())
        self.assertEqual(path.read_bytes(), b'old')
        self.assertTrue(self.fixtures.fixtureErrors([path]))
        self.assertEqual(list(self.root.iterdir()), [path])
        other = self.root / 'next.bin'
        self.assertTrue(self.fixtures.genFile(other, '1K'))
        self.assertEqual(other.stat().st_size, 1024)
        self.assertTrue(self.fixtures.genFile(path, '1K'))
        self.assertEqual(self.fixtures.fixtureErrors([path]), [])

    def test_invalid_size_and_missing_parent_are_reported(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertFalse(self.fixtures.genFile(self.root / 'invalid', 'bad'))
            self.assertFalse(self.fixtures.genFile(self.root / 'absent/file', '1K'))
        self.assertFalse((self.root / 'absent').exists())
        self.assertTrue(self.fixtures.genFile(self.root / 'after-error', '1K'))

    def test_missing_name_avoids_collisions_without_deleting_files(self):
        first = self.root / '__cn_judge_missing_taken.mp4'
        first.write_bytes(b'keep')
        other = self.root / 'other'
        other.mkdir()
        with mock.patch.object(self.fixtures.uuid, 'uuid4', side_effect=[
            type('ID', (), {'hex': 'taken'})(), type('ID', (), {'hex': 'free'})(),
        ]):
            name = self.fixtures.missingFilename(self.root, other, suffix='.mp4')
        self.assertEqual(name, '__cn_judge_missing_free.mp4')
        self.assertEqual(first.read_bytes(), b'keep')
        self.assertFalse((self.root / name).exists())
        self.assertFalse((other / name).exists())


if __name__ == '__main__':
    unittest.main()
