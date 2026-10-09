"""Observe whether started services listen, without connecting to them (T15).

A probe connection could disturb a student server before grading starts, so a
service is ready when the started process, or one of its descendants, holds a
LISTEN TCP socket on its port. The kernel accepts connections from that
moment, even before the program calls accept(). Linux /proc is required.

Student-side outcomes (exited, not_listening) only describe startup; the
runner still lets the functional tests decide. Judge-side outcomes (a port
held by another process, observation errors) mean the results cannot be
attributed to the started program.

    service_readiness.py free PORT...
    service_readiness.py wait --deadline SECONDS [--json PATH] LABEL:PORT:PID...

Both commands print tab-separated rows. Exit 0: all free/ready; 1: some
student-side startup failure (wait) or occupied port (free); 2: judge-side.
"""

import argparse
import ipaddress
import json
import os
from pathlib import Path
import socket
import sys
import time


LISTEN_STATE = '0A'
POLL_SECONDS = .05
STUDENT_SIDE = {'exited', 'not_listening'}


class ObservationError(Exception):
    pass


def _address(hexaddr):
    raw = bytes.fromhex(hexaddr)
    # /proc stores each 32-bit word in host byte order.
    if sys.byteorder == 'little':
        raw = b''.join(raw[i:i + 4][::-1] for i in range(0, len(raw), 4))
    return str(ipaddress.ip_address(raw))


def listeners(port):
    """Return {socket inode: listen address} for LISTEN TCP sockets on port."""
    found = {}
    readable = False
    for table in ('/proc/net/tcp', '/proc/net/tcp6'):
        try:
            lines = Path(table).read_text().splitlines()[1:]
        except FileNotFoundError:
            continue  # tcp6 is absent when IPv6 is disabled.
        except OSError as error:
            raise ObservationError(f'cannot read {table}: {error}') from error
        readable = True
        for line in lines:
            fields = line.split()
            try:
                hexaddr, _, hexport = fields[1].rpartition(':')
                if fields[3] == LISTEN_STATE and int(hexport, 16) == port:
                    found[fields[9]] = _address(hexaddr)
            except (IndexError, ValueError) as error:
                raise ObservationError(f'malformed {table} row: {line!r}') from error
    if not readable:
        raise ObservationError('no /proc/net/tcp table is readable')
    return found


def process_state(pid):
    """Return (state, ppid), or None after the process was reaped."""
    try:
        data = Path(f'/proc/{pid}/stat').read_text()
    except (FileNotFoundError, ProcessLookupError):
        return None
    except OSError as error:
        raise ObservationError(f'cannot read /proc/{pid}/stat: {error}') from error
    # comm may contain spaces or parentheses; the state follows the last ')'.
    try:
        fields = data[data.rindex(')') + 2:].split()
        return fields[0], int(fields[1])
    except (ValueError, IndexError):
        return None  # Read while the process was being torn down.


def descendants(root):
    children = {}
    try:
        entries = [entry.name for entry in os.scandir('/proc') if entry.name.isdigit()]
    except OSError as error:
        raise ObservationError(f'cannot list /proc: {error}') from error
    for name in entries:
        info = process_state(int(name))
        if info is not None:
            children.setdefault(info[1], []).append(int(name))
    tree, stack = [], [root]
    while stack:
        pid = stack.pop()
        tree.append(pid)
        stack.extend(children.get(pid, []))
    return tree


UNREADABLE_GRACE = 2.0
_unreadable_since = {}


def socket_inodes(pids):
    """Return (socket inodes, whether some process could not be inspected yet)."""
    inodes = set()
    skipped = False
    for pid in pids:
        try:
            fds = os.listdir(f'/proc/{pid}/fd')
            _unreadable_since.pop(pid, None)
        except (FileNotFoundError, ProcessLookupError):
            continue  # Exited while being inspected.
        except PermissionError as error:
            # A process that is exiting briefly refuses /proc/PID/fd. Only a
            # live process that stays unreadable is an observation failure.
            first = _unreadable_since.setdefault(pid, time.monotonic())
            if time.monotonic() - first < UNREADABLE_GRACE:
                skipped = True
                continue
            raise ObservationError(f'cannot inspect /proc/{pid}/fd: {error}') from error
        except OSError as error:
            raise ObservationError(f'cannot inspect /proc/{pid}/fd: {error}') from error
        for fd in fds:
            try:
                target = os.readlink(f'/proc/{pid}/fd/{fd}')
            except OSError:
                continue  # Closed while being inspected.
            if target.startswith('socket:[') and target.endswith(']'):
                inodes.add(target[8:-1])
    return inodes, skipped


