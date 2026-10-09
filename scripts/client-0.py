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
    genFile(f'{REPO}/hw2/client.bin', size='1M')
    genPath(f'assets/pseudo-server/files-{SERV}')
    genFile(f'assets/pseudo-server/files-{SERV}/server.bin', size='1M')

    def startup_usage():
        for arguments in [[], ['localhost'], ['localhost', str(PORT), 'demo:123', 'extra']]:
            c = ClientProcess(['./client', *arguments], cwd=f'{REPO}/hw2')
            try:
                assert c.startup_usage(timeout=TIMEOUT).decode() == 'Usage: ./client [host] [port] [username:password]\n'
            finally:
                c.close()

    c = None
    def start_client():
        global c
        c = ClientProcess(f'./client localhost {PORT} demo:12345', cwd=f'{REPO}/hw2')

    def successful_put():
        sendCMD(c, 'put client.bin', 'Command succeeded.\n')
        cmpFile(f'{REPO}/hw2/client.bin', f'assets/pseudo-server/files-{SERV}/client.bin')

    def missing_put():
        name = missingFilename(f'{REPO}/hw2')
        sendCMD(c, f'put {name}', 'Command failed. File not found on local.\n', stream='stderr')

    def successful_get():
        sendCMD(c, 'get server.bin', 'Command succeeded.\n')
        cmpFile(f'assets/pseudo-server/files-{SERV}/server.bin', f'{REPO}/hw2/files/server.bin')

    def missing_get():
        name = missingFilename(f'assets/pseudo-server/files-{SERV}', f'{REPO}/hw2/files')
        sendCMD(c, f'get {name}', 'Command failed. File not found on server.\n', stream='stderr')

    def quit_and_close():
        sendCMD(c, 'quit', 'Bye.\n')
        try:
            sendCMD(c, 'quit', 'Bye.\n')
        except EOFError:
            print('Client close.')
        else:
            raise AssertionError('client did not exit after quit')

    checks.run('startup usage', startup_usage)
    checks.run('start client', start_client)
    checks.run('put usage', lambda: sendCMD(c, 'put', 'Usage: put [file]\n', stream='stderr'))
    checks.run('wrong credentials', lambda: sendCMD(c, 'put client.bin', 'Command failed. Invalid user or wrong password.\n', stream='stderr'),
               fixtures=[f'{REPO}/hw2/client.bin'])
    checks.run('auth usage', lambda: sendCMD(c, 'auth', 'Usage: auth [username:password]\n', stream='stderr'))
    checks.run('auth update', lambda: sendCMD(c, 'auth demo:123', 'Command succeeded.\n'))
    checks.run('put file', successful_put, fixtures=[f'{REPO}/hw2/client.bin'])
    checks.run('put missing file', missing_put)
    checks.run('putv usage', lambda: sendCMD(c, 'putv', 'Usage: putv [file]\n', stream='stderr'))
    checks.run('get usage', lambda: sendCMD(c, 'get', 'Usage: get [file]\n', stream='stderr'))
    checks.run('get file', successful_get, fixtures=[f'assets/pseudo-server/files-{SERV}/server.bin'])
    checks.run('get missing file', missing_get)
    checks.run('unknown command with spaces', lambda: sendCMD(c, 'fake command', 'Command Not Found.\n', stream='stderr'))
    checks.run('unknown command', lambda: sendCMD(c, 'fakecommand', 'Command Not Found.\n', stream='stderr'))
    checks.run('quit', quit_and_close)
    if c is not None:
        c.close()
    delFile(f'{REPO}/hw2/client.bin')
    delPath(f'{REPO}/hw2/files')
    sys.exit(checks.summary())
