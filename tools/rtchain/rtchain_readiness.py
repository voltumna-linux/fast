"""Final startup barrier: all producers, live settings and observed data flow."""
import json
import os
from pathlib import Path
import runpy
import subprocess
import time

from rtchain_config import absolute
import rtchain_producers as producers

GROUPS = tuple(range(1, 90, 8))
LIVE = ('naff-knobs', 'real-beam', 'compaction', 'fair-server', 'tick-revalidate')
STAGES = ('rtchain-prepare.service', 'rtchain-producer-preflight.service',
          'rtchain-producer-live.service')


def validate(config):
    cfg = config['readiness']
    naffs = config['producers']['naffs']
    if [n['group'] for n in naffs] != list(GROUPS):
        raise ValueError('exactly twelve naffs in group order 1,9,..,89 required')
    if len({n['shm'] for n in naffs}) != 1:
        raise ValueError('twelve naffs must share one fleet resource')
    if naffs[0]['shm'] in {producers.entries(config)[n]['shm'] for n in producers.NAMES}:
        raise ValueError('naff fleet resource collides with another producer')
    for n in naffs:
        env = producers.environment(config, f"naff{n['group']}")
        if not env.get('NAFF_CCD_PREFIX'):
            raise ValueError('explicit qualified NAFF_CCD_PREFIX required')
        if not n['ready_keys'] or 'spectrum ring labelled' not in n['ready_log']:
            raise ValueError(f"naff{n['group']}: published keys and spectrum label evidence required")
    if len({producers.environment(config, f"naff{n['group']}")['NAFF_CCD_PREFIX'] for n in naffs}) != 1:
        raise ValueError('naffs must use the same fleet prefix')
    if cfg['live'] != [op for op in LIVE if op in cfg['live']]:
        raise ValueError('invalid or unordered final live operations')
    for op in LIVE:
        if (op in cfg['live']) == (op in cfg['not_applicable']):
            raise ValueError(f'{op}: select final live operation or give not_applicable reason')
        if op in cfg['not_applicable'] and not str(cfg['not_applicable'][op]).strip():
            raise ValueError(f'{op}: empty not_applicable reason')
    if 'naff-knobs' not in cfg['live']:
        raise ValueError('naff-knobs is mandatory')
    knobs = config['naff-knobs']
    if knobs['groups'] != list(GROUPS) or knobs['bases'] != [cfg['naff_base']]:
        raise ValueError('naff-knobs must cover the complete fleet and all twelve groups')
    if knobs['pipeline_stages'] != 31:
        raise ValueError('naff PipelineStages=31 required, including sidebands')
    if not 0 < cfg['sample_interval'] <= 30 or not cfg['sample_interval'] < cfg['timeout'] <= 120:
        raise ValueError('invalid functional sampling interval/timeout')
    absolute(config['tools']['rtchain_probe'])
    labels = cfg['labels']
    if set(labels) != {f'naff{g}' for g in GROUPS} or any(
            not isinstance(v, list) or len(v) < 2 or len(v) != len(set(v)) or
            any(not isinstance(label, str) or not label for label in v) for v in labels.values()):
        raise ValueError('explicit spectrum and tune labels required for each naff')
    return [config['tools']['rtchain_probe']]


def stages(config, final=False):
    for unit in (*STAGES, *(('rtchain-final-live.service',) if final else ())):
        state = producers.command(config, 'systemctl', 'show', '-p', 'ActiveState', '--value', unit)
        result = producers.command(config, 'systemctl', 'show', '-p', 'Result', '--value', unit)
        if state != 'active' or result != 'success':
            raise ValueError(f'{unit}: required successful stage, got {state}/{result}')


def resources(config):
    pids = {}
    for name in producers.entries(config):
        try:
            pid = producers.main_pid(config, name)
            pending = producers.evidence(config, name, pid)
        except Exception as error:
            raise ValueError(f'{name}: {error}') from error
        if pending:
            raise ValueError(f'{name}: ' + ', '.join(pending))
        pids[name] = pid
    return pids


