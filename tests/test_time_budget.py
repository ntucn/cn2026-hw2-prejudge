"""Each public check limit covers the deadlines inside that check (T16)."""

import re
from pathlib import Path
import unittest

from time_budget_analysis import ScriptBudget

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'scripts'
RUN_SH = (ROOT / 'run.sh').read_text()


def margin(budget):
    return max(30, budget / 10)


class PublicTimeBudgetTests(unittest.TestCase):
    def budgets(self):
        budgets = {name: ScriptBudget(SCRIPTS / name) for name in ['server-0.py', 'server-1.py', 'client-0.py']}
        totals = {name: analysis.total() for name, analysis in budgets.items()}
        for name, analysis in budgets.items():
            self.assertEqual(analysis.unbounded, [], name)
        # client-1 binds each sample's deadline in a loop: put and get for the
        # 100M (100 s), 1M (2 s) and 5M (2 s) samples, then two quit commands (2 s).
        source = (SCRIPTS / 'client-1.py').read_text()
        self.assertIn("('L4RGebUtNoT7o01ArgE', '100M', 100)", source)
        self.assertEqual(source.count(', TIMEOUT)'), 2)
        self.assertEqual(source.count("sendCMD(c, 'quit', 'Bye.\\n')"), 2)
        totals['client-1.py'] = 2 * (100 + 2 + 2) + 2 * 2
        return totals

    def test_known_budgets(self):
        self.assertEqual(self.budgets(), {'server-0.py': 80, 'server-1.py': 220,
                                          'client-0.py': 34, 'client-1.py': 212})

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
        calls = re.findall(r'requests\.(?:get|post)\([^\n]*\)', source)
        self.assertEqual(len(calls), 4)
        self.assertTrue(all('timeout=TIMEOUT' in call for call in calls))


if __name__ == '__main__':
    unittest.main()
