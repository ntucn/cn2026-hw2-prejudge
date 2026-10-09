"""Read-only inspection of a 2026 submission; structure penalties remain manual."""
import argparse
import fnmatch
import json
import os
from pathlib import Path
import stat
import sys
import tempfile


SOURCE_EXTENSIONS = ('.c', '.cpp', '.cc', '.cxx', '.C')
REQUIRED_FILES = (
    ('.gitignore',), ('Dockerfile',),
    ('docker-compose.yml', 'docker-compose.yaml', 'compose.yml', 'compose.yaml'),
    ('hw2/makefile', 'hw2/Makefile'),
    tuple('hw2/server' + suffix for suffix in SOURCE_EXTENSIONS),
    tuple('hw2/client' + suffix for suffix in SOURCE_EXTENSIONS),
    tuple('hw2/utils/base64' + suffix for suffix in SOURCE_EXTENSIONS),
    ('hw2/utils/base64.h',),
) + tuple(('hw2/web/' + name,) for name in (
    'index.html', 'listf.rhtml', 'listv.rhtml', 'player.rhtml', 'uploadf.html', 'uploadv.html',
))
REQUIRED_DIRECTORIES = ('hw2', 'hw2/utils', 'hw2/web')
FORBIDDEN_PATHS = (
    'hw2/client', 'hw2/server', 'hw2/secret', 'hw2/files',
    'hw2/web/files', 'hw2/web/tmp', 'hw2/web/videos',
)
ARTIFACT_PATTERNS = (
    '.vscode', '__pycache__', 'node_modules', '__MACOSX', 'Thumbs.db', '.DS_Store',
    '*.d', '*.slo', '*.lo', '*.o', '*.ko', '*.obj', '*.elf', '*.ilk',
    '*.exp', '*.gch', '*.pch', '*.so', '*.so.*', '*.dylib', '*.dll', '*.lai',
    '*.la', '*.a', '*.lib', '*.exe', '*.out', '*.app', '*.su', '*.idb', '*.pdb',
    '*~', '*.swp', '*.swo', '*.swx', '*.pyc', '*.pyo', '*.class',
    '*.mp4', '*.m4s', '*.mpd', '*.mkv', '*.webm', '*.avi', '*.mov', '*.m4v', '*.mpeg', '*.mpg',
    '*.pdf', '*.doc', '*.docx', '*.tmp',
    'report*.md', 'report*.txt', 'report*.tex', '*_hw2.md', '*_hw2.txt', '*_hw2.tex',
)


def compiled_binary(path):
    """Read a bounded header, so renaming an executable cannot hide an artifact."""
    with path.open('rb') as source:
        header = source.read(64)
        if header.startswith(b'\x7fELF') or header[:4] in (
            b'\xfe\xed\xfa\xce', b'\xce\xfa\xed\xfe',
            b'\xfe\xed\xfa\xcf', b'\xcf\xfa\xed\xfe',
            b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca',
        ):
            return True
        if len(header) == 64 and header.startswith(b'MZ'):
            source.seek(int.from_bytes(header[60:64], 'little'))
            return source.read(4) == b'PE\x00\x00'
    return False


