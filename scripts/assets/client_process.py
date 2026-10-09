"""Interactive client transport with independently checked stdout and stderr.

stdin/stdout share a raw PTY, preserving the terminal behaviour of the old
runner. stderr has its own pipe. Both outputs are read concurrently so an
error line can arrive before or after the next stdout prompt.
"""

import errno
import os
import pty
import selectors
import shlex
import subprocess
import time
import tty


class ClientOutputError(AssertionError):
    pass


class ClientProcess:
    PROMPT = b'> '

    def __init__(self, command, cwd='.'):
        master, slave = pty.openpty()
        self._selector = selectors.DefaultSelector()
        self._buffers = {'stdout': b'', 'stderr': b''}
        self._prompt_ready = False
        self._closed = False
        try:
            tty.setraw(slave)
            args = shlex.split(command) if isinstance(command, str) else command
            self.proc = subprocess.Popen(args, cwd=cwd, stdin=slave,
                                         stdout=slave, stderr=subprocess.PIPE)
        except BaseException:
            os.close(master)
            self._selector.close()
            raise
        finally:
            os.close(slave)
        self._master = master
        for fd, stream in [(master, 'stdout'), (self.proc.stderr.fileno(), 'stderr')]:
            os.set_blocking(fd, False)
            self._selector.register(fd, selectors.EVENT_READ, stream)

    def _read(self, timeout):
        events = self._selector.select(max(0, timeout))
        for key, _ in events:
            try:
                chunk = os.read(key.fd, 65536)
            except BlockingIOError:
                continue
            except OSError as error:
                # A Linux PTY signals EOF with EIO after its slave closes.
                if key.data != 'stdout' or error.errno != errno.EIO:
                    raise
                chunk = b''
            if not chunk:
                self._selector.unregister(key.fd)
            else:
                self._buffers[key.data] += chunk
        return bool(events)

    def _drain(self):
        # Recheck both FDs after the first read, but keep each batch bounded so
        # continuous output cannot prevent the caller from checking its deadline.
        self._read(0)
        self._read(0)

    def _has_prompt(self):
        data = self._buffers['stdout']
        return data == self.PROMPT or data.endswith(b'\n' + self.PROMPT)

    def _take(self, prompt):
        data = self._buffers.copy()
        if prompt:
            data['stdout'] = data['stdout'][:-len(self.PROMPT)]
        self._buffers = {'stdout': b'', 'stderr': b''}
        self._prompt_ready = prompt
        return data

    @staticmethod
    def _description(data):
        return ', '.join(f'{stream}={value[:512]!r}' for stream, value in data.items())

    def _await_prompt(self, deadline):
        if self._prompt_ready:
            return
        while True:
            self._drain()
            if self._has_prompt():
                data = self._take(prompt=True)
                if any(data.values()):
                    raise ClientOutputError('unexpected output before stdout prompt: ' +
                                            self._description(data))
                return
            if not self._selector.get_map():
                data = self._take(prompt=False)
                if any(data.values()):
                    raise ClientOutputError('expected stdout prompt; ' + self._description(data))
                raise EOFError('client closed before the next stdout prompt')
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ClientOutputError('timed out waiting for stdout prompt; ' +
                                        self._description(self._buffers))
            self._read(remaining)

    @classmethod
    def _reply(cls, data, stream):
        other = 'stderr' if stream == 'stdout' else 'stdout'
        if data[other]:
            raise ClientOutputError(f'expected reply only on {stream}, unexpected {other}; ' +
                                    cls._description(data))
        reply = data[stream]
        if not reply.endswith(b'\n') or reply.count(b'\n') != 1:
            raise ClientOutputError(f'expected one complete reply line on {stream}; ' +
                                    cls._description(data))
        return reply

    def command(self, command, timeout=5, stream='stdout'):
        """Return the reply; consume the next prompt even on a stream violation.

        A single monotonic deadline covers the prompt, command and reply.
        EOFError is reserved for a client that has already closed; output errors
        carry both stream contents and leave later commands runnable when a
        next prompt is available.
        """
        if stream not in self._buffers:
            raise ValueError(f'unknown output stream: {stream}')
        if self._closed:
            raise EOFError('client transport is closed')
        deadline = time.monotonic() + timeout
        self._await_prompt(deadline)
        self._prompt_ready = False
        try:
            os.write(self._master, command.encode() + b'\n')
        except OSError as error:
            raise EOFError('client closed while sending command') from error
        while True:
            self._drain()
            prompt = self._has_prompt()
            ended = not self._selector.get_map()
            # A stdout prompt can precede the stderr reply. Wait for that reply
            # rather than treating an empty stderr buffer as an immediate error.
            data = self._buffers.copy()
            if prompt:
                data['stdout'] = data['stdout'][:-len(self.PROMPT)]
            other = 'stderr' if stream == 'stdout' else 'stdout'
            if ended or (prompt and (data[other] or b'\n' in data[stream])):
                return self._reply(self._take(prompt), stream)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                data = self._take(prompt)
                raise ClientOutputError(f'timed out waiting for {stream} reply and next stdout prompt; ' +
                                        self._description(data))
            self._read(remaining)

    def startup_usage(self, timeout=5, expected_exit=255):
        """Require stderr usage and return -1 (POSIX exit status 255)."""
        deadline = time.monotonic() + timeout
        while self._selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ClientOutputError('timed out waiting for startup usage and exit; ' +
                                        self._description(self._buffers))
            self._read(remaining)
        reply = self._reply(self._take(prompt=False), 'stderr')
        try:
            code = self.proc.wait(timeout=max(0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired as error:
            raise ClientOutputError('client did not exit after startup usage') from error
        if code != expected_exit:
            raise ClientOutputError(f'expected startup exit {expected_exit}, received {code}')
        return reply

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc.wait()
        self._selector.close()
        os.close(self._master)
        self.proc.stderr.close()
