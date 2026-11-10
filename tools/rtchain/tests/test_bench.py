"""Post-Starter bench CLI against controlled Tango and registry endpoints."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest

import test_operations
import test_preparation

ROOT = Path(__file__).resolve().parents[1]


class BenchTest(unittest.TestCase):
    def setUp(self):
        self.fixture = test_preparation.PreparationTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.config = self.fixture.config
        ops = test_operations.OperationsTest()
        ops.setUp()
        self.addCleanup(ops.doCleanups)
        self.config['tools']['crudaknob'] = str(ops.tool)
        self.config['tango_servers'] = {'tango_host': 'fixture-db:20001'}
        self.config['bench'] = {
            'enabled': True, 'timeout': 1, 'poll_interval': 0.02,
            'devices': [{'name': 'sr/sim/fixture', 'attributes': ['Qx']},
                        {'name': 'sr/tcbuf/fixture', 'attributes': []}],
            'keys': ['naff/field'],
            'operations': ['synthetic-beam', 'smart-rescan']}
        self.config['synthetic-beam'] = dict(device='sr/sim/fixture', sim_base='sim_fixture', mode=1,
                                              action_j='1e-9', noise_nm=3, qs='0.00269')
        self.config['smart-rescan'] = dict(tango_host='fixture-db:20001',
                                          devices=['sr/tcbuf/fixture'])
        (self.root / 'tango.py').write_text('''import os
from pathlib import Path
root = Path(os.environ['TANGO_FIXTURE'])
class DeviceProxy:
    def __init__(self, name):
        assert os.environ['TANGO_HOST'] == 'fixture-db:20001'
        self.name = name
    def set_timeout_millis(self, value): pass
    def ping(self):
        if not (root / 'published').exists(): raise RuntimeError('not published')
    def read_attribute(self, name):
        self.ping()
        if (root / 'missing-attribute').exists(): raise RuntimeError('missing attribute')
    def command_query(self, name): self.ping()
    def command_inout(self, name):
        self.ping()
        if (root / 'fail-command').exists(): raise RuntimeError('rescan failed')
        with (root / 'rescans').open('a') as stream: stream.write(self.name + '\\n')
''')
        self.env = {**os.environ, 'PYTHONPATH': str(self.root),
                    'FIXTURE': str(ops.root), 'TANGO_FIXTURE': str(self.root),
                    'TANGO_HOST': 'wrong-db:10000'}
        self.state = ops.root / 'state.json'

    def start(self):
        self.fixture.config_path.write_text(json.dumps(self.config))
        process = subprocess.Popen([sys.executable, str(ROOT / 'rtchain-bench'),
                                    '--config', str(self.fixture.config_path)],
                                   env=self.env, text=True, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE)
        def cleanup():
            if process.poll() is None:
                process.kill()
            process.communicate()
        self.addCleanup(cleanup)
        return process

    def test_waits_for_tango_before_configuring_and_reports_bench_only_success(self):
        process = self.start()
        time.sleep(0.15)
        self.assertIsNone(process.poll())
        self.assertFalse(self.state.exists())
        (self.root / 'published').touch()
        out, err = process.communicate(timeout=5)
        self.assertEqual(process.returncode, 0, err)
        self.assertIn('bench: ready', out)
        self.assertNotIn('realtime: ready', out)
        self.assertEqual(json.loads(self.state.read_text())['sr/diagnostics/ebpm/SynthMode'], '1')
        self.assertEqual((self.root / 'rescans').read_text(), 'sr/tcbuf/fixture\n')

    def test_missing_prerequisites_fail_without_writes_or_ready(self):
        for missing in ('server', 'attribute', 'field'):
            with self.subTest(missing=missing):
                self.config['bench']['timeout'] = 0.15
                (self.root / 'published').touch()
                if missing == 'server':
                    (self.root / 'published').unlink()
                if missing == 'attribute':
                    (self.root / 'missing-attribute').touch()
                else:
                    (self.root / 'missing-attribute').unlink(missing_ok=True)
                self.env['MISSING_KEY'] = 'naff/field' if missing == 'field' else ''
                process = self.start()
                out, err = process.communicate(timeout=5)
                self.assertNotEqual(process.returncode, 0)
                self.assertIn('prerequisites unavailable', err)
                self.assertNotIn('bench: ready', out)
                self.assertFalse(self.state.exists())

    def test_command_failure_never_declares_bench_ready(self):
        (self.root / 'published').touch()
        (self.root / 'fail-command').touch()
        process = self.start()
        out, err = process.communicate(timeout=5)
        self.assertNotEqual(process.returncode, 0)
        self.assertIn('rescan failed', err)
        self.assertNotIn('bench: ready', out)

    def test_database_mismatch_and_foreign_device_uri_fail_before_writes(self):
        for invalid in ('database', 'device'):
            with self.subTest(invalid=invalid):
                self.config['smart-rescan']['tango_host'] = 'fixture-db:20001'
                if invalid == 'database':
                    self.config['smart-rescan']['tango_host'] = 'other-db:10000'
                else:
                    self.config['bench']['devices'][0]['name'] = 'tango://other-db:10000/sr/sim/fixture'
                process = self.start()
                out, err = process.communicate(timeout=5)
                self.assertNotEqual(process.returncode, 0)
                self.assertIn('bench not ready', err)
                self.assertFalse(self.state.exists())

    def test_real_beam_bench_does_not_require_simulator(self):
        self.config['bench']['operations'] = ['smart-rescan']
        self.config['bench']['devices'] = self.config['bench']['devices'][1:]
        del self.config['synthetic-beam']
        (self.root / 'published').touch()
        process = self.start()
        out, err = process.communicate(timeout=5)
        self.assertEqual(process.returncode, 0, err)
        self.assertIn('bench: ready', out)
        self.assertFalse(self.state.exists())

    def test_generator_pulls_bench_from_starter_without_reverse_readiness_dependency(self):
        result = self.fixture.invoke('rtchain-systemd-generator', self.fixture.out)
        self.assertEqual(result.returncode, 0, result.stderr)
        dropin = self.fixture.out / 'starter.service.d/80-bench.conf'
        self.assertIn('Wants=rtchain-bench.service', dropin.read_text())
        self.assertNotIn('Requires=rtchain-bench', dropin.read_text())
        self.assertFalse((self.fixture.out / 'rtchain-ready.service.d/80-bench.conf').exists())
        self.config['smart-rescan']['tango_host'] = 'wrong-db:10000'
        result = self.fixture.invoke('rtchain-systemd-generator', self.fixture.out)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('ExecStartPre=/bin/false',
                      (self.fixture.out / 'rtchain-bench.service.d/80-bench.conf').read_text())
        self.assertFalse((self.fixture.out / 'rtchain-producer-preflight.service.d').exists())

    def test_failed_generator_write_does_not_rescan_or_declare_ready(self):
        (self.root / 'published').touch()
        self.env['MISSING_KEY'] = 'sr/diagnostics/ebpm/SynthNoiseNm'
        process = self.start()
        out, err = process.communicate(timeout=5)
        self.assertNotEqual(process.returncode, 0)
        self.assertIn('unresolved', err)
        self.assertNotIn('bench: ready', out)
        self.assertFalse((self.root / 'rescans').exists())

    def test_installed_unit_waits_for_starter_and_preserves_separate_result(self):
        from test_startup import directives
        unit = ROOT / 'systemd/rtchain-bench.service'
        dependencies = directives(unit, 'Unit')
        self.assertIn('starter.service', dependencies['Requires'])
        self.assertIn('starter.service', dependencies['After'])
        service = directives(unit, 'Service')
        self.assertEqual(service['Type'], ['oneshot'])
        self.assertEqual(service['RemainAfterExit'], ['yes'])
        for path in [ROOT / 'systemd/rtchain-ready.service',
                     * (ROOT / 'systemd/starter.service.d').glob('*.conf')]:
            self.assertNotIn('rtchain-bench', path.read_text())

    def test_duplicate_rescan_destinations_rejected_before_writes(self):
        self.config['smart-rescan']['devices'] *= 2
        (self.root / 'published').touch()
        process = self.start()
        out, err = process.communicate(timeout=5)
        self.assertNotEqual(process.returncode, 0)
        self.assertIn('unique SmartRescan', err)
        self.assertFalse(self.state.exists())
        self.assertFalse((self.root / 'rescans').exists())

    def test_hung_field_probe_is_bounded_by_prerequisite_timeout(self):
        tool = self.root / 'hung-knob'
        tool.write_text('#!/usr/bin/env python3\nimport time\ntime.sleep(5)\n')
        tool.chmod(0o755)
        self.config['tools']['crudaknob'] = str(tool)
        self.config['bench']['timeout'] = 0.15
        (self.root / 'published').touch()
        started = time.monotonic()
        process = self.start()
        out, err = process.communicate(timeout=3)
        self.assertNotEqual(process.returncode, 0)
        self.assertIn('timed out', err)
        self.assertLess(time.monotonic() - started, 2)
        self.assertNotIn('bench: ready', out)
