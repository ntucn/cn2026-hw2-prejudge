from assets.utils import *
import requests
import string
import sys
import re

PORT = 8080
REPO = '..'
def regex(_content, rexexpfile):
    with open(rexexpfile, 'r') as f:
        _rexexp = f.read()
    
    content = _content.translate({ord(c): None for c in string.whitespace})
    rexexp = _rexexp.translate({ord(c): None for c in string.whitespace})

    reger = re.compile(r'{}'.format(rexexp))
    match = reger.match(content)
    assert(match)
    
    return match.groups()

def parseTable(html):
    table = html[html.find("<tbody>")+7:html.rfind("</tbody>")].strip()

    items = {}
    rows = table.split("<tr><td>")
    for r in rows:
        row = r.strip()
        if row == "":
            continue

        reger = re.compile(r'<a href=\"([\w\W]+)\">([\w\W]+)</a></td></tr>')
        match = reger.match(row)
        href = match.groups()[0]
        text = match.groups()[1]
        
        items[text] = href

    return(items)

def uploadFile(filename):
    return {'upfile': open(filename,'rb')}

def saveFile(content, filename):
    with open(filename, 'wb') as f:
        f.write(content)

if __name__ == '__main__':
    if len(sys.argv) >= 2:
        PORT = sys.argv[1]
    if len(sys.argv) >= 3:
        REPO = sys.argv[2]

    TIMEOUT = 20
    checks = Checks()
    delPath('assets/files')
    delPath('assets/download')
    genPath('assets/files')
    genPath('assets/download')
    # T31: "200MB" means 200 MiB (decided 2026-10-07); test the largest allowed file.
    samples = [('afile', '10M'), ('alargerfile', '100M'),
               ('averylargefile', '200M'), ('a STaRanG3 F1le', '3K')]
    for name, size in samples:
        genFile(f'assets/files/{name}', size=size)

    def homepage():
        req = requests.get(f'http://localhost:{PORT}/', timeout=TIMEOUT)
        assert req.status_code == 200
        name, id = regex(req.content.decode(), 'assets/index.html')
        print(f'{name = } {id = }')

    def upload(name, credentials, expected_status):
        files = uploadFile(f'assets/files/{name}')
        try:
            req = requests.post(f'http://localhost:{PORT}/api/file',
                                files=files, auth=credentials, timeout=TIMEOUT)
        finally:
            files['upfile'].close()
        assert req.status_code == expected_status
        if expected_status == 200:
            cmpFile(f'assets/files/{name}', f'{REPO}/hw2/web/files/{name}')

    def file_list():
        req = requests.get(f'http://localhost:{PORT}/file/', timeout=TIMEOUT)
        assert req.status_code == 200
        regex(req.content.decode(), 'assets/listf.rhtml')
        actual = parseTable(req.content.decode())
        print(f'{actual = }')
        assert actual == {'a STaRanG3 F1le': '/api/file/a%20STaRanG3%20F1le',
                          'afile': '/api/file/afile', 'alargerfile': '/api/file/alargerfile',
                          'averylargefile': '/api/file/averylargefile'}

    def video_list():
        req = requests.get(f'http://localhost:{PORT}/video/', timeout=TIMEOUT)
        assert req.status_code == 200
        regex(req.content.decode(), 'assets/listv.rhtml')
        assert parseTable(req.content.decode()) == {}

    def download():
        req = requests.get(f'http://localhost:{PORT}/api/file/a%20STaRanG3%20F1le', timeout=TIMEOUT)
        assert req.status_code == 200
        saveFile(req.content, 'assets/download/a STaRanG3 F1le')
        cmpFile('assets/files/a STaRanG3 F1le', 'assets/download/a STaRanG3 F1le')

    checks.run('homepage', homepage)
    for name, credentials, status in [
        ('afile', None, 401),
        ('afile', requests.auth.HTTPBasicAuth('m4JorTOM', 'SpAcEoDD1TY'), 200),
        ('alargerfile', None, 401),
        ('alargerfile', requests.auth.HTTPBasicAuth('admin', 'admin'), 401),
        ('alargerfile', requests.auth.HTTPBasicAuth('demo', '123'), 200),
        ('averylargefile', requests.auth.HTTPBasicAuth('demo', '123'), 200),
        ('a STaRanG3 F1le', requests.auth.HTTPBasicAuth('demo', '123'), 200),
    ]:
        checks.run(f'upload {name}: expected {status}',
                   lambda name=name, credentials=credentials, status=status: upload(name, credentials, status),
                   fixtures=[f'assets/files/{name}'])
    checks.run('file list', file_list, fixtures=[f'assets/files/{name}' for name, _ in samples])
    checks.run('empty video list', video_list)
    checks.run('download special filename', download,
               fixtures=['assets/files/a STaRanG3 F1le', 'assets/download'])

    delPath('assets/files')
    delPath('assets/download')
    delPath(f'{REPO}/hw2/web/files')
    delPath(f'{REPO}/hw2/web/videos')
    delPath(f'{REPO}/hw2/web/tmp')
    sys.exit(checks.summary())
