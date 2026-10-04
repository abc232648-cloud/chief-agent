"""Checkpoint A import direction, including relative and literal dynamic imports."""
import ast
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def imported_modules(source, module, package=False):
    context = module if package else module.rpartition('.')[0]
    result = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            name = '.' * node.level + (node.module or '')
            base = importlib.util.resolve_name(name, context) if node.level else name
            result.add(base)
            result.update(base + '.' + alias.name for alias in node.names if alias.name != '*')
        elif isinstance(node, ast.Call) and node.args:
            name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, 'attr', '')
            if name in {'import_module', '__import__'} and isinstance(node.args[0], ast.Constant):
                target = node.args[0].value
                if isinstance(target, str):
                    result.add(importlib.util.resolve_name(target, context) if target.startswith('.') else target)
    return result


def belongs(module, package):
    return module == package or module.startswith(package + '.')


def source_graph():
    graph = {}
    for path in ROOT.rglob('*.py'):
        relative = path.relative_to(ROOT)
        if relative.parts[0] in {'tests', 'work'} or '__pycache__' in relative.parts:
            continue
        parts = list(relative.with_suffix('').parts)
        package = parts[-1] == '__init__'
        if package:
            parts.pop()
        module = '.'.join(parts)
        graph[module] = imported_modules(path.read_text(encoding='utf-8'), module, package)
    return graph


def find_path(graph, start, forbidden):
    pending = [(start, [start])]
    seen = set()
    while pending:
        module, path = pending.pop()
        if module in seen:
            continue
        seen.add(module)
        if module != start and forbidden(module):
            return path
        for target in graph.get(module, ()):
            pending.append((target, path + [target]))
    return None


def test_chief_core_cannot_reach_domain_implementations_or_composition():
    graph = source_graph()
    domains = ['domains.' + p.name for p in (ROOT / 'domains').iterdir()
               if p.is_dir() and (p / '__init__.py').exists()]
    core_packages = ('agents', 'capabilities', 'control', 'notifications', 'policy', 'security', 'database', 'config', 'gateway', 'operations', 'evidence', 'data_quality', 'decision_ledger', 'model_registry', 'runbooks', 'identity', 'compatibility', 'update_center', 'runtime_qualification')
    core = [m for m in graph if any(belongs(m, p) for p in core_packages)
            or m in {'domains', 'domains.contracts', 'domains.runtime', 'domains.storage'}]
    for module in core:
        path = find_path(graph, module, lambda m: belongs(m, 'application') or any(belongs(m, d) for d in domains))
        assert path is None, 'Forbidden Core dependency: ' + ' -> '.join(path or [])


def test_domains_cannot_reach_other_domains_or_application_wiring():
    graph = source_graph()
    domains = ['domains.' + p.name for p in (ROOT / 'domains').iterdir()
               if p.is_dir() and (p / '__init__.py').exists()]
    for domain in domains:
        for module in (m for m in graph if belongs(m, domain)):
            path = find_path(graph, module, lambda m: belongs(m, 'application') or any(
                other != domain and belongs(m, other) for other in domains))
            assert path is None, 'Forbidden domain dependency: ' + ' -> '.join(path or [])


@pytest.mark.parametrize('source,module,package,expected', [
    ('import domains.jobs', 'control.example', False, 'domains.jobs'),
    ('from domains import farming', 'control.example', False, 'domains.farming'),
    ('from ..jobs import definition', 'domains.farming', True, 'domains.jobs'),
    ('from . import farming', 'domains', True, 'domains.farming'),
    ('importlib.import_module("domains.jobs")', 'control.example', False, 'domains.jobs'),
    ('__import__("domains.farming")', 'control.example', False, 'domains.farming'),
])
def test_import_guard_detects_alternative_import_forms(source, module, package, expected):
    assert expected in imported_modules(source, module, package)


def test_import_guard_detects_transitive_violation():
    graph = {'control.example': {'helper'}, 'helper': {'domains.jobs'}}
    assert find_path(graph, 'control.example', lambda m: belongs(m, 'domains.jobs')) == [
        'control.example', 'helper', 'domains.jobs']
