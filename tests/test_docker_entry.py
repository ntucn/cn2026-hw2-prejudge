"""Host entry for a one-shot pre-judge container, with a docker stand-in (T18)."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class DockerEntryTests(unittest.TestCase):
    def run_entry(self, *args, docker=True, code=0):
        with tempfile.TemporaryDirectory() as directory:
            layout = Path(directory)
            bin_dir = layout / 'bin'
            bin_dir.mkdir()
            if docker:
                stub = bin_dir / 'docker'
                stub.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" > {layout}/args\nexit {code}\n')
                stub.chmod(0o755)
            # Only the tools the entry needs, so a host docker cannot leak in.
            for tool in ['dirname', 'realpath', 'mkdir', 'id']:
                (bin_dir / tool).symlink_to(subprocess.run(['which', tool], capture_output=True,
                                                           text=True).stdout.strip())
            submission = layout / 'repo with spaces'
            (submission / 'hw2').mkdir(parents=True)
            env = dict(os.environ, PATH=str(bin_dir))
            argv = [str(ROOT / 'run-in-docker.sh'), str(submission), *[a.replace('{root}', directory) for a in args]]
            result = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=30)
            recorded = (layout / 'args').read_text().splitlines() if (layout / 'args').exists() else []
            self.output_created = (layout / 'results').is_dir()
            return result, recorded, submission, layout

    def test_submission_is_readonly_and_container_is_one_shot_and_offline(self):
        result, args, submission, layout = self.run_entry('{root}/results')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(args[:4], ['run', '--rm', '--init', '--network'])
        self.assertEqual(args[4], 'none')
        self.assertIn(f'{submission}:/mnt/submission:ro', args)
        self.assertIn(f'{ROOT}:/opt/prejudge:ro', args)
        self.assertIn(f'{layout}/results:/mnt/output', args)
        self.assertEqual(args[-5:], ['cnta/cn-hw2:2026', 'bash', '/opt/prejudge/run.sh', '/mnt/submission', '/mnt/output'])
        self.assertTrue(self.output_created)

    def test_exit_code_is_passed_through_and_output_inside_submission_is_refused(self):
        result, _, _, _ = self.run_entry('{root}/results', code=2)
        self.assertEqual(result.returncode, 2)
        result, args, _, _ = self.run_entry('{root}/repo with spaces/out')
        self.assertEqual((result.returncode, args), (2, []))
        self.assertIn('outside the original submission', result.stderr)

    def test_missing_docker_is_incomplete(self):
        result, _, _, _ = self.run_entry('{root}/results', docker=False)
        self.assertEqual(result.returncode, 2)
        self.assertIn('docker was not found', result.stderr)


if __name__ == '__main__':
    unittest.main()