def observe(port, pid):
    """Classify one service now; return (status, detail) or None if pending."""
    state = process_state(pid)
    alive = state is not None and state[0] not in {'Z', 'X'}
    sockets = listeners(port)
    if alive:
        inodes, skipped = socket_inodes(descendants(pid))
        owned = set(sockets) & inodes
        if owned:
            return 'ready', 'listening on ' + ', '.join(sorted({sockets[i] for i in owned}))
        if skipped:
            return None  # Ownership unknown this round; look again.
    if sockets:
        # Ports are checked free before start, so another listener means the
        # results could come from a different program.
        return 'foreign_listener', ('port held by a process outside the started process tree: '
                                    + ', '.join(sorted(set(sockets.values()))))
    if not alive:
        return 'exited', 'process exited before listening'
    return None


def wait(services, deadline_seconds):
    deadline = time.monotonic() + deadline_seconds
    started = time.monotonic()
    results = {}
    while True:
        for label, port, pid in services:
            if label not in results:
                outcome = observe(port, pid)
                if outcome is not None:
                    results[label] = outcome + (round(time.monotonic() - started, 3),)
        if len(results) == len(services) or time.monotonic() >= deadline:
            break
        time.sleep(POLL_SECONDS)
    for label, port, pid in services:
        if label not in results:
            results[label] = ('not_listening', f'no LISTEN socket on port {port} '
                              f'within {deadline_seconds:g} s', round(time.monotonic() - started, 3))
    return results


def service(text):
    label, port, pid = text.rsplit(':', 2)
    if not label or '\t' in label:
        raise argparse.ArgumentTypeError(f'invalid label in {text!r}')
    return label, int(port), int(pid)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    commands = parser.add_subparsers(dest='command', required=True)
    free = commands.add_parser('free')
    free.add_argument('ports', nargs='+', type=int)
    waiting = commands.add_parser('wait')
    waiting.add_argument('--deadline', type=float, required=True)
    waiting.add_argument('--json')
    waiting.add_argument('services', nargs='+', type=service)
    args = parser.parse_args(argv)
    try:
        if args.command == 'free':
            code = 0
            for port in args.ports:
                sockets = listeners(port)
                status = 'occupied' if sockets else 'free'
                print(f'{port}\t{status}\t{", ".join(sorted(set(sockets.values())))}')
                code = max(code, int(bool(sockets)))
            return code
        results = wait(args.services, args.deadline)
    except ObservationError as error:
        print(f'OBSERVATION ERROR: {error}', file=sys.stderr)
        return 2
    code = 0
    for label, _, _ in args.services:
        status, detail, elapsed = results[label]
        print(f'{label}\t{status}\t{detail}')
        code = max(code, 1 if status in STUDENT_SIDE else 0 if status == 'ready' else 2)
    if args.json:
        evidence = {'deadline_seconds': args.deadline, 'host': socket.gethostname(),
                    'services': [{'label': label, 'port': port, 'pid': pid,
                                  'status': results[label][0], 'detail': results[label][1],
                                  'elapsed_seconds': results[label][2]}
                                 for label, port, pid in args.services]}
        try:
            temporary = Path(args.json + '.tmp')
            temporary.write_text(json.dumps(evidence, indent=2) + '\n')
            temporary.replace(args.json)
        except OSError as error:
            print(f'OBSERVATION ERROR: cannot save {args.json}: {error}', file=sys.stderr)
            return 2
    return code


if __name__ == '__main__':
    sys.exit(main())
