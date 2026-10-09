import requests
import string
import sys
import re

PORT = 8080
# Every request has a deadline so a stalled server fails instead of hanging.
TIMEOUT = 20

def regex(_content, rexexpfile):
    with open(rexexpfile, 'r') as f:
        _rexexp = f.read()
    
    content = _content.translate({ord(c): None for c in string.whitespace})
    rexexp = _rexexp.translate({ord(c): None for c in string.whitespace})

    reger = re.compile(r'{}'.format(rexexp))
    match = reger.match(content)
    assert(match)
    
    return match.groups()

if __name__ == '__main__':
    if len(sys.argv) >= 2:
        PORT = sys.argv[1]

    req = requests.get(f'http://localhost:{PORT}/', timeout=TIMEOUT)
    assert(req.status_code == 200)
    name, id = regex(req.content.decode(), 'assets/index.html')
    print(f"{name = } {id = }")


    req = requests.post(f'http://localhost:{PORT}/', timeout=TIMEOUT)
    assert(req.status_code == 405)

    req = requests.get(f'http://localhost:{PORT}/hehehe', timeout=TIMEOUT)
    assert(req.status_code == 404)

    req = requests.post(f'http://localhost:{PORT}/heh/', timeout=TIMEOUT)
    assert(req.status_code == 404)

