"""Exercise generator/CLI contracts with controlled resources; no boot claim."""
import copy
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PreparationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config_path = self.root / 'host.json'
        self.out = self.root / 'generator'
        self.pool = self.root / 'nr_hugepages'
        self.pool.write_text('0')
        self.registry = self.root / 'cruda'
        self.log = self.root / 'commands'
        self.tool = self.root / 'tool'
        self.tool.write_text('''#!/usr/bin/env python3
import os, sys
from pathlib import Path
with open(os.environ['COMMAND_LOG'], 'a') as stream:
    stream.write(' '.join(sys.argv[1:]) + '\\n')
args = sys.argv[1:]
if args[0] == '-n':
    if os.environ.get('FAIL_MOUNT'):
        sys.exit(1)
    print('tmpfs' if args[-1].endswith('/logs') else 'hugetlbfs')
elif args[0] == '-t':
    sys.exit(32)
elif args[0].startswith('--cpunodebind=') or args[0].startswith('--membind='):
    import subprocess
    sys.exit(subprocess.call(args[2:]))
elif args[0] == '--shm-name':
    Path(args[1]).write_text('registry')
elif args[0] == 'show':
    print('0-1')
elif '--verifica' in args and os.environ.get('FAIL_LIBRARIES'):
    sys.exit(7)
''')
        self.tool.chmod(0o755)
        self.config = {
            'host': socket.gethostname().split('.')[0], 'commissioned': True,
            'log_directory': str(self.root / 'logs'),
            'requires_mounts_for': ['/srv/fast/bin', '/srv/fast/etc'],
            'library_path': ['/srv/fast/lib'],
            'time_sync_units': ['systemd-time-wait-sync.service'],
            'tools': {name: str(self.tool) for name in
                      ('mount', 'findmnt', 'numactl', 'libhot_setup', 'cruda_init', 'systemctl')},
            'phases': {'prepare': ['mounts', 'hugepages', 'libraries', 'registry', 'system-cpuset']},
            'not_applicable': {name: 'controlled fixture without peripherals' for name in
                               ('kernel', 'kernel-jail', 'network', 'x710')},
            'mounts': {'entries': [
                {'target': str(self.root), 'type': 'hugetlbfs', 'source': 'none', 'options': 'pagesize=2M'},
                {'target': str(self.root / 'logs'), 'type': 'tmpfs', 'source': 'tmpfs', 'options': 'size=128M'}]},
            'hugepages': {'pools': [{'path': str(self.pool), 'count': 17}]},
            'libraries': {'nodes': [0]},
            'registry': {'mount': str(self.root), 'path': str(self.registry), 'node': 1},
            'system-cpuset': {'units': ['system.slice', 'user.slice', 'init.scope'], 'cpus': '0-1'},
            'resources': {'rtchain': {'cpus': '2-3', 'memory_nodes': '0-1'},
                          'tango': {'cpus': '4-5', 'memory_nodes': '0-1'}},
        }

    def invoke(self, command, *args, **env):
        self.config_path.write_text(json.dumps(self.config))
        return subprocess.run([sys.executable, str(ROOT / command), '--config',
                               str(self.config_path), *map(str, args)],
                              env={**os.environ, 'COMMAND_LOG': str(self.log), **env},
                              text=True, capture_output=True, timeout=10)

    def test_generator_orders_external_tools_libraries_and_configuration_mounts(self):
        result = self.invoke('rtchain-systemd-generator', self.out)
        self.assertEqual(result.returncode, 0, result.stderr)
        unit = (self.out / 'rtchain-prepare.service.d/50-host.conf').read_text()
        self.assertIn('RequiresMountsFor=', unit)
        for path in ('/srv/fast/bin', '/srv/fast/lib', '/srv/fast/etc', str(self.tool)):
            self.assertIn(path, unit)
        self.assertIn('Requires=systemd-time-wait-sync.service', unit)
        tango = (self.out / 'tango.slice.d/50-host.conf').read_text()
        self.assertIn('AllowedCPUs=4-5', tango)
        self.assertIn('AllowedMemoryNodes=0-1', tango)
        self.assertFalse(self.log.exists())

    def test_success_reserves_memory_and_publishes_registry(self):
        result = self.invoke('rtchain-prepare')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.pool.read_text().strip(), '17')
        self.assertTrue(self.registry.is_file())
        self.assertLess(result.stdout.index('libraries: completed'),
                        result.stdout.index('registry: starting'))
        self.assertNotIn('Tango', self.log.read_text())

    def test_required_library_failure_prevents_registry_creation(self):
        result = self.invoke('rtchain-prepare', FAIL_LIBRARIES='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('exit 7', result.stderr)
        self.assertIn('libraries: starting', result.stdout)
        self.assertFalse(self.registry.exists())
        self.assertNotIn('system.slice', self.log.read_text())

    def test_mount_failure_stops_before_pool_reservation_or_external_tools(self):
        result = self.invoke('rtchain-prepare', FAIL_MOUNT='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('mounts:', result.stderr)
        self.assertIn('exit 32', result.stderr)
        self.assertEqual(self.pool.read_text(), '0')
        self.assertFalse(self.registry.exists())
        self.assertNotIn('--verifica', self.log.read_text())

    def test_invalid_configuration_fails_generator_and_command_before_mutation(self):
        original = copy.deepcopy(self.config)
        mutations = [
            lambda c: c.update(host='wrong-host'),
            lambda c: c.update(commissioned=False),
            lambda c: c.update(requires_mounts_for=[]),
            lambda c: c['requires_mounts_for'].append('/bad%specifier'),
            lambda c: c['resources']['tango'].update(memory_nodes='0'),
            lambda c: c['resources']['rtchain'].update(cpus='0-3'),
            lambda c: c['phases']['prepare'].reverse(),
            lambda c: c['not_applicable'].pop('kernel'),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                self.config = copy.deepcopy(original)
                mutate(self.config)
                result = self.invoke('rtchain-systemd-generator', self.out)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('ExecStartPre=/bin/false',
                              (self.out / 'rtchain-prepare.service.d/50-host.conf').read_text())
                result = self.invoke('rtchain-prepare')
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.pool.read_text(), '0')
                self.assertFalse(self.log.exists())

if __name__ == '__main__':
    unittest.main()
