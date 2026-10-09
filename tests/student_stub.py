"""Write an executable fixture that really emits replies on separate FDs."""

from pathlib import Path
import sys


PROGRAM = r'''
import os, shutil, sys
from pathlib import Path
files = Path(config['files'])
videos = Path(config['videos'])
log = Path(config['log'])
mode = config['mode']
def reply(text, stream):
    if mode == 'errors-on-stdout' and stream == 2:
        stream = 1
    if mode == 'success-on-stderr' and stream == 1:
        stream = 2
    os.write(stream, text.encode())
if len(sys.argv) not in (3, 4) or mode == 'credentials-required' and len(sys.argv) == 3:
    reply('Usage: ./client [host] [port] [username:password]\n', 2)
    sys.exit(0 if mode == 'bad-startup-exit' else 255)
valid_auth = sys.argv[-1] == 'demo:123'
os.write(1, b'> ')
for command in sys.stdin:
    command = command.rstrip('\n')
    with log.open('a') as output:
        output.write(command + '\n')
    verb, _, name = command.partition(' ')
    if verb == 'quit':
        reply('Bye.\n', 1)
        break
    if verb == 'auth':
        if name:
            valid_auth = name == 'demo:123'
            reply('Command succeeded.\n', 1)
        else:
            reply('Usage: auth [username:password]\n', 2)
    elif verb in {'put', 'putv', 'get'}:
        source = files / name if verb == 'get' else Path(name)
        if not name:
            reply(f'Usage: {verb} [file]\n', 2)
        elif verb == 'get' and name in config.get('forced_downloads', []):
            reply('Command failed. Internal server error.\n', 2)
        elif not source.is_file():
            place = 'server' if verb == 'get' else 'local'
            reply(f'Command failed. File not found on {place}.\n', 2)
        elif verb != 'get' and not valid_auth:
            reply('Command failed. Invalid user or wrong password.\n', 2)
        else:
            target = (Path('files') / name if verb == 'get' else
                      (videos if verb == 'putv' else files) / name)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            reply('Command succeeded.\n', 1)
    else:
        reply('Command not found.\n' if mode == 'unknown-lowercase' else 'Command Not Found.\n', 2)
    os.write(1, b'> ')
'''


def write_student(directory, files, videos, log, mode, forced_downloads=()):
    config = {'files': str(files), 'videos': str(videos), 'log': str(log), 'mode': mode, 'forced_downloads': list(forced_downloads)}
    path = Path(directory) / 'client'
    path.write_text(f'#!{sys.executable}\nconfig = {config!r}\n' + PROGRAM)
    path.chmod(0o755)
    return path
