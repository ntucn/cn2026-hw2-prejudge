from assets.utils import *
import sys

TIMEOUT = 2
PORT = 2025
REPO = '..'
SERV = 'nodejs'

def sendCMD(p, cmd, expect, timeout=TIMEOUT, stream='stdout'):
    recv = p.command(cmd, timeout=timeout, stream=stream)
    print(f'send = {cmd} {stream} = {recv}')
    assert recv.decode() == expect, f'{stream}: {recv!r} != {expect!r}'

if __name__ == '__main__':
    if len(sys.argv) >= 2:
        PORT = sys.argv[1]
    if len(sys.argv) >= 3:
        REPO = sys.argv[2]
    if len(sys.argv) >= 4:
        SERV = sys.argv[3]

    checks = Checks()
    delPath(f'{REPO}/hw2/files')
    # 100 s for 100 MiB as in the official judge; 10 s failed on loaded or emulated machines.
    samples = [('L4RGebUtNoT7o01ArgE', '100M', 100),
               ('a 5 a 5 a a 5 5 5 o o 1 a 1 a a 5 5 5 o o', '1M', TIMEOUT),
               ('iS7H@TL3g4L?', '5M', TIMEOUT)]
    for name, size, _ in samples:
        genFile(f'{REPO}/hw2/{name}', size=size)

    c = None
    def start_client():
        global c
        c = ClientProcess(f'./client localhost {PORT} demo:123', cwd=f'{REPO}/hw2')

    def transfer(name, timeout):
        sendCMD(c, f'put {name}', 'Command succeeded.\n', timeout=timeout)
        cmpFile(f'{REPO}/hw2/{name}', f'assets/pseudo-server/files-{SERV}/{name}')
        sendCMD(c, f'get {name}', 'Command succeeded.\n', timeout=timeout)
        cmpFile(f'{REPO}/hw2/{name}', f'{REPO}/hw2/files/{name}')

    def quit_and_close():
        sendCMD(c, 'quit', 'Bye.\n')
        try:
            sendCMD(c, 'quit', 'Bye.\n')
        except EOFError:
            print('Client close.')
        else:
            raise AssertionError('client did not exit after quit')

    checks.run('start client', start_client)
    for name, _, timeout in samples:
        checks.run(f'put/get {name}', lambda name=name, timeout=timeout: transfer(name, timeout),
                   fixtures=[f'{REPO}/hw2/{name}'])
    checks.run('quit', quit_and_close)
    if c is not None:
        c.close()
    for name, _, _ in samples:
        delFile(f'{REPO}/hw2/{name}')
    delPath(f'{REPO}/hw2/files')
    sys.exit(checks.summary())
