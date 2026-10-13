"""Each public check limit covers the deadlines inside that check (T16/T36)."""

import ast
import re
from pathlib import Path
import sys
import unittest

from time_budget_analysis import ScriptBudget

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'scripts'
RUN_SH = (ROOT / 'run.sh').read_text()
MIB = 1024 * 1024


def margin(budget):
    return max(30, budget / 10)


def size_bytes(size):
    number, unit = re.fullmatch(r'(\d+)([KMG]?)', size).groups()
    return int(number) * 1024 ** ('KMG'.index(unit) + 1 if unit else 0)


def prejudge(base, size='0'):
    """T36: twice the official limit, 2 x (base + size / 1 MiB/s)."""
    return 2 * (base + size_bytes(size) / MIB)


def assigned(tree, name):
    return next(ast.literal_eval(node.value) for node in ast.walk(tree)
                if isinstance(node, ast.Assign) and getattr(node.targets[0], 'id', '') == name)


class PublicTimeBudgetTests(unittest.TestCase):
    def server_one_budget(self):
        # Uploads and the download use 2 x (10 s + size / 1 MiB/s) per file;
        # everything else is charged by the analyzer at TIMEOUT.
        analysis = ScriptBudget(SCRIPTS / 'server-1.py')
        rest = analysis.total(skip_functions=('upload', 'download'))
        self.assertEqual(analysis.unbounded, [])
        sizes = dict(assigned(analysis.tree, 'samples'))
        uploads = next(node.iter for node in ast.walk(analysis.tree)
                       if isinstance(node, ast.For) and isinstance(node.iter, ast.List)
                       and node.iter.elts and isinstance(node.iter.elts[0], ast.Tuple))
        names = [row.elts[0].value for row in uploads.elts]
        self.assertEqual(len(names), 7)
        return rest + sum(prejudge(10, sizes[name]) for name in names) + prejudge(10, sizes['a STaRanG3 F1le'])

    def client_one_budget(self):
        # put and get for each sample, then two quit commands.
        tree = ast.parse((SCRIPTS / 'client-1.py').read_text())
        samples = assigned(tree, 'samples')
        for _, size, limit in samples:
            self.assertEqual(limit, prejudge(3, size), size)
        return sum(2 * limit for _, _, limit in samples) + 2 * prejudge(3)

    def budgets(self):
        budgets = {name: ScriptBudget(SCRIPTS / name) for name in ['server-0.py', 'client-0.py']}
        totals = {name: analysis.total() for name, analysis in budgets.items()}
        for name, analysis in budgets.items():
            self.assertEqual(analysis.unbounded, [], name)
        totals['server-1.py'] = self.server_one_budget()
        totals['client-1.py'] = self.client_one_budget()
        return totals

    def test_scripts_use_twice_the_official_limits(self):
        sys.path.insert(0, str(SCRIPTS))
        try:
            from assets.timed_http import prejudge_limit, PREJUDGE_FACTOR
        finally:
            sys.path.remove(str(SCRIPTS))
        self.assertEqual(PREJUDGE_FACTOR, 2)
        self.assertEqual(prejudge_limit(10, '100M'), 220)
        for name, constants in [('server-0.py', {'TIMEOUT': prejudge(10)}),
                                ('server-1.py', {'TIMEOUT': prejudge(10)}),
                                ('client-0.py', {'TIMEOUT': prejudge(3), 'TRANSFER_1M': prejudge(3, '1M')}),
                                ('client-1.py', {'TIMEOUT': prejudge(3)})]:
            analysis = ScriptBudget(SCRIPTS / name)
            for constant, value in constants.items():
                self.assertEqual(analysis.constants[constant], value, f'{name} {constant}')
        self.assertIn('timeout=limit(sizes[name])', (SCRIPTS / 'server-1.py').read_text())
        self.assertIn('\nBUILD_LIMIT=120\n', RUN_SH)            # 2 x 60 s
        self.assertIn('\nSERVER_READY_LIMIT=20\n', RUN_SH)      # 2 x 10 s

    def test_known_budgets(self):
        actual = {name: round(value, 2) for name, value in self.budgets().items()}
        self.assertEqual(actual, {'server-0.py': 80, 'server-1.py': 1260.01,
                                  'client-0.py': 106, 'client-1.py': 472})

    def test_every_check_limit_covers_its_deadlines(self):
        limits = {}
        for limit, script in re.findall(r'runCheck (\d+) (\S+\.py)', RUN_SH):
            limits[script] = min(limits.get(script, int(limit)), int(limit))
        for name, budget in self.budgets().items():
            with self.subTest(script=name):
                self.assertGreaterEqual(limits[name], budget + margin(budget))

    def test_server_zero_requests_have_deadlines(self):
        # Previously requests without a timeout could wait until the check was killed.
        source = (SCRIPTS / 'server-0.py').read_text()
        calls = re.findall(r'(?:requests|timed_http)\.(?:get|post)\([^\n]*\)', source)
        self.assertEqual(len(calls), 4)
        self.assertTrue(all('timed_http' in call and 'timeout=TIMEOUT' in call for call in calls))


if __name__ == '__main__':
    unittest.main()
