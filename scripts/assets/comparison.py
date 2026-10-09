"""Compare actual bytes; the first path is always the judge-owned reference."""
import os
from pathlib import Path
import stat
from assets.fixtures import FixtureError


CHUNK_SIZE = 64 * 1024


def _open_file(path, reference):
    role = 'reference' if reference else 'actual output'
    try:
        mode = Path(path).stat().st_mode
        if not stat.S_ISREG(mode):
            if reference:
                raise FixtureError(f'comparison reference is not a regular file: {path}')
            return None
        return open(path, 'rb')
    except (FileNotFoundError, NotADirectoryError, IsADirectoryError) as error:
        if not reference:
            return None
        raise FixtureError(f'comparison reference unavailable: {path}: {error}') from error
    except OSError as error:
        raise FixtureError(f'comparison cannot read {role}: {path}: {error}') from error


def _read(source, path, reference):
    try:
        return source.read(CHUNK_SIZE)
    except OSError as error:
        role = 'reference' if reference else 'actual output'
        raise FixtureError(f'comparison IO error in {role}: {path}: {error}') from error


def compare_files(reference, actual):
    # Never infer equality from size/mtime or reuse filecmp's cached results.
    with _open_file(reference, True) as expected:
        received = _open_file(actual, False)
        if received is None:
            return False
        with received:
            while True:
                left = _read(expected, reference, True)
                right = _read(received, actual, False)
                if left != right:
                    return False
                if not left:
                    return True


def _inventory(root, reference):
    root = Path(root)
    try:
        if not stat.S_ISDIR(root.stat().st_mode):
            if reference:
                raise FixtureError(f'comparison reference is not a directory: {root}')
            return None
    except (FileNotFoundError, NotADirectoryError) as error:
        if not reference:
            return None
        raise FixtureError(f'comparison reference directory unavailable: {root}: {error}') from error
    except OSError as error:
        raise FixtureError(f'comparison cannot inspect directory: {root}: {error}') from error

    files, directories = set(), set()

    def onerror(error):
        raise FixtureError(f'comparison directory IO error: {root}: {error}') from error

    for directory, dirs, names in os.walk(root, onerror=onerror, followlinks=False):
        relative = Path(directory).relative_to(root)
        directories.update(relative / name for name in dirs)
        files.update(relative / name for name in names)
        # An expected symlink directory would be silently skipped by os.walk.
        if reference and any((Path(directory) / name).is_symlink() for name in dirs):
            raise FixtureError(f'comparison reference contains an untraversed symlink directory: {directory}')
    return files, directories


def reference_files(root):
    """Return a nonempty, recursive list of judge reference files."""
    files, _ = _inventory(root, True)
    if not files:
        raise FixtureError(f'comparison reference has no files: {root}')
    paths = [Path(root) / name for name in sorted(files)]
    for path in paths:
        # Reject unreadable/non-regular fixtures before issuing student requests.
        with _open_file(path, True):
            pass
    return paths


def compare_folders(reference, actual, *, require_files=False):
    expected = _inventory(reference, True)
    if require_files and not expected[0]:
        raise FixtureError(f'comparison reference has no files: {reference}')
    received = _inventory(actual, False)
    if received is None or expected != received:
        return False
    return all(compare_files(Path(reference) / name, Path(actual) / name)
               for name in sorted(expected[0]))
