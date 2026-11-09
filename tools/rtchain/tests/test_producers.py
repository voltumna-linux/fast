"""Operational producer CLI with real, controllable foreground processes."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

import test_preparation

ROOT = Path(__file__).resolve().parents[1]


class ProducersTest(unittest.TestCase):
    def setUp(self):
        fixture = test_preparation.PreparationTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.root = fixture.root
        self.config = fixture.config
        self.config_path = fixture.config_path
        self.processes = {}
        self.config['log_directory'] = str(self.root / 'logs')
        (self.root / 'logs').mkdir()
        self.wrapper = self.root / 'wrapper'
        self.wrapper.write_text('''#!/usr/bin/env python3
import os, sys
args = sys.argv[1:]
if args[0].isdigit():
    args = args[2:]
else:
    args = args[args.index('--')+1:]
os.execvpe(args[0], args, os.environ)
''')
        self.wrapper.chmod(0o755)
        self.binary = self.root / 'producer'
        self.binary.write_text('''#!/usr/bin/env python3
# DPDK_EBPM_MASTERS -- capability advertised by controlled ebpm
import os, sys, time, ctypes
ctypes.CDLL(None).prctl(15, b"producer-ctrl", 0, 0, 0)
from pathlib import Path
root = Path(os.environ['FIXTURE_STATE'])
name = sys.argv[1]
if name == 'tsc':
    if not (root / 'omit-tsc').exists():
        (root / 'tsc').write_text('clock')
elif name.startswith('master'):
    port = int(name[-1])
    if not (root / f'omit-{name}').exists():
        (root / f'shm{port}').write_text('master')
        (root / f'eal{port}').mkdir()
    print('queues ready', flush=True)
else:
    (root / 'ebpm-shm').write_text('ebpm')
    print('running', flush=True)
    for port in range(6):
        if not (root / f'omit-registration{port}').exists():
            with (root / 'logs' / f'master{port}.log').open('a') as log:
                log.write('slave 2: registrato\\nslave 7: registrato\\n')
while True:
    time.sleep(1)
''')
        self.binary.chmod(0o755)
        self.config['tools'].update(numactl=str(self.wrapper), libhot_run=str(self.wrapper))
        def producer(name):
            return {'executable': str(self.binary), 'args': [name], 'node': 0,
                    'cpus': '2-3', 'policy': 'other', 'priority': 0,
                    'environment': {'FIXTURE_STATE': str(self.root)},
                    'files': [], 'ready_keys': [], 'ready_log': [], 'timeout': 0.3}
        tsc = producer('tsc')
        tsc['shm'] = str(self.root / 'tsc')
        masters = []
        for port in range(6):
            master = producer(f'master{port}')
            master.update(port=port, prefix=f'sx{port}', name=f'fast{port}', app_id=40+port,
                          bdf=f'0000:81:0{port}.0', ip=f'10.0.0.{port+1}',
                          shm=str(self.root / f'shm{port}'), eal=str(self.root / f'eal{port}'),
                          cruda_template=str(self.root / 'master.conf'),
                          control_cpus='2', control_threads=['producer-ctrl'])
            master['environment'].update(DPDK_CORE_LIST=f'0@2,{16*port+1}@3',
                                         DPDK_RXPORT_CONFIG=f'port0:{16*port+1}',
                                         DPDK_TXPORT_CONFIG=f'port0:{16*port+5}')
            master['ready_log'] = ['queues ready']
            masters.append(master)
        ebpm = producer('ebpm')
        ebpm.update(shm=str(self.root / 'ebpm-shm'), calibration_base=str(self.root / 'bpm'),
                    cruda_conf=str(self.root / 'ebpm.conf'), units_per_port=1,
                    fill_id=2, drain_id=7, catalog_units=6)
        ebpm['ready_log'] = ['running']
        ebpm['environment']['DPDK_EBPM_TX_SYNTH'] = '0'
        (self.root / 'master.conf').write_text('-name=fast; -app_id=1; -manifest=/tmp/old.tsv;\n')
        (self.root / 'ebpm.conf').write_text('ebpm schema')
        (self.root / 'bpm_chain.csv').write_text(''.join(f'-fpgaidx={i};\n' for i in range(1, 7)))
        (self.root / 'bpm.csv').write_text(''.join(
            f'-fpgaidx={i}; -pos={pos};\n' for i in range(1, 7) for pos in ('A', 'B')))
        self.config['producers'] = {'tsc': tsc, 'masters': masters, 'ebpm': ebpm,
                                    'free_hugepages': [{'path': str(self.root / 'free'), 'count': 12}],
                                    'pci_root': str(self.root / 'pci'),
                                    'live': [], 'not_applicable': {
                                        'e810': 'controlled producers without NICs',
                                        'master-knobs': 'no cruda in fixture',
                                        'tx-rate': 'no synthetic TX'}}
        (self.root / 'free').write_text('20')
        for master in masters:
            device = self.root / 'pci' / master['bdf']
            (device / 'iommu_group' / 'devices').mkdir(parents=True)
            (device / 'iommu_group' / 'devices' / master['bdf']).touch()
            (device / 'driver').symlink_to('/sys/bus/pci/drivers/vfio-pci')
        self.config['tools']['dpdk_devbind'] = str(self.wrapper)

    def invoke(self, *args):
        self.config_path.write_text(json.dumps(self.config))
        return subprocess.run([sys.executable, str(ROOT / 'rtchain-producer'), '--config',
                               str(self.config_path), *map(str, args)], text=True,
                              capture_output=True, timeout=10)

    def launch(self, name):
        self.config_path.write_text(json.dumps(self.config))
        process = subprocess.Popen([sys.executable, str(ROOT / 'rtchain-producer'), '--config',
                                    str(self.config_path), 'launch', name],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.processes[name] = process
        def cleanup():
            process.terminate()
            process.communicate(timeout=5)
        self.addCleanup(cleanup)
        return self.invoke('wait', name, '--pid', process.pid)

    def test_chain_publishes_resources_and_registers_ebpm_on_six_masters(self):
        result = self.invoke('preflight')
        self.assertEqual(result.returncode, 0, result.stderr)
        for name in ('tsc', 'master0', 'master1', 'master2', 'master3', 'master4', 'master5', 'ebpm'):
            result = self.launch(name)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('six masters', result.stdout)
        for master in self.config['producers']['masters']:
            conf = (self.root / 'logs' / f"master{master['port']}.conf").read_text()
            self.assertIn(f"-app_id={master['app_id']};", conf)
            self.assertIn(f"-name={master['name']};", conf)

    def test_missing_tsc_resource_blocks_consumers_even_with_live_process(self):
        (self.root / 'omit-tsc').touch()
        result = self.launch('tsc')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('shared resource', result.stderr)
        self.assertIsNone(self.processes['tsc'].poll())

    def test_each_master_must_publish_shm_and_eal_resources(self):
        for port in range(6):
            with self.subTest(port=port):
                (self.root / f'omit-master{port}').touch()
                result = self.launch(f'master{port}')
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(f'master{port}: timeout', result.stderr)
                self.assertIn('EAL prefix', result.stderr)

    def test_duplicate_registrations_cannot_hide_one_missing_master(self):
        for port in range(6):
            result = self.launch(f'master{port}')
            self.assertEqual(result.returncode, 0, result.stderr)
        for missing in range(6):
            with self.subTest(missing=missing):
                (self.root / f'omit-registration{missing}').touch()
                for port in range(6):
                    (self.root / 'logs' / f'master{port}.log').write_text('queues ready\n')
                # Repeated registration on a different master must not count.
                with (self.root / 'logs' / f'master{(missing+1)%6}.log').open('a') as stream:
                    stream.write('slave 2: registrato\nslave 7: registrato\n' * 6)
                result = self.launch('ebpm')
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(f'master{missing} FILL registration', result.stderr)
                self.assertIn(f'master{missing} DRAIN registration', result.stderr)
                self.processes['ebpm'].terminate()
                self.processes['ebpm'].communicate(timeout=5)
                (self.root / 'ebpm-shm').unlink()
                (self.root / f'omit-registration{missing}').unlink()

    def test_generator_connects_static_units_and_mounts_for_each_producer(self):
        result = self.invoke_generator()
        self.assertEqual(result.returncode, 0, result.stderr)
        generated = self.root / 'generated'
        for name in ('tsc', 'master@0', 'master@5', 'ebpm'):
            unit = (generated / f'rtchain-{name}.service.d/60-producer.conf').read_text()
            self.assertIn('RequiresMountsFor=', unit)
            self.assertIn(str(self.binary), unit)
            self.assertIn('CPUSchedulingPolicy=other', unit)
        master5 = (generated / 'rtchain-master@5.service.d/60-producer.conf').read_text()
        self.assertIn('Requires=rtchain-master@4.service', master5)
        self.assertIn('After=rtchain-master@4.service', master5)

    def test_real_beam_keeps_remapping_disabled_with_complete_catalog(self):
        self.config['producers']['ebpm']['units_per_port'] = 0
        result = self.invoke('preflight')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_bad_catalog_or_shared_iommu_group_fails_before_binding(self):
        chain = self.root / 'bpm_chain.csv'
        original = chain.read_text()
        chain.write_text(original.replace('-fpgaidx=6;', '-fpgaidx=5;'))
        result = self.invoke('preflight')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('catalog', result.stderr)
        chain.write_text(original)
        (self.root / 'pci/0000:81:00.0/iommu_group/devices/another-function').touch()
        result = self.invoke('preflight')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('IOMMU', result.stderr)

    def test_stale_resources_and_low_hugepage_budget_are_not_success(self):
        (self.root / 'tsc').write_text('old clock')
        result = self.invoke('preflight')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('existing resource', result.stderr)
        (self.root / 'tsc').unlink()
        (self.root / 'free').write_text('11')
        result = self.invoke('preflight')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('insufficient free hugepages', result.stderr)

    def test_invalid_topology_blocks_preflight_in_generator(self):
        self.config['producers']['masters'][5]['prefix'] = 'sx0'
        result = self.invoke_generator()
        self.assertNotEqual(result.returncode, 0)
        block = self.root / 'generated/rtchain-producer-preflight.service.d/60-producer.conf'
        self.assertIn('ExecStartPre=/bin/false', block.read_text())

    def configure_live(self):
        allowed = sorted(c for c in os.sched_getaffinity(0) if c >= 2)
        cpus = ','.join(map(str, allowed[:2]))
        self.config['resources']['rtchain']['cpus'] = cpus
        for item in [self.config['producers']['tsc'], *self.config['producers']['masters'],
                     self.config['producers']['ebpm']]:
            item['cpus'] = cpus
        for master in self.config['producers']['masters']:
            master['control_cpus'] = str(allowed[0])
        ctl = self.root / 'systemctl'
        ctl.write_text('#!/usr/bin/env python3\nimport json,sys\nfrom pathlib import Path\n'
                       + f'print(json.loads(Path({str(self.root / "pids")!r}).read_text())[sys.argv[-1]])\n')
        ctl.chmod(0o755)
        self.config['tools']['systemctl'] = str(ctl)
        for name in ('tsc', 'master0', 'master1', 'master2', 'master3', 'master4', 'master5', 'ebpm'):
            result = self.launch(name)
            self.assertEqual(result.returncode, 0, result.stderr)
        pids = {('rtchain-master@' + name[-1] if name.startswith('master') else 'rtchain-' + name)
                + '.service': process.pid for name, process in self.processes.items()}
        (self.root / 'pids').write_text(json.dumps(pids))
        tune = self.root / 'tune'
        tune.write_text('#!/usr/bin/env python3\nfrom pathlib import Path\nimport sys\n'
                        + f'root = Path({str(self.root)!r})\n'
                        + 'if (root / "fail-tune").exists(): sys.exit(7)\n'
                        + '(root / "tuned").write_text("done")\n')
        tune.chmod(0o755)
        self.config['tools']['dpdk_master_tune'] = str(tune)
        self.config['e810'] = {'bdfs': [m['bdf'] for m in self.config['producers']['masters']],
                               'tph': False, 'parameters': ['apply']}
        self.config['producers']['live'] = ['e810']
        self.config['producers']['not_applicable'].pop('e810')

    def test_live_hardware_steps_and_control_threads_cover_all_masters(self):
        self.configure_live()
        result = self.invoke('live')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root / 'tuned').exists())
        for port in range(6):
            self.assertEqual(os.sched_getaffinity(self.processes[f'master{port}'].pid),
                             {min(c for c in os.sched_getaffinity(0) if c >= 2)})

    def test_required_post_eal_tuning_failure_fails_the_phase(self):
        self.configure_live()
        (self.root / 'fail-tune').touch()
        result = self.invoke('live')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('exit 7', result.stderr)
        self.assertNotIn('producer live steps completed', result.stdout)

    def test_synthetic_rate_is_applied_at_one_time_on_all_six_masters(self):
        self.configure_live()
        knobs = self.root / 'knobs'
        knobs.write_text('{}')
        tool = self.root / 'crudaknob'
        tool.write_text('#!/usr/bin/env python3\nimport json,sys,time\nfrom pathlib import Path\n'
                        + f'path = Path({str(knobs)!r})\n'
                        + "values = json.loads(path.read_text())\nkey = sys.argv[1]\n"
                        + "if len(sys.argv) > 2:\n    values[key] = sys.argv[2]\n    path.write_text(json.dumps(values))\n"
                        + "if key.endswith('/RateAppliedPct'):\n    base = key.rsplit('/', 1)[0]\n"
                        + "    value = values[base + '/RatePct'] if time.time() >= float(values[base + '/ApplyAtS']) else '0'\n"
                        + "else:\n    value = values.get(key, '0')\nprint(key + ' = ' + value)\n")
        tool.chmod(0o755)
        self.config['tools']['crudaknob'] = str(tool)
        self.config['producers']['live'].append('tx-rate')
        self.config['producers']['not_applicable'].pop('tx-rate')
        self.config['producers']['tx-rate'] = {'percent': 32}
        self.config['producers']['ebpm']['timeout'] = 6
        result = self.invoke('live')
        self.assertEqual(result.returncode, 0, result.stderr)
        values = json.loads(knobs.read_text())
        dates = [values[f'sr/dpdkmaster/fast{p}/txsynth/ApplyAtS'] for p in range(6)]
        self.assertEqual(len(set(dates)), 1)
        for port in range(6):
            self.assertEqual(float(values[f'sr/dpdkmaster/fast{port}/txsynth/RatePct']), 32)

    def test_slow_successful_key_read_cannot_bypass_readiness_timeout(self):
        knob = self.root / 'slow-knob'
        knob.write_text('#!/usr/bin/env python3\nimport time,sys\ntime.sleep(1)\nprint(sys.argv[1] + " = 1")\n')
        knob.chmod(0o755)
        self.config['tools']['crudaknob'] = str(knob)
        self.config['producers']['masters'][0]['ready_keys'] = ['sr/dpdkmaster/fast0/ready']
        result = self.launch('master0')
        self.assertNotEqual(result.returncode, 0)
        self.assertRegex(result.stderr, 'timeout|timed out')

    def test_readiness_probe_receives_libraries_declared_by_host(self):
        tool = self.root / 'library-knob'
        tool.write_text('#!/usr/bin/env python3\nimport os,sys\n'
                        + 'assert os.environ.get("LD_LIBRARY_PATH") == "/srv/fast/lib"\n'
                        + 'print(sys.argv[1] + " = 1")\n')
        tool.chmod(0o755)
        self.config['tools']['crudaknob'] = str(tool)
        self.config['producers']['masters'][0]['ready_keys'] = ['sr/dpdkmaster/fast0/ready']
        result = self.launch('master0')
        self.assertEqual(result.returncode, 0, result.stderr)

    def invoke_generator(self):
        self.config_path.write_text(json.dumps(self.config))
        return subprocess.run([sys.executable, str(ROOT / 'rtchain-systemd-generator'),
                               '--config', str(self.config_path), str(self.root / 'generated')],
                              capture_output=True, text=True, timeout=10)


if __name__ == '__main__':
    unittest.main()
