"""Real files and injected IO faults must not become shallow comparison passes."""
import contextlib
import csv
import errno
import filecmp
import importlib
import io
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from assets import comparison
from assets.fixtures import FixtureError
sys.path.remove(str(ROOT / 'scripts'))
FORMAL = (ROOT / 'scripts/pseudo-server').is_dir()


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.reference, self.actual = self.root / 'reference', self.root / 'actual'
        self.reference.write_bytes(b'correct')
        self.actual.write_bytes(b'correct')

    def same_stat(self):
        stamp = self.reference.stat()
        os.utime(self.actual, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))

    def test_same_size_and_mtime_cannot_hide_wrong_bytes(self):
        self.actual.write_bytes(b'WRONG!!')
        self.same_stat()
        self.assertTrue(filecmp.cmp(self.reference, self.actual))  # Reproduce old false positive.
        self.assertFalse(comparison.compare_files(self.reference, self.actual))

    def test_rewrite_with_unchanged_stat_does_not_use_a_stale_cache(self):
        self.same_stat()
        self.assertTrue(comparison.compare_files(self.reference, self.actual))
        self.actual.write_bytes(b'WRONG!!')
        self.same_stat()
        self.assertFalse(comparison.compare_files(self.reference, self.actual))

    def test_empty_binary_and_different_size_files(self):
        for body in (b'', bytes(range(256)), '檔案內容'.encode()):
            with self.subTest(body=body[:10]):
                self.reference.write_bytes(body)
                self.actual.write_bytes(body)
                self.assertTrue(comparison.compare_files(self.reference, self.actual))
                self.actual.write_bytes(body + b'x')
                self.assertFalse(comparison.compare_files(self.reference, self.actual))

    def test_large_files_are_compared_in_bounded_chunks(self):
        body = bytes(range(256)) * 8192
        self.reference.write_bytes(body)
        self.actual.write_bytes(body)
        read_sizes = []
        real_open = open

        class File:
            def __init__(self, path):
                self.source = real_open(path, 'rb')
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.source.close()
            def read(self, size):
                read_sizes.append(size)
                return self.source.read(size)

        with mock.patch.object(comparison, 'open', lambda path, mode: File(path), create=True):
            self.assertTrue(comparison.compare_files(self.reference, self.actual))
        self.assertGreater(len(read_sizes), 4)
        self.assertTrue(all(size == 65536 for size in read_sizes))

    def test_missing_directory_and_fifo_actual_outputs_fail(self):
        self.actual.unlink()
        self.assertFalse(comparison.compare_files(self.reference, self.actual))
        self.actual.mkdir()
        self.assertFalse(comparison.compare_files(self.reference, self.actual))
        self.actual.rmdir()
        if hasattr(os, 'mkfifo'):
            os.mkfifo(self.actual)
            self.assertFalse(comparison.compare_files(self.reference, self.actual))

    def test_missing_or_nonfile_reference_is_a_fixture_fault(self):
        self.reference.unlink()
        with self.assertRaisesRegex(FixtureError, 'reference'):
            comparison.compare_files(self.reference, self.actual)
        self.reference.mkdir()
        with self.assertRaisesRegex(FixtureError, 'reference'):
            comparison.compare_files(self.reference, self.actual)

    def test_open_and_read_io_faults_are_incomplete_for_either_side(self):
        original_open = open
        for path in (self.reference, self.actual):
            def fault(name, mode):
                if Path(name) == path:
                    raise PermissionError('injected open permission failure')
                return original_open(name, mode)
            with self.subTest(path=path), mock.patch.object(comparison, 'open', fault, create=True), \
                    self.assertRaisesRegex(FixtureError, 'cannot read'):
                comparison.compare_files(self.reference, self.actual)
        original_read = comparison._read
        for path in (self.reference, self.actual):
            def read(source, name, reference):
                if Path(name) == path:
                    source = types.SimpleNamespace(read=mock.Mock(side_effect=OSError(errno.EIO, 'injected disk error')))
                return original_read(source, name, reference)
            with self.subTest(path=path), mock.patch.object(comparison, '_read', read), \
                    self.assertRaisesRegex(FixtureError, 'IO error'):
                comparison.compare_files(self.reference, self.actual)

    def trees(self):
        self.reference.unlink()
        self.actual.unlink()
        for root in (self.reference, self.actual):
            (root / 'nested/empty').mkdir(parents=True)
            (root / 'nested/segment.m4s').write_bytes(b'correct')
            (root / '.hidden').write_bytes(b'header')

    def test_recursive_tree_compares_nested_bytes_and_empty_subdirectories(self):
        self.trees()
        self.assertTrue(comparison.compare_folders(self.reference, self.actual, require_files=True))
        (self.actual / 'nested/segment.m4s').write_bytes(b'WRONG!!')
        stamp = (self.reference / 'nested/segment.m4s').stat()
        os.utime(self.actual / 'nested/segment.m4s', ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        self.assertFalse(comparison.compare_folders(self.reference, self.actual, require_files=True))

    def test_missing_extra_hidden_and_file_directory_mismatches_fail(self):
        self.trees()
        (self.actual / '.hidden').unlink()
        self.assertFalse(comparison.compare_folders(self.reference, self.actual))
        (self.actual / '.hidden').write_bytes(b'header')
        (self.actual / 'extra').write_bytes(b'')
        self.assertFalse(comparison.compare_folders(self.reference, self.actual))
        (self.actual / 'extra').unlink()
        (self.actual / 'nested/empty').rmdir()
        (self.actual / 'nested/empty').write_bytes(b'')
        self.assertFalse(comparison.compare_folders(self.reference, self.actual))

    def test_empty_required_reference_cannot_pass_by_vacuous_comparison(self):
        self.reference.unlink()
        self.actual.unlink()
        self.reference.mkdir()
        self.actual.mkdir()
        self.assertTrue(comparison.compare_folders(self.reference, self.actual))
        for nested in (False, True):
            if nested:
                (self.reference / 'empty').mkdir()
                (self.actual / 'empty').mkdir()
            with self.assertRaisesRegex(FixtureError, 'no files'):
                comparison.compare_folders(self.reference, self.actual, require_files=True)

    def test_reference_listing_cannot_be_empty_or_hide_nested_files(self):
        self.trees()
        self.assertEqual(comparison.reference_files(self.reference),
                         [self.reference / '.hidden', self.reference / 'nested/segment.m4s'])
        for path in comparison.reference_files(self.reference):
            path.unlink()
        with self.assertRaisesRegex(FixtureError, 'no files'):
            comparison.reference_files(self.reference)
        os.mkfifo(self.reference / 'not-a-segment')
        with self.assertRaisesRegex(FixtureError, 'not a regular file'):
            comparison.reference_files(self.reference)

    def test_directory_inspection_faults_are_not_content_failures(self):
        self.trees()
        walk = comparison.os.walk
        for path in (self.reference, self.actual):
            def broken(root, onerror, followlinks):
                if Path(root) == path:
                    onerror(PermissionError('injected directory error'))
                yield from walk(root, onerror=onerror, followlinks=followlinks)
            with self.subTest(path=path), mock.patch.object(comparison.os, 'walk', broken), \
                    self.assertRaisesRegex(FixtureError, 'directory IO error'):
                comparison.compare_folders(self.reference, self.actual)


class ComparisonAdapterTests(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(ROOT / 'scripts'))
        self.addCleanup(sys.path.remove, str(ROOT / 'scripts'))
        with mock.patch.dict(sys.modules, {'pwn': types.ModuleType('pwn')}):
            self.utils = importlib.import_module('assets.utils')
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.reference, self.actual = self.root / 'reference', self.root / 'actual'
        self.reference.write_bytes(b'correct')
        self.actual.write_bytes(b'correct')

    def run_cases(self, missing_reference):
        if missing_reference:
            self.reference.unlink()
        else:
            self.actual.unlink()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            if FORMAL:
                g = self.utils.grader(str(self.root / 'result.csv'))
                g.testcase_begin('compare', 1, 'same bytes')
                g.compare_file(self.reference, self.actual)
                g.testcase_end()
                g.testcase_begin('later', 1, 'still executes')
                g.testcase_judge((True, ''))
                g.testcase_end()
                code = g.summary()
                with (self.root / 'result.csv').open() as source:
                    rows = list(csv.DictReader(source))
                self.assertEqual(rows[0]['got'], '' if missing_reference else '0')
                return code, [row['result'] for row in rows]
            checks = self.utils.Checks()
            checks.run('compare', lambda: self.utils.cmpFile(self.reference, self.actual))
            checks.run('later', lambda: None)
            return checks.summary(), [result for _, result in checks.results]

    def test_student_missing_output_fails_and_later_check_runs(self):
        self.assertEqual(self.run_cases(False), (1, ['failed', 'pass']))

    def test_reference_fault_is_incomplete_and_later_check_runs(self):
        self.assertEqual(self.run_cases(True), (2, ['incomplete', 'pass']))

    def test_adapter_rejects_shallow_false_positive_and_reports_io_fault(self):
        self.actual.write_bytes(b'WRONG!!')
        stamp = self.reference.stat()
        os.utime(self.actual, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        if FORMAL:
            self.assertFalse(self.utils.cmpFile(self.reference, self.actual))
        else:
            with self.assertRaises(AssertionError):
                self.utils.cmpFile(self.reference, self.actual)
        with mock.patch.object(comparison, 'open', side_effect=PermissionError('disk unavailable'), create=True):
            with self.assertRaises(FixtureError):
                self.utils.cmpFile(self.reference, self.actual)


if __name__ == '__main__':
    unittest.main()