def inspect_submission(root):
    root = Path(os.path.abspath(os.fspath(root)))
    result = {
        'format_version': 1, 'spec_year': 2026, 'submission_root': str(root),
        'status': 'pass', 'exit_code': 0, 'manual_review_required': False,
        'automatic_deduction': 0, 'functional_tests': 'continue',
        'violations': [], 'inspection_errors': [], 'inventory': [],
        'scope': 'Filesystem structure only; language and third-party library compliance need source review.',
    }

    def violation(rule, path, message):
        result['violations'].append({'rule': rule, 'path': str(path), 'message': message})

    def error(path, reason):
        result['inspection_errors'].append({'path': str(path), 'message': str(reason)})

    try:
        if not stat.S_ISDIR(root.stat().st_mode):
            raise OSError('submission root is missing or is not a directory')
    except OSError as fault:
        error('.', fault)
        return finish(result)

    pending = [root]

    def visit(directory):
        try:
            with os.scandir(directory) as scan:
                entries = sorted(scan, key=lambda entry: entry.name)
        except OSError as fault:
            error(Path(directory).relative_to(root), fault)
            return
        for entry in entries:
            path = Path(entry.path)
            relative = path.relative_to(root).as_posix()
            try:
                mode = entry.stat(follow_symlinks=False).st_mode
                kind = ('symlink' if stat.S_ISLNK(mode) else 'directory' if stat.S_ISDIR(mode)
                        else 'file' if stat.S_ISREG(mode) else 'special')
                result['inventory'].append({'path': relative, 'kind': kind})
                if relative in FORBIDDEN_PATHS:
                    violation('generated_path', relative, 'Must be created during execution, not included in the submission.')
                if any(fnmatch.fnmatchcase(entry.name.lower(), pattern.lower()) for pattern in ARTIFACT_PATTERNS):
                    violation('artifact', relative, 'Generated file, media, report/document, or development artifact; review against the submission rules.')
                if kind == 'file' and compiled_binary(path):
                    violation('compiled_binary', relative,
                              'Compiled executable/library header found; submit source and build during testing.')
                if kind == 'directory' and entry.name != '.git':
                    pending.append(path)
                elif kind == 'symlink' and path.is_dir():
                    # No undocumented symlink penalty: disclose an inspection limit.
                    error(relative, 'Directory symlink contents were not inspected; manual inspection is needed.')
            except OSError as fault:
                error(relative, fault)

    while pending:
        visit(pending.pop())
    for name in REQUIRED_DIRECTORIES:
        try:
            try:
                present = stat.S_ISDIR((root / name).stat().st_mode)
            except (FileNotFoundError, NotADirectoryError):
                present = False
            if not present:
                violation('required_directory', name, 'Required directory is missing or has the wrong type.')
        except OSError as fault:
            error(name, fault)
    for alternatives in REQUIRED_FILES:
        try:
            found = False
            for name in alternatives:
                try:
                    found = found or stat.S_ISREG((root / name).stat().st_mode)
                except (FileNotFoundError, NotADirectoryError):
                    pass
            if not found:
                violation('required_file', alternatives[0],
                          'Required regular file is missing or has the wrong type; accepted: ' + ', '.join(alternatives))
        except OSError as fault:
            error(alternatives[0], fault)
    return finish(result)


def finish(result):
    result['status'] = ('incomplete' if result['inspection_errors'] else
                        'needs_review' if result['violations'] else 'pass')
    result['exit_code'] = {'pass': 0, 'needs_review': 1, 'incomplete': 2}[result['status']]
    result['manual_review_required'] = bool(result['violations'] or result['inspection_errors'])
    return result


def save_report(result, filename):
    target = Path(filename)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=target.parent,
                                         prefix='.structure-', suffix='.json', delete=False) as output:
            temporary = Path(output.name)
            json.dump(result, output, ensure_ascii=False, indent=2)
            output.write('\n')
        os.replace(temporary, target)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Inspect a 2026 submission before clean/build. No automatic point deduction.')
    parser.add_argument('submission_root', nargs='?', default='.', help='Directory containing hw2, not hw2 itself')
    parser.add_argument('--json-output', help='Save the structure report outside the submission')
    args = parser.parse_args(argv)
    result = inspect_submission(args.submission_root)
    if args.json_output:
        try:
            save_report(result, args.json_output)
        except (OSError, UnicodeError) as fault:
            result['inspection_errors'].append({'path': args.json_output, 'message': f'Cannot save structure report: {fault}'})
            finish(result)
    for issue in result['violations']:
        print(f"REVIEW: {issue['path']}: {issue['message']}")
    for issue in result['inspection_errors']:
        print(f"INCOMPLETE: {issue['path']}: {issue['message']}", file=sys.stderr)
    print(f"Structure: {result['status']}; automatic deduction: 0; continue functional tests.")
    return result['exit_code']
