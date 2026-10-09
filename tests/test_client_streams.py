"""Use real subprocess file descriptors, not mocked output streams."""

import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest


DRIVER = Path(__file__).resolve().parents[1] / 'scripts/assets/client_process.py'
spec = importlib.util.spec_from_file_location('client_process_under_test', DRIVER)
driver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(driver)
ClientProcess = driver.ClientProcess
ClientOutputError = driver.ClientOutputError

PROGRAM = r'''
import os, sys, time
mode = sys.argv[1]
def emit(fd, text):
    os.write(fd, text)
if mode.startswith('startup'):
    line = b'Usage: ./client [host] [port] [username:password]\n'
    emit(1 if mode == 'startup-wrong' else 2, line)
    if mode == 'startup-duplicate':
        emit(1, line)
    sys.exit(0 if mode == 'startup-exit-zero' else 255)
prompt_fd = 2 if mode == 'bad-prompt' else 1
emit(prompt_fd, b'> ')
for command in sys.stdin:
    command = command.strip()
    if command == 'quit':
        emit(1, b'Bye.\n')
        break
    elif command in {'usage', 'wrong-usage'}:
        emit(1 if command == 'wrong-usage' else 2, b'Usage: put [file]\n')
    elif command in {'error', 'prompt-before-error', 'prompt-before-split-error'}:
        if command.startswith('prompt-before-'):
            emit(1, b'> ')
            time.sleep(0.025)
        if command == 'prompt-before-split-error':
            emit(2, b'Command failed. File not found')
            time.sleep(0.015)
            emit(2, b' on local.\n')
        else:
            emit(2, b'Command failed. File not found on local.\n')
        if command.startswith('prompt-before-'):
            continue
    elif command == 'unknown':
        emit(2, b'Command Not Found.\n')
    elif command == 'wrong-success':
        emit(2, b'Command succeeded.\n')
    elif command == 'duplicate':
        emit(1, b'Command succeeded.\n')
        emit(2, b'Command succeeded.\n')
    elif command == 'split':
        emit(1, b'Command suc')
        time.sleep(0.015)
        emit(1, b'ceeded.\n')
    elif command == 'extra':
        emit(1, b'Command succeeded.\nextra line\n')
    elif command == 'missing':
        pass
    elif command == 'stall':
        time.sleep(5)
    else:
        emit(1, b'Command succeeded.\n')
    emit(1, b'> ')
'''


class ClientStreamTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.program = self.root / 'student client.py'
        self.program.write_text(PROGRAM)

    def start(self, mode='normal'):
        client = ClientProcess([sys.executable, '-u', str(self.program), mode])
        self.addCleanup(client.close)
        return client

    def test_correct_prompt_success_error_usage_unknown_and_quit(self):
        client = self.start()
        for command, stream, expected in [
            ('usage', 'stderr', b'Usage: put [file]\n'),
            ('success', 'stdout', b'Command succeeded.\n'),
            ('error', 'stderr', b'Command failed. File not found on local.\n'),
            ('unknown', 'stderr', b'Command Not Found.\n'),
            ('quit', 'stdout', b'Bye.\n'),
        ]:
            self.assertEqual(client.command(command, stream=stream, timeout=1), expected)
        with self.assertRaises(EOFError):
            client.command('quit', timeout=0.1)

    def test_error_on_stdout_fails_and_later_commands_still_run(self):
        client = self.start()
        with self.assertRaisesRegex(ClientOutputError, 'unexpected stdout'):
            client.command('wrong-usage', stream='stderr', timeout=1)
        self.assertEqual(client.command('success', timeout=1), b'Command succeeded.\n')
        self.assertEqual(client.command('quit', timeout=1), b'Bye.\n')

    def test_success_on_stderr_fails_and_later_error_still_runs(self):
        client = self.start()
        with self.assertRaisesRegex(ClientOutputError, 'unexpected stderr'):
            client.command('wrong-success', timeout=1)
        self.assertEqual(client.command('usage', stream='stderr', timeout=1), b'Usage: put [file]\n')

    def test_duplicate_output_is_rejected(self):
        client = self.start()
        with self.assertRaisesRegex(ClientOutputError, 'unexpected stderr'):
            client.command('duplicate', timeout=1)
        self.assertEqual(client.command('success', timeout=1), b'Command succeeded.\n')

    def test_extra_lines_are_rejected_without_losing_next_prompt(self):
        client = self.start()
        with self.assertRaisesRegex(ClientOutputError, 'one complete reply line'):
            client.command('extra', timeout=1)
        self.assertEqual(client.command('success', timeout=1), b'Command succeeded.\n')

    def test_split_reply_and_stderr_after_stdout_prompt(self):
        client = self.start()
        self.assertEqual(client.command('split', timeout=1), b'Command succeeded.\n')
        for command in ['prompt-before-error', 'prompt-before-split-error']:
            self.assertEqual(client.command(command, stream='stderr', timeout=1),
                             b'Command failed. File not found on local.\n')
        self.assertEqual(client.command('success', timeout=1), b'Command succeeded.\n')

    def test_prompt_on_stderr_is_rejected(self):
        client = self.start('bad-prompt')
        with self.assertRaisesRegex(ClientOutputError, 'stdout prompt.*stderr='):
            client.command('success', timeout=0.15)

    def test_missing_reply_times_out_but_retains_next_prompt(self):
        client = self.start()
        client.command('success', timeout=1)
        with self.assertRaisesRegex(ClientOutputError, 'timed out'):
            client.command('missing', timeout=0.05)
        self.assertEqual(client.command('success', timeout=1), b'Command succeeded.\n')

    def test_reply_deadline_is_respected(self):
        client = self.start()
        client.command('success', timeout=1)
        started = time.monotonic()
        with self.assertRaisesRegex(ClientOutputError, 'timed out'):
            client.command('stall', timeout=0.05)
        self.assertLess(time.monotonic() - started, 0.5)

    def test_startup_usage_requires_stderr_only(self):
        self.assertEqual(self.start('startup').startup_usage(timeout=1),
                         b'Usage: ./client [host] [port] [username:password]\n')
        for mode in ['startup-wrong', 'startup-duplicate']:
            with self.subTest(mode=mode), self.assertRaisesRegex(ClientOutputError, 'unexpected stdout'):
                self.start(mode).startup_usage(timeout=1)

    def test_startup_usage_rejects_zero_exit_code(self):
        with self.assertRaisesRegex(ClientOutputError, 'expected startup exit 255, received 0'):
            self.start('startup-exit-zero').startup_usage(timeout=1)

    @unittest.skipUnless(shutil.which('gcc'), 'gcc is required for C stdio buffering validation')
    def test_real_c_client_keeps_terminal_buffering_and_separate_stderr(self):
        source = self.root / 'client.c'
        source.write_text(r'''
#include <stdio.h>
#include <unistd.h>
int main(void) {
    char command[128];
    if (!isatty(0) || !isatty(1)) return 2;
    printf("> ");
    while (fgets(command, sizeof(command), stdin)) {
        fprintf(stderr, "Usage: put [file]\n");
        printf("> ");
    }
}
''')
        binary = self.root / 'C client'
        subprocess.run(['gcc', str(source), '-o', str(binary)], check=True, capture_output=True)
        client = ClientProcess([str(binary)])
        self.addCleanup(client.close)
        # Reading terminal stdin flushes the line-buffered stdout prompt, even
        # without explicit fflush. Raw mode prevents echo and CRLF translation.
        for _ in range(2):
            self.assertEqual(client.command('put', stream='stderr', timeout=1), b'Usage: put [file]\n')


if __name__ == '__main__':
    unittest.main()