def live(config):
    stages(config)
    resources(config)
    ops = runpy.run_path(str(Path(__file__).with_name('rtchain-ops')))
    os.environ['LD_LIBRARY_PATH'] = ':'.join(config['library_path'])
    for op in config['readiness']['live']:
        try:
            ops['OPERATIONS'][op][1](config)
        except Exception as error:
            raise ValueError(f'{op}: {error}') from error
        print(f'{op}: completed', flush=True)
    print('final producer live steps completed; functional readiness still required')


def sample(config, pids, deadline):
    """The qualified adapter must read producer buffers directly, without Tango.

    The manifest ties counters and labels to this startup's exact resources/PIDs.
    Never accept process counts, log lines, wall clocks or synthetic counters.
    """
    manifest = {'version': 1, 'sources': {
        name: {'pid': pids[name], 'shm': item['shm'],
               'labels': config['readiness']['labels'].get(name, []),
               **({'group': item['group']} if name.startswith('naff') else {})}
        for name, item in producers.entries(config).items() if name != 'tsc'}}
    result = subprocess.run([config['tools']['rtchain_probe']], input=json.dumps(manifest),
                            capture_output=True, text=True,
                            timeout=max(0.001, deadline - time.monotonic()),
                            env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin',
                                 'LD_LIBRARY_PATH': ':'.join(config['library_path'])})
    if result.returncode:
        raise ValueError(f'functional probe: exit {result.returncode}: {result.stderr.strip()}')
    output = json.loads(result.stdout)
    if not isinstance(output, dict) or set(output) != set(manifest['sources']):
        raise ValueError('functional probe must cover six masters, ebpm and all twelve naffs')
    for name, expected in manifest['sources'].items():
        item = output[name]
        if not isinstance(item, dict) or type(item.get('sequence')) is not int or item['sequence'] < 0:
            raise ValueError(f'{name}: invalid data sequence from functional probe')
        if not isinstance(item.get('labels'), list) or item['labels'] != expected['labels']:
            raise ValueError(f'{name}: missing or incorrect published labels')
    if time.monotonic() >= deadline:
        raise ValueError('functional probe exceeded readiness timeout')
    return output


def check(config):
    stages(config, final=True)
    pids = resources(config)
    cfg = config['readiness']
    deadline = time.monotonic() + cfg['timeout']
    first = sample(config, pids, deadline)
    time.sleep(cfg['sample_interval'])
    second = sample(config, pids, deadline)
    stalled = [name for name in first if second[name]['sequence'] <= first[name]['sequence']]
    if stalled:
        raise ValueError('realtime data not advancing: ' + ', '.join(stalled))
    stages(config, final=True)
    if resources(config) != pids:
        raise ValueError('producer MainPID changed during functional readiness')
    print('realtime readiness succeeded: preparation, six masters, ebpm registrations, twelve naffs and data flow; bench configuration remains separate')


def generate(config, root):
    mounts = validate(config)
    previous = 'rtchain-producer-live.service'
    for group in GROUPS:
        unit = f'rtchain-naff@{group}.service'
        path = root / f'{unit}.d/70-readiness.conf'
        path.parent.mkdir(parents=True, exist_ok=True)
        content = f'[Unit]\nRequires={previous}\nAfter={previous}\n'
        if group == GROUPS[0]:
            content += ('[Service]\nExecStartPost=\n'
                        f'ExecStartPost={Path(__file__).parent}/rtchain-producer wait naff%i --pid $MAINPID\n'
                        'ExecStartPost=/bin/sleep 8\n')
        path.write_text(content)
        previous = unit
    for unit in ('rtchain-ready.service', 'rtchain-producer-preflight.service'):
        path = root / f'{unit}.d/70-readiness.conf'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('[Unit]\nRequiresMountsFor=' + ' '.join(mounts) + '\n')
