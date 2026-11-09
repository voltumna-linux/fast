"""Exercise the installed-command boundary without root, hardware or Tango."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

CLI = Path(__file__).resolve().parents[1] / 'rtchain-ops'


class OperationsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.log = self.root / 'commands'
        self.tool = self.root / 'crudaknob'
        self.tool.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
root = Path(os.environ['FIXTURE'])
with (root / 'commands').open('a') as log:
    log.write(json.dumps(sys.argv[1:]) + '\\n')
state = root / 'state.json'
values = json.loads(state.read_text()) if state.exists() else {}
key = sys.argv[1]
if os.environ.get('MISSING_KEY') == key:
    print('chiave non risolta')
    sys.exit(0)
if len(sys.argv) == 3:
    values[key] = sys.argv[2]
    state.write_text(json.dumps(values))
print(key + ' = ' + values.get(key, '0'))
''')
        self.tool.chmod(0o755)
        self.config = {
            'host': os.uname().nodename.split('.')[0],
            'tools': {'crudaknob': str(self.tool)},
            'phases': {'live': ['master-knobs', 'naff-knobs', 'real-beam']},
            'master-knobs': {'values': {'sr/dpdkmaster/fast0/thermal/FifoSetpoint': '2'}},
            'naff-knobs': {'bases': ['sr/diagnostics/naff'], 'groups': [1, 9],
                           'pipeline_stages': '31', 'sideband_floor': '0.0005',
                           'waterfall_mask': '4'},
        }

    def run_ops(self, *args, **env):
        config = self.root / 'host.json'
        config.write_text(json.dumps(self.config))
        return subprocess.run([sys.executable, str(CLI), '--config', str(config), *args],
                              text=True, capture_output=True, timeout=10,
                              env={**os.environ, 'FIXTURE': str(self.root), **env})

    def test_before_starter_uses_only_registry_with_no_tango_or_teardown(self):
        result = self.run_ops('phase', 'live')
        self.assertEqual(result.returncode, 0, result.stderr)
        values = json.loads((self.root / 'state.json').read_text())
        self.assertEqual(values['sr/diagnostics/ebpm/SynthMode'], '0')
        self.assertEqual(values['sr/diagnostics/ebpm/SynthSimBase'], '')
        self.assertEqual(values['sr/diagnostics/naff/common/PipelineStages'], '31')
        self.assertEqual(values['sr/diagnostics/naff/g9/WaterfallMask'], '4')
        self.assertIn('live: completed', result.stdout)
        self.assertNotIn('ready', result.stdout.lower())

    def test_unresolved_registry_key_fails_and_stops_the_phase(self):
        result = self.run_ops('phase', 'live',
                              MISSING_KEY='sr/dpdkmaster/fast0/thermal/FifoSetpoint')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('unresolved', result.stderr)
        self.assertNotIn('SynthMode', self.log.read_text())

    def test_tango_operation_cannot_be_in_pre_starter_phase(self):
        self.config['phases']['live'].append('smart-rescan')
        result = self.run_ops('phase', 'live')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.log.exists(), result.stdout)

    def test_wrong_host_rejected_before_any_command(self):
        self.config['host'] = 'another-host'
        result = self.run_ops('phase', 'live')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.log.exists())

    def test_hugepage_reservation_is_checked_on_controlled_resources(self):
        pool = self.root / 'nr_hugepages'
        pool.write_text('0')
        self.config['hugepages'] = {'pools': [{'path': str(pool), 'count': 17}]}
        result = self.run_ops('operation', 'hugepages')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(pool.read_text().strip(), '17')

    def test_registry_requires_exact_hugetlbfs_before_creation(self):
        findmnt = self.root / 'findmnt'
        findmnt.write_text('#!/bin/sh\nprintf "nfs4\\n"\n')
        findmnt.chmod(0o755)
        self.config['tools']['findmnt'] = str(findmnt)
        self.config['registry'] = {'mount': str(self.root),
                                   'path': str(self.root / 'cruda'), 'node': 1}
        result = self.run_ops('operation', 'registry')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('hugetlbfs', result.stderr)
        self.assertFalse((self.root / 'cruda').exists())

    def test_synthetic_configuration_requires_simulator_publication(self):
        self.config['synthetic-beam'] = {'sim_base': 'sim_fixture', 'mode': 1,
                                        'action_j': '1e-9', 'noise_nm': 3, 'qs': '0.00269'}
        result = self.run_ops('operation', 'synthetic-beam', MISSING_KEY='sim_fixture__Qx')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('SynthMode', self.log.read_text())

    def test_smart_rescan_uses_explicit_host_and_propagates_failure(self):
        probe = self.root / 'python-probe'
        probe.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
Path(os.environ['FIXTURE'], 'rescan').write_text(json.dumps(
    {'host': os.environ['TANGO_HOST'], 'devices': sys.argv[3:]}))
sys.exit(7)
''')
        probe.chmod(0o755)
        self.config['tools']['python3'] = str(probe)
        self.config['smart-rescan'] = {'tango_host': 'fixture-db:20001',
                                      'devices': ['tcbuf/test/fixture']}
        result = self.run_ops('operation', 'smart-rescan', TANGO_HOST='wrong-db:20002')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('exit 7', result.stderr)
        record = json.loads((self.root / 'rescan').read_text())
        self.assertEqual(record, {'host': 'fixture-db:20001',
                                  'devices': ['tcbuf/test/fixture']})

    def test_prepare_and_live_with_controlled_mount_pool_and_registry(self):
        for name, body in {
            'findmnt': '#!/bin/sh\nprintf "hugetlbfs\\n"\n',
            'numactl': '''#!/usr/bin/env python3
import subprocess, sys
sys.exit(subprocess.call(sys.argv[3:]))
''',
            'cruda_init': '''#!/usr/bin/env python3
from pathlib import Path
import sys
Path(sys.argv[sys.argv.index('--shm-name') + 1]).write_text('registry fixture')
''',
        }.items():
            tool = self.root / name
            tool.write_text(body)
            tool.chmod(0o755)
            self.config['tools'][name] = str(tool)
        pool = self.root / 'nr_hugepages'
        pool.write_text('0')
        self.config['mounts'] = {'entries': [{'target': str(self.root), 'type': 'hugetlbfs',
                                             'source': 'none', 'options': 'pagesize=2M'}]}
        self.config['hugepages'] = {'pools': [{'path': str(pool), 'count': 17}]}
        self.config['registry'] = {'mount': str(self.root),
                                   'path': str(self.root / 'cruda'), 'node': 1}
        self.config['phases']['prepare'] = ['mounts', 'hugepages', 'registry']
        result = self.run_ops('phase', 'prepare')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root / 'cruda').is_file())
        result = self.run_ops('phase', 'live')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(pool.read_text().strip(), '17')

    def test_x710_accepts_kernel_normalized_masks_and_cpu_lists(self):
        ethtool = self.root / 'ethtool'
        ethtool.write_text('#!/bin/sh\nif [ "$1" = "-x" ]; then printf "  0: 0 0 0 0\\n"; fi\n')
        ethtool.chmod(0o755)
        self.config['tools']['ethtool'] = str(ethtool)
        xps = self.root / 'xps_cpus'
        irq = self.root / 'smp_affinity_list'
        workers = []

        def kernel_file(path, normalized):
            # A FIFO models sysfs accepting a write, then returning its
            # canonical representation on the next read, across the CLI boundary.
            with path.open() as stream:
                stream.read()
            with path.open('w') as stream:
                stream.write(normalized + '\n')

        for path, normalized in [(xps, '00000000,0000000f'), (irq, '2-3')]:
            os.mkfifo(path)
            worker = threading.Thread(target=kernel_file, args=(path, normalized), daemon=True)
            worker.start()
            workers.append(worker)
        self.config['x710'] = {'interface': 'fixture0', 'queues': [
            {'xps_path': str(xps), 'mask': 'f', 'irq_path': str(irq), 'irq_cpus': '2,3'}]}
        result = self.run_ops('operation', 'x710')
        self.assertEqual(result.returncode, 0, result.stderr)
        for worker in workers:
            worker.join(timeout=1)
            self.assertFalse(worker.is_alive())

    def test_kernel_selector_checks_the_active_value_returned_by_sysfs(self):
        for returned, success in [('always madvise [never]', True),
                                  ('[always] madvise never', False)]:
            with self.subTest(returned=returned):
                path = self.root / ('selector-success' if success else 'selector-failure')
                os.mkfifo(path)

                def selector_file():
                    with path.open() as stream:
                        stream.read()
                    with path.open('w') as stream:
                        stream.write(returned + '\n')

                worker = threading.Thread(target=selector_file, daemon=True)
                worker.start()
                self.config['kernel'] = {'selectors': {str(path): 'never'}}
                result = self.run_ops('operation', 'kernel')
                self.assertEqual(result.returncode == 0, success, result.stderr)
                worker.join(timeout=1)
                self.assertFalse(worker.is_alive())


if __name__ == '__main__':
    unittest.main()
