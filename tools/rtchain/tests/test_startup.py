"""Check the installed boot contract and the complete generated ordering graph."""
from pathlib import Path
import json
import subprocess
import sys
import unittest

import test_readiness

ROOT = Path(__file__).resolve().parents[1]


def directives(path, section):
    values = {}
    current = None
    for line in path.read_text().splitlines():
        line = line.strip()
        if line.startswith('['):
            current = line
        elif current == f'[{section}]' and '=' in line and not line.startswith('#'):
            key, value = line.split('=', 1)
            if not value:
                values[key] = []
            else:
                values.setdefault(key, []).extend(value.split())
    return values


class StartupTest(unittest.TestCase):
    def test_gate_requires_success_and_excludes_legacy_owner(self):
        gate = directives(ROOT / 'systemd/starter.service.d/70-realtime-ready.conf', 'Unit')
        self.assertIn('rtchain-ready.service', gate['Requires'])
        self.assertIn('rtchain-ready.service', gate['After'])
        self.assertIn('rtchain.service', gate['Conflicts'])
        ready = directives(ROOT / 'systemd/rtchain-ready.service', 'Unit')
        self.assertIn('rtchain.service', ready['Conflicts'])
        service = directives(ROOT / 'systemd/rtchain-ready.service', 'Service')
        self.assertEqual(service['Type'], ['oneshot'])
        self.assertEqual(service['RemainAfterExit'], ['yes'])
        recipe = (ROOT.parents[1] / 'recipes-core/rtchain/fast-rtchain.bb').read_text()
        self.assertIn('ln -s /dev/null ${D}${systemd_unitdir}/system/rtchain.service', recipe)
        self.assertIn('70-realtime-ready.conf', recipe)

    def test_generated_boot_graph_is_acyclic_and_readiness_has_no_tango_dependency(self):
        fixture = test_readiness.ReadinessTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        generated = fixture.root / 'generated'
        fixture.fixture.config_path.write_text(json.dumps(fixture.config))
        result = subprocess.run([sys.executable, str(ROOT / 'rtchain-systemd-generator'),
                                 '--config', str(fixture.fixture.config_path), str(generated)],
                                text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        units = {p.name: directives(p, 'Unit') for p in (ROOT / 'systemd').glob('rtchain-*')
                 if p.suffix in ('.service', '.target')}
        # Expand template instances referenced by the static fleet and generator.
        for unit in list(units.values()):
            for names in unit.values():
                for name in names:
                    if '@' in name and name not in units:
                        template = name.split('@')[0] + '@.service'
                        if template in units:
                            units[name] = {k: [v.replace('%i', name.split('@')[1].split('.')[0])
                                              for v in values] for k, values in units[template].items()}
        units['starter.service'] = {}
        for path in sorted((ROOT / 'systemd/starter.service.d').glob('*.conf')):
            for key, values in directives(path, 'Unit').items():
                units['starter.service'].setdefault(key, []).extend(values)
        for path in generated.glob('*.service.d/*.conf'):
            name = path.parent.name.removesuffix('.d')
            for key, values in directives(path, 'Unit').items():
                units.setdefault(name, {}).setdefault(key, []).extend(values)

        edges = {name: set() for name in units}
        for name, unit in units.items():
            edges[name].update(n for n in unit.get('After', []) if n in units)
            for following in unit.get('Before', []):
                if following in units:
                    edges[following].add(name)
        done, visiting = set(), set()

        def visit(name):
            self.assertNotIn(name, visiting, f'ordering cycle at {name}')
            if name in done:
                return
            visiting.add(name)
            for dependency in edges[name]:
                visit(dependency)
            visiting.remove(name)
            done.add(name)

        for name in units:
            visit(name)
        pending, seen = ['rtchain-ready.service'], set()
        while pending:
            name = pending.pop()
            if name in seen:
                continue
            seen.add(name)
            self.assertNotIn(name, ('starter.service', 'rtchain.service'))
            self.assertFalse(any(token in name.lower() for token in ('catds', 'devx', 'tcbuf')))
            unit = units.get(name, {})
            pending.extend(unit.get('Requires', []) + unit.get('Wants', []) + unit.get('After', []))
