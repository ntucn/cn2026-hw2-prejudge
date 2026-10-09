"""Owned service stand-ins for runner tests (T15).

The runners now wait until a started process listens, so stand-ins for the
student server and the pseudo-servers really listen. Each records its PID so
the test can stop exactly the processes it caused, and exits on its own after
LIFETIME seconds if a test is interrupted.
"""

import os
from pathlib import Path
import signal
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from assets.service_readiness import ObservationError, listeners  # noqa: E402

LIFETIME = 120
FORMAL_PORTS = [8001, 8002, 8003, 8004, 8005, 8006, 8007, 9001, 9002, 9006]
PUBLIC_PORTS = [8080, 7777, 4500, 2024]

# argv: ./server PORT, node app.js [PORT], python web.py. TEST_LISTEN_MODE:
# listen (default), exit (exit 3 at once) or idle (stay alive, never listen).
LISTENER = r'''
import os, signal, socket, sys, time
from pathlib import Path
name = Path(sys.argv[0]).name
if '-e' in sys.argv:
    # `node -e "require(...)"`: are the pseudo-server modules preinstalled (T23)?
    sys.exit(int(os.environ.get('TEST_NODE_MODULES', '1')))
args = [arg for arg in sys.argv[1:] if arg.isdigit()]
port = int(args[0]) if args else {'web.py': 2024}.get(Path(sys.argv[-1]).name, 4500)
modes = dict(item.split('=') for item in os.environ.get('TEST_LISTEN_MODES', '').split(',') if item)
mode = modes.get(str(port), os.environ.get('TEST_LISTEN_MODE', 'listen'))
Path(os.environ['TEST_PID_DIR'], str(os.getpid())).write_text(f'{name} {port}\n')
if mode == 'exit':
    sys.exit(3)
signal.alarm(LIFETIME)
if mode == 'listen':
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(('', port))
    server.listen(16)
while True:
    time.sleep(1)
'''.replace('LIFETIME', str(LIFETIME))


def listener_source(python=sys.executable):
    return f'#!{python}\n' + LISTENER


def busy_ports(ports):
    """Ports someone else already listens on; tests skip rather than misjudge."""
    try:
        return [port for port in ports if listeners(port)]
    except ObservationError as error:
        return [f'unobservable: {error}']


def stop_recorded(pid_dir, extra_pid_files=()):
    """SIGKILL each recorded stand-in, then confirm it is gone."""
    pids = {int(path.name) for path in Path(pid_dir).iterdir() if path.name.isdigit()}
    for path in extra_pid_files:
        try:
            pids.add(int(Path(path).read_text().split()[0]))
        except (OSError, ValueError, IndexError):
            pass
    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + 5
    for pid in pids:
        while time.monotonic() < deadline:
            try:
                state = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[0]
            except (FileNotFoundError, ProcessLookupError):
                break
            if state in ('Z', 'X'):
                break
            time.sleep(.02)
