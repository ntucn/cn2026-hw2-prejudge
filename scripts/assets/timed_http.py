"""HTTP requests with one total deadline (T35).

requests' timeout bounds each socket operation, so a server that keeps
sending a byte now and then never times out. The published limits are totals:
a request must finish within 10 s, plus size / 1 MiB/s for a file transfer.
requests still prepares the message (multipart body, auth, quoting, default
headers); this module sends it and reads the response under the deadline.
Like urllib3, a server that closes while the body is being sent may still
have answered (e.g. 401), so the response is read before giving up. A timer
shuts the socket down at the deadline, so a trickling peer cannot extend any
phase (headers, body, sending).
"""
import http.client
import socket
import threading
import time
from urllib.parse import urlsplit

import requests
from requests.structures import CaseInsensitiveDict

from assets.fixtures import fixtureSize

MIB = 1024 * 1024
MIN_RATE = MIB  # bytes per second (decided 2026-10-13: 1 MiB/s)
CHUNK = 64 * 1024


def transfer_timeout(base, size):
    """Published limit for a transfer: base + size / 1 MiB/s (size as '100M' or bytes)."""
    return base + fixtureSize(size) / MIN_RATE


# T36 (decided 2026-10-13): the pre-judge allows twice every official limit,
# so a pass here leaves room on the TA's machine.
PREJUDGE_FACTOR = 2


def prejudge_limit(base, size=0):
    """Twice the official limit: 2 x (base + size / 1 MiB/s)."""
    return PREJUDGE_FACTOR * transfer_timeout(base, size)


class _Deadline:
    def __init__(self, seconds):
        self.seconds = seconds
        self.end = time.monotonic() + seconds

    def left(self):
        remaining = self.end - time.monotonic()
        if remaining <= 0:
            raise requests.exceptions.Timeout(f'no complete response within {self.seconds:g} s')
        return remaining


def _headers(response):
    # urllib3 joins repeated headers with ', '; keep that for the checks.
    merged = CaseInsensitiveDict()
    for name, value in response.getheaders():
        merged[name] = f'{merged[name]}, {value}' if name in merged else value
    return merged


def request(method, url, timeout, **kwargs):
    """requests.request(method, url, **kwargs) with `timeout` as a total limit."""
    deadline = _Deadline(timeout)
    with requests.Session() as session:
        prepared = session.prepare_request(requests.Request(method, url, **kwargs))
    parts = urlsplit(prepared.url)
    body = prepared.body.encode('latin-1') if isinstance(prepared.body, str) else prepared.body
    if body is not None and not isinstance(body, bytes):
        raise TypeError('timed_http sends only in-memory bodies')
    try:
        sock = socket.create_connection((parts.hostname, parts.port or 80), timeout=deadline.left())
    except (socket.timeout, TimeoutError) as error:
        raise requests.exceptions.ConnectTimeout(str(error)) from error
    except OSError as error:
        raise requests.exceptions.ConnectionError(str(error)) from error
    expired = threading.Event()

    def expire():
        expired.set()
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    timer = threading.Timer(max(0, deadline.end - time.monotonic()), expire)
    timer.daemon = True
    timer.start()
    try:
        connection = http.client.HTTPConnection(parts.hostname, parts.port or 80)
        connection.sock = sock
        connection.putrequest(prepared.method, prepared.path_url, skip_accept_encoding=True)
        for name, value in prepared.headers.items():
            connection.putheader(name, value)
        try:
            sock.settimeout(deadline.left())
            connection.endheaders()
            view = memoryview(body or b'')
            for start in range(0, len(view), CHUNK):
                sock.settimeout(deadline.left())
                sock.sendall(view[start:start + CHUNK])
        except (BrokenPipeError, ConnectionResetError):
            pass  # The server may have answered early; read it below.
        sock.settimeout(deadline.left())
        response = http.client.HTTPResponse(sock, method=prepared.method)
        response.begin()
        chunks = []
        while True:
            sock.settimeout(deadline.left())
            data = response.read1(CHUNK)
            if not data:
                break
            chunks.append(data)
        if expired.is_set():
            # The shutdown may look like the end of a close-delimited body.
            raise requests.exceptions.ReadTimeout(f'no complete response within {timeout:g} s')
        result = requests.models.Response()
        result.status_code = response.status
        result.reason = response.reason
        result.headers = _headers(response)
        result._content = b''.join(chunks)
        result.encoding = requests.utils.get_encoding_from_headers(result.headers)
        result.url = prepared.url
        result.request = prepared
        return result
    except requests.RequestException:
        raise  # Already classified (an OSError subclass, so keep it first).
    except (socket.timeout, TimeoutError) as error:
        raise requests.exceptions.ReadTimeout(f'no complete response within {timeout:g} s') from error
    except (OSError, http.client.HTTPException) as error:
        if expired.is_set():
            raise requests.exceptions.ReadTimeout(f'no complete response within {timeout:g} s') from error
        raise requests.exceptions.ConnectionError(f'{type(error).__name__}: {error}') from error
    finally:
        timer.cancel()
        sock.close()


def get(url, timeout, **kwargs):
    return request('GET', url, timeout, **kwargs)


def post(url, timeout, **kwargs):
    return request('POST', url, timeout, **kwargs)


def put(url, timeout, **kwargs):
    return request('PUT', url, timeout, **kwargs)
