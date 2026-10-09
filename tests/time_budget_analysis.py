"""Static worst-case time budgets for graded scripts (T16).

Every bounded operation is charged at its full configured deadline, even when
the real code would stop earlier. Branches and exception handlers are summed,
so the result is an upper bound for non-trickling peers. Loops must iterate
over a literal sequence or a declared fixture whose size is known.
"""

import ast
from pathlib import Path


# Defaults of helper calls that do not pass an explicit deadline.
DEFAULT_COSTS = {'command': 5, 'startup_usage': 5, 'prepare_video_fixture': 45}
# conversion_timeout bounds the whole video check, including its requests.
DEADLINE_KEYWORDS = ('conversion_timeout', 'timeout', 'connect_timeout')
HTTP_VERBS = {'get', 'post', 'put', 'delete', 'head', 'patch', 'options', 'request'}
# A 6 s fixture with the published DASH settings: one MPD plus init and two
# media segments for each of the four representations.
DECLARED_LOOPS = {'reference_files(videoFolder_path)': 13}


class BudgetError(ValueError):
    pass


class ScriptBudget:
    def __init__(self, path):
        self.path = Path(path)
        self.tree = ast.parse(self.path.read_text())
        self.constants = {}
        for node in ast.walk(self.tree):
            if (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)
                    and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, (int, float))
                    and not isinstance(node.value.value, bool)):
                self.constants[node.targets[0].id] = node.value.value
        self.functions = {node.name: node for node in ast.walk(self.tree)
                          if isinstance(node, ast.FunctionDef)}
        self.unbounded = []
        self._active = []

    def total(self, skip_functions=()):
        """Cost of the module body; calls to skip_functions are excluded."""
        self.skip = set(skip_functions)
        return self.block(self.tree.body, {})

    # Expressions -----------------------------------------------------
    def value(self, node, env):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.Name):
            if node.id in env:
                return env[node.id]
            if node.id in self.constants:
                return self.constants[node.id]
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Mult, ast.Add)):
            left, right = self.value(node.left, env), self.value(node.right, env)
            if left is not None and right is not None:
                return left * right if isinstance(node.op, ast.Mult) else left + right
        return None

    def calls(self, node, env):
        """Charge calls in evaluation order, without entering nested defs."""
        cost = 0
        if isinstance(node, ast.Lambda):
            # Lambdas here are passed to a checker that calls them once.
            return self.calls(node.body, env)
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            return 0
        for child in ast.iter_child_nodes(node):
            cost += self.calls(child, env)
        if isinstance(node, ast.Call):
            cost += self.call(node, env)
            # A local function passed by name (e.g. checks.run('x', action)) is
            # charged as if called once, like a lambda.
            for arg in [*node.args, *(item.value for item in node.keywords)]:
                if (isinstance(arg, ast.Name) and arg.id in self.functions
                        and arg.id not in self._active and arg.id not in self.skip):
                    cost += self.inline(self.functions[arg.id], ast.Call(func=arg, args=[], keywords=[]), env)
        return cost

    def call(self, node, env):
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, 'id', '')
        line = f'{self.path.name}:{node.lineno}'
        if name in self.skip:
            return 0
        if name in self.functions and name not in self._active:
            return self.inline(self.functions[name], node, env)
        for keyword in DEADLINE_KEYWORDS:
            for item in node.keywords:
                if item.arg == keyword:
                    seconds = self.value(item.value, env)
                    if seconds is None:
                        raise BudgetError(f'{line}: cannot evaluate {keyword}')
                    return seconds
        if name == 'sleep' and node.args:
            seconds = self.value(node.args[0], env)
            if seconds is None:
                raise BudgetError(f'{line}: cannot evaluate sleep')
            return seconds
        if name in DEFAULT_COSTS:
            return DEFAULT_COSTS[name]
        receiver = func.value if isinstance(func, ast.Attribute) else None
        if (name in HTTP_VERBS and isinstance(receiver, ast.Name)
                and receiver.id in {'requests', 'session'}) or name in {'remote', 'httpConnection'}:
            self.unbounded.append(line)
        return 0

    def inline(self, function, node, env):
        params = [arg.arg for arg in function.args.args]
        defaults = function.args.defaults
        bound = {}
        for param, default in zip(params[len(params) - len(defaults):], defaults):
            value = self.value(default, env)
            if value is not None:
                bound[param] = value
        for param, arg in zip(params, node.args):
            value = self.value(arg, env)
            if value is not None:
                bound[param] = value
        for item in node.keywords:
            value = self.value(item.value, env)
            if item.arg in params and value is not None:
                bound[item.arg] = value
        self._active.append(function.name)
        try:
            return self.block(function.body, bound)
        finally:
            self._active.pop()

    # Statements ------------------------------------------------------
    def block(self, statements, env):
        return sum(self.statement(statement, env) for statement in statements)

    def statement(self, node, env):
        if isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.Import, ast.ImportFrom)):
            return 0
        if isinstance(node, ast.For):
            body = self.block(node.body, env)
            count = self.iterations(node.iter) if body else 0
            return self.calls(node.iter, env) + count * body + self.block(node.orelse, env)
        if isinstance(node, ast.While):
            if self.block(node.body, env):
                raise BudgetError(f'{self.path.name}:{node.lineno}: undeclared while loop')
            return 0
        if isinstance(node, ast.If):
            return (self.calls(node.test, env) + self.block(node.body, env)
                    + self.block(node.orelse, env))
        if isinstance(node, ast.Try):
            return (self.block(node.body, env)
                    + sum(self.block(handler.body, env) for handler in node.handlers)
                    + self.block(node.orelse, env) + self.block(node.finalbody, env))
        if isinstance(node, ast.With):
            return (sum(self.calls(item.context_expr, env) for item in node.items)
                    + self.block(node.body, env))
        return self.calls(node, env)

    def iterations(self, node):
        if isinstance(node, (ast.List, ast.Tuple)):
            return len(node.elts)
        source = ast.unparse(node)
        if source in DECLARED_LOOPS:
            return DECLARED_LOOPS[source]
        raise BudgetError(f'{self.path.name}:{node.lineno}: undeclared loop over {source}')
