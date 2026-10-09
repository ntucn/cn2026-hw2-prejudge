import shutil
import os
import sys
import traceback
from assets.fixtures import *
from assets.client_process import ClientProcess
from assets.comparison import compare_files

class Checks:
    """Run independent public checks even when preparation or one check fails."""

    def __init__(self):
        self.results = []

    def run(self, name, action, fixtures=()):
        errors = fixtureErrors(fixtures)
        if errors:
            result = 'incomplete'
            print(f'INCOMPLETE: {name}: ' + '; '.join(errors),
                  file=sys.stderr, flush=True)
        else:
            try:
                action()
                result = 'pass'
            except FixtureError as error:
                result = 'incomplete'
                print(f'INCOMPLETE: {name}: {error}', file=sys.stderr, flush=True)
            except Exception:
                result = 'failed'
                traceback.print_exc()
        self.results.append((name, result))
        print(f'{name}: {result}', flush=True)
        return result == 'pass'

    def summary(self):
        print('-----check-summary-----')
        for name, result in self.results:
            print(f'{name}: {result}')
        if any(result == 'incomplete' for _, result in self.results):
            return 2
        return int(any(result == 'failed' for _, result in self.results))

def cmpFile(reference, actual):
    assert compare_files(reference, actual), f'content differs or output missing: {actual}'

def delFile(filename):
    try:
        os.remove(filename)
    except:
        pass
   
def delPath(path): 
    shutil.rmtree(path, ignore_errors=True)
