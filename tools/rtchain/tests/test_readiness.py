"""Readiness CLI with foreground producers and buffer-reading adapter; no Tango."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest

import test_producers

ROOT = Path(__file__).resolve().parents[1]
GROUPS = list(range(1, 90, 8))


class ReadinessTest(unittest.TestCase):
    def setUp(self):
        self.fixture = test_producers.ProducersTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.config = self.fixture.config
        # Each actual controlled producer updates a data sequence. The adapter
        # observes those files, rather than manufacturing progress itself.
        binary = self.fixture.binary
        binary.write_text(binary.read_text().replace('    time.sleep(1)', '''    counter = root / (name + '-sequence')
    previous = int(counter.read_text()) if counter.exists() else 0
    if not (root / ('stall-' + name)).exists():
        temp = counter.with_suffix('.new')
        temp.write_text(str(previous + 1))
        temp.replace(counter)
    time.sleep(0.01)'''))
        naff = self.root / 'naff'
        naff.write_text('''#!/usr/bin/env python3
import os, time, json
from pathlib import Path
root = Path(os.environ['FIXTURE_STATE'])
g = int(os.environ['NAFF_CCD_GROUP'])
name = 'naff' + str(g)
shm = root / 'naff-shm'
if g == 1:
    assert not shm.exists(), 'a second creator would erase labels'
    shm.write_text('{}')
else:
    assert shm.exists(), 'attacher started before creator'
if not (root / ('omit-' + name)).exists():
    labels = json.loads(shm.read_text())
    labels[name] = ['spectrum-g' + str(g), 'tune-g' + str(g)]
    shm.write_text(json.dumps(labels))
    print('spectrum ring labelled', flush=True)
while True:
    counter = root / (name + '-sequence')
    previous = int(counter.read_text()) if counter.exists() else 0
    if not (root / ('stall-' + name)).exists():
        temp = counter.with_suffix('.new')
        temp.write_text(str(previous + 1))
        temp.replace(counter)
    time.sleep(0.01)
''')
        naff.chmod(0o755)
        self.config['producers']['naffs'] = [dict(
            executable=str(naff), args=[], node=0, cpus='2-3', policy='other', priority=0,
            environment={'FIXTURE_STATE':str(self.root), 'NAFF_CCD_PREFIX':'fixture'},
            files=[], ready_keys=[f'sr/diagnostics/naff/g{g}/ready'],
            ready_log=['spectrum ring labelled'], timeout=0.3,
            shm=str(self.root / 'naff-shm'), group=g) for g in GROUPS]
        knob = self.root / 'knob'
        knob.write_text('''#!/usr/bin/env python3
import sys,json
from pathlib import Path
''' + f'root = Path({str(self.root)!r})\n' + '''
key = sys.argv[1]
if (root / 'unpublished-key').exists():
    print('unknown key'); sys.exit(0)
path = root / 'knob-values'
values = json.loads(path.read_text()) if path.exists() else {}
if len(sys.argv) > 2:
    if (root / 'fail-knobs').exists(): sys.exit(7)
    values[key] = sys.argv[2]; path.write_text(json.dumps(values))
print(key + ' = ' + values.get(key, '1'))
''')
        knob.chmod(0o755)
        self.config['tools']['crudaknob'] = str(knob)
        probe = self.root / 'probe'
        probe.write_text('''#!/usr/bin/env python3
import json,sys
from pathlib import Path
''' + f'root = Path({str(self.root)!r})\n' + '''
manifest = json.load(sys.stdin)
labels = json.loads((root / 'naff-shm').read_text())
output = {name: {'sequence': int((root / (name + '-sequence')).read_text()),
                 'labels': labels.get(name, [])} for name in manifest['sources']}
if (root / 'incomplete-probe').exists(): output.pop('master5')
print(json.dumps(output))
''')
        probe.chmod(0o755)
        self.config['tools']['rtchain_probe'] = str(probe)
        self.config['readiness'] = {
            'live':['naff-knobs'], 'not_applicable':{op:'controlled fixture without kernel tuning'
                for op in ('real-beam','compaction','fair-server','tick-revalidate')},
            'naff_base':'sr/diagnostics/naff','sample_interval':0.15,'timeout':2,
            'labels':{f'naff{g}':[f'spectrum-g{g}',f'tune-g{g}'] for g in GROUPS}}
        self.config['naff-knobs'] = {'bases':['sr/diagnostics/naff'], 'groups':GROUPS,
            'pipeline_stages':31,'sideband_floor':0.01,'waterfall_mask':4}

    def invoke(self, command):
        self.fixture.config_path.write_text(json.dumps(self.config))
        return subprocess.run([sys.executable, str(ROOT / 'rtchain-ready'), '--config',
            str(self.fixture.config_path), command], text=True, capture_output=True, timeout=15)

    def start_chain(self):
        result = self.fixture.invoke('preflight')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.fixture.configure_live()
        for n in self.config['producers']['naffs']:
            n['cpus'] = self.config['resources']['rtchain']['cpus']
        result = self.fixture.invoke('live')
        self.assertEqual(result.returncode, 0, result.stderr)
        for group in GROUPS:
            result = self.fixture.launch(f'naff{group}')
            self.assertEqual(result.returncode, 0, result.stderr)
        pids = {('rtchain-naff@' + name[4:] if name.startswith('naff') else
                 'rtchain-master@' + name[6:] if name.startswith('master') else 'rtchain-' + name)
                + '.service': p.pid for name,p in self.fixture.processes.items()}
        (self.root / 'pids').write_text(json.dumps(pids))
        ctl = Path(self.config['tools']['systemctl'])
        ctl.write_text('''#!/usr/bin/env python3
import json,sys
from pathlib import Path
''' + f'root = Path({str(self.root)!r})\n' + '''
unit = sys.argv[-1]
prop = sys.argv[sys.argv.index('-p')+1]
if prop == 'MainPID': print(json.loads((root / 'pids').read_text())[unit])
elif (root / ('fail-' + unit)).exists(): print('failed')
else: print('active' if prop == 'ActiveState' else 'success')
''')
        result = self.invoke('live')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_full_chain_and_readiness_without_tango(self):
        self.start_chain()
        result = self.invoke('check')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('readiness succeeded', result.stdout)
        self.assertIn('bench configuration remains separate', result.stdout)
        values = json.loads((self.root / 'knob-values').read_text())
        self.assertEqual(values['sr/diagnostics/naff/common/PipelineStages'], '31')
        for g in GROUPS:
            self.assertEqual(values[f'sr/diagnostics/naff/g{g}/WaterfallMask'], '4')

    def test_stalled_data_fails_for_every_required_source(self):
        self.start_chain()
        for name in ('master0','master1','master2','master3','master4','master5','ebpm',
                     *(f'naff{g}' for g in GROUPS)):
            with self.subTest(source=name):
                flag = self.root / ('stall-' + name)
                flag.touch()
                result = self.invoke('check')
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('not advancing: ' + name, result.stderr)
                flag.unlink()

    def test_missing_resources_registrations_labels_and_stages_fail(self):
        self.start_chain()
        paths = ['shm0','shm1','shm2','shm3','shm4','shm5','tsc','ebpm-shm','naff-shm']
        for resource in paths:
            with self.subTest(resource=resource):
                path = self.root / resource
                saved = path.read_bytes(); path.unlink()
                result = self.invoke('check')
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('shared resource', result.stderr)
                path.write_bytes(saved)
        for port in range(6):
            path = self.root / f'logs/master{port}.log'
            saved = path.read_text()
            path.write_text(saved.replace('slave 7: registrato','no drain'))
            result = self.invoke('check')
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(f'master{port} DRAIN registration', result.stderr)
            path.write_text(saved)
        for unit in ('rtchain-prepare','rtchain-producer-preflight','rtchain-producer-live','rtchain-final-live'):
            flag = self.root / f'fail-{unit}.service'; flag.touch()
            result = self.invoke('check')
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('required successful stage', result.stderr)
            flag.unlink()
        path = self.root / 'naff-shm'
        labels = json.loads(path.read_text()); labels['naff89'] = []
        path.write_text(json.dumps(labels))
        result = self.invoke('check')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('naff89: missing or incorrect published labels', result.stderr)

    def test_incomplete_naff_cannot_finish_startup(self):
        self.start_chain()
        for g in GROUPS:
            path = self.root / f'logs/naff{g}.log'
            path.write_text('still starting\n')
            result = self.fixture.invoke('wait', f'naff{g}', '--pid', self.fixture.processes[f'naff{g}'].pid)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('spectrum ring labelled', result.stderr)
            path.write_text('spectrum ring labelled\n')
        (self.root / 'unpublished-key').touch()
        result = self.invoke('check')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('unpublished key', result.stderr)

    def test_missing_failed_incomplete_or_hung_probe_never_succeeds(self):
        self.start_chain()
        probe = Path(self.config['tools']['rtchain_probe'])
        saved = probe.read_text()
        for content in ('#!/usr/bin/env python3\nimport sys\nsys.exit(7)\n',
                        '#!/usr/bin/env python3\nimport time\ntime.sleep(5)\n',
                        '#!/usr/bin/env python3\nprint("{}")\n',
                        '#!/usr/bin/env python3\nprint("invalid json")\n'):
            probe.write_text(content)
            result = self.invoke('check')
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn('readiness succeeded', result.stdout)
        probe.unlink()
        result = self.invoke('check')
        self.assertNotEqual(result.returncode, 0)
        result = self.fixture.invoke('preflight')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('missing functional probe', result.stderr)
        probe.write_text(saved); probe.chmod(0o755)

    def test_final_live_failure_and_missing_producer_are_diagnostic(self):
        self.start_chain()
        (self.root / 'fail-knobs').touch()
        result = self.invoke('live')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('naff-knobs', result.stderr)
        self.fixture.processes['naff89'].terminate()
        self.fixture.processes['naff89'].wait(timeout=5)
        result = self.invoke('check')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('readiness succeeded', result.stdout)
        self.assertIn('naff89', result.stderr)

    def test_creator_rejects_stale_resources_and_attacher_requires_creator(self):
        path = self.root / 'naff-shm'
        path.write_text('stale labels')
        result = self.fixture.invoke('launch', 'naff1')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('expected new creator resource', result.stderr)
        self.assertEqual(path.read_text(), 'stale labels')
        path.unlink()
        result = self.fixture.invoke('launch', 'naff9')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('expected existing creator resource', result.stderr)

    def test_generator_orders_naffs_and_blocks_invalid_contract(self):
        result = self.fixture.invoke_generator()
        self.assertEqual(result.returncode, 0, result.stderr)
        previous = 'rtchain-producer-live.service'
        for g in GROUPS:
            unit = f'rtchain-naff@{g}.service'
            content = (self.root / 'generated' / f'{unit}.d/70-readiness.conf').read_text()
            self.assertIn('Requires=' + previous, content)
            self.assertIn('After=' + previous, content)
            placement = (self.root / 'generated' / f'{unit}.d/60-producer.conf').read_text()
            self.assertIn('CPUSchedulingPolicy=other', placement)
            previous = unit
        creator = (self.root / 'generated/rtchain-naff@1.service.d/70-readiness.conf').read_text()
        self.assertIn('ExecStartPost=/bin/sleep 8', creator)
        self.assertIn('wait naff%i --pid $MAINPID', creator)
        # Check the installed barrier's failure dependencies as well as host
        # drop-ins: ordering alone would allow a failed live step to be ignored.
        for filename in ('rtchain-ready.service', 'rtchain-final-live.service'):
            content = (ROOT / 'systemd' / filename).read_text()
            required = next(line.removeprefix('Requires=') for line in content.splitlines()
                            if line.startswith('Requires=')).split()
            after = next(line.removeprefix('After=') for line in content.splitlines()
                         if line.startswith('After=')).split()
            for dependency in ('rtchain-prepare.service', 'rtchain-producer-live.service',
                               *(f'rtchain-naff@{g}.service' for g in GROUPS)):
                self.assertIn(dependency, required)
                self.assertIn(dependency, after)
            if filename == 'rtchain-ready.service':
                self.assertIn('rtchain-final-live.service', required)
                self.assertIn('rtchain-final-live.service', after)
        original = copy.deepcopy(self.config)
        for mutate in (
            lambda c:c['producers']['naffs'].pop(),
            lambda c:c['readiness'].pop('labels'),
            lambda c:c['readiness']['not_applicable'].pop('compaction'),
            lambda c:c['naff-knobs'].update(groups=[1]),
            lambda c:c['naff-knobs'].update(pipeline_stages=23),
        ):
            self.config.clear(); self.config.update(copy.deepcopy(original)); mutate(self.config)
            result = self.fixture.invoke_generator()
            self.assertNotEqual(result.returncode, 0)
            content = (self.root / 'generated/rtchain-producer-preflight.service.d/60-producer.conf').read_text()
            self.assertIn('ExecStartPre=/bin/false', content)


if __name__ == '__main__':
    unittest.main()
