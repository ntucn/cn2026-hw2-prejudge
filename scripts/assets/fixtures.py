"""Local test data preparation; failures are recorded for dependent checks."""

import os
from pathlib import Path
import random
import re
import string
import sys
import tempfile
import uuid

__all__ = [
    'FixtureError', 'fixtureSize', 'fixtureErrors', 'markFixtureError',
    'genFile', 'genText', 'genPath', 'missingFilename',
]

_errors = {}
_sizes = {}
_directories = set()
_CHUNK_SIZE = 1024 * 1024


class FixtureError(Exception):
    """A judge-side preparation error, rather than a student failure."""


def _key(filename):
    return os.path.abspath(os.fspath(filename))


def markFixtureError(filename, error):
    key = _key(filename)
    message = f'{key}: {error}'
    _errors[key] = message
    print(f'FIXTURE ERROR: {message}', file=sys.stderr, flush=True)
    return False


def fixtureSize(size):
    """Keep the existing dd K/M/G convention (powers of 1024)."""
    if isinstance(size, int) and not isinstance(size, bool) and size >= 0:
        return size
    if isinstance(size, str):
        match = re.fullmatch(r'(\d+)([KMGT]?)', size.upper())
        if match:
            number, unit = match.groups()
            return int(number) * 1024 ** ('KMGT'.index(unit) + 1 if unit else 0)
    raise ValueError(f'invalid fixture size: {size!r}')


def _generate(filename, size, text=False):
    key = _key(filename)
    temporary = None
    try:
        expected = fixtureSize(size)
        # Replace only after a complete write; stale/partial files never count
        # as successfully prepared data for this run.
        with tempfile.NamedTemporaryFile(dir=Path(key).parent, delete=False) as output:
            temporary = output.name
            remaining = expected
            alphabet = string.ascii_letters + string.digits + '\t \n'
            while remaining:
                length = min(remaining, 64 * 1024 if text else _CHUNK_SIZE)
                data = (''.join(random.choices(alphabet, k=length)).encode('ascii')
                        if text else os.urandom(length))
                written = output.write(data)
                if written != length:
                    raise OSError(f'short write: {written}/{length} bytes')
                remaining -= length
        actual = os.path.getsize(temporary)
        if actual != expected:
            raise OSError(f'wrong size: {actual}/{expected} bytes')
        os.replace(temporary, key)
        _errors.pop(key, None)
        _sizes[key] = expected
        return True
    except (OSError, ValueError) as error:
        return markFixtureError(filename, error)
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            except OSError as error:
                markFixtureError(temporary, f'cannot remove temporary file: {error}')


def genFile(filename, size='1M'):
    return _generate(filename, size)


def genText(filename, size=1024):
    return _generate(filename, size, text=True)


def genPath(path):
    key = _key(path)
    try:
        os.makedirs(key, exist_ok=True)
        _directories.add(key)
        _errors.pop(key, None)
        return True
    except OSError as error:
        return markFixtureError(path, error)


def fixtureErrors(paths):
    errors = []
    for filename in paths:
        key = _key(filename)
        if key in _errors:
            errors.append(_errors[key])
            continue
        try:
            path = Path(key)
            if path.is_file():
                actual = path.stat().st_size
                if key in _sizes and actual != _sizes[key]:
                    raise OSError(f'wrong size: {actual}/{_sizes[key]} bytes')
            elif path.is_dir():
                if key not in _directories and not any(path.iterdir()):
                    raise OSError('empty fixture directory')
            else:
                raise OSError('fixture does not exist')
        except OSError as error:
            errors.append(f'{key}: {error}')
    return errors


def missingFilename(*directories, suffix=''):
    """Pick an absent basename without deleting any submitted/server files."""
    if not directories:
        raise FixtureError('missing-file test requires a directory')
    try:
        for _ in range(64):
            name = f'__cn_judge_missing_{uuid.uuid4().hex}{suffix}'
            for directory in directories:
                try:
                    (Path(directory) / name).lstat()
                    break
                except FileNotFoundError:
                    continue
            else:
                return name
    except OSError as error:
        raise FixtureError(f'cannot prepare missing-file test: {error}') from error
    raise FixtureError('cannot find an unused test filename after 64 attempts')
