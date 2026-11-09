"""Static producer configuration and startup checks (no Tango dependencies)."""
import fnmatch
import ipaddress
import os
from pathlib import Path
import re
import runpy
import subprocess
import time

from rtchain_config import absolute, cpu_set

NAMES = ('tsc', *(f'master{p}' for p in range(6)), 'ebpm')
LIVE = ('e810', 'master-knobs', 'tx-rate')


def entries(config):
    cfg = config['producers']
    return dict(zip(NAMES, [cfg['tsc'], *cfg['masters'], cfg['ebpm']]))


def validate(config):
    """Validate without touching external mounts; also return their paths."""
    cfg = config['producers']
    if [m['port'] for m in cfg['masters']] != list(range(6)):
        raise ValueError('exactly six masters in physical port order 0..5 required')
    mounts = []
    for tool in ('numactl', 'libhot_run', 'dpdk_devbind'):
        mounts.append(absolute(config['tools'][tool]))
    rt = config['resources']['rtchain']
    for name, item in entries(config).items():
        mounts.append(absolute(item['executable']))
        if not isinstance(item['args'], list) or any(not isinstance(a, str) or '\0' in a for a in item['args']):
            raise ValueError(f'{name}: invalid executable arguments')
        if item['node'] not in config['libraries']['nodes']:
            raise ValueError(f'{name}: library node not prepared')
        if not cpu_set(item['cpus']) <= cpu_set(rt['cpus']):
            raise ValueError(f'{name}: CPUs outside rtchain.slice')
        if int(item['node']) not in cpu_set(rt['memory_nodes']):
            raise ValueError(f'{name}: memory node outside rtchain.slice')
        if item['policy'] not in ('other', 'fifo', 'rr') or type(item['priority']) is not int:
            raise ValueError(f'{name}: invalid scheduling policy')
        if not (item['priority'] == 0 if item['policy'] == 'other' else 1 <= item['priority'] <= 99):
            raise ValueError(f'{name}: invalid scheduling priority')
        if not 0 < item['timeout'] <= 120:
            raise ValueError(f'{name}: timeout must be in (0,120] seconds')
        absolute(item['shm'])
        for path in item['files']:
            mounts.append(absolute(path))
        if not all(isinstance(s, str) and s for s in item['ready_keys'] + item['ready_log']):
            raise ValueError(f'{name}: invalid readiness evidence')
        for key, value in environment(config, name).items():
            if not re.fullmatch(r'[A-Z_][A-Z_0-9]*', key) or not isinstance(value, str) or '\0' in value:
                raise ValueError(f'{name}: invalid environment entry {key}')
    for field in ('prefix', 'name', 'app_id', 'bdf', 'ip', 'shm', 'eal'):
        values = [m[field] for m in cfg['masters']]
        if len(set(values)) != 6:
            raise ValueError(f'masters require distinct {field}')
    for master in cfg['masters']:
        for field in ('prefix', 'name'):
            if not re.fullmatch(r'[a-zA-Z0-9_-]+', master[field]):
                raise ValueError(f'invalid master {field}')
        if type(master['app_id']) is not int or master['app_id'] <= 2:
            raise ValueError('master app_id must not collide with legacy master/ebpm IDs')
        if not re.fullmatch(r'[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]', master['bdf']):
            raise ValueError('invalid PCI BDF')
        ipaddress.IPv4Address(master['ip'])
        absolute(master['eal'])
        mounts.append(absolute(master['cruda_template']))
        controls = cpu_set(master['control_cpus'])
        if not controls <= cpu_set(master['cpus']) or not master['control_threads']:
            raise ValueError('master control CPUs/threads required within its CPU set')
        env = environment(config, f"master{master['port']}")
        for key in ('DPDK_CORE_LIST', 'DPDK_RXPORT_CONFIG', 'DPDK_TXPORT_CONFIG'):
            if not env.get(key):
                raise ValueError(f'master{master["port"]}: explicit {key} required')
    ebpm = cfg['ebpm']
    mounts.extend((absolute(ebpm['calibration_base'] + '.csv'),
                   absolute(ebpm['calibration_base'] + '_chain.csv'), absolute(ebpm['cruda_conf'])))
    if type(ebpm['units_per_port']) is not int or not 0 <= ebpm['units_per_port'] <= 256:
        raise ValueError('units_per_port must be zero (real beam) or a positive remap stride')
    if type(ebpm['catalog_units']) is not int or ebpm['catalog_units'] <= 0:
        raise ValueError('positive catalog_units required')
    if ebpm['units_per_port'] and ebpm['catalog_units'] != 6 * ebpm['units_per_port']:
        raise ValueError('catalog_units must equal six remap strides')
    if ebpm['fill_id'] == ebpm['drain_id'] or not all(type(ebpm[k]) is int and ebpm[k] > 0
                                                   for k in ('fill_id', 'drain_id')):
        raise ValueError('distinct positive FILL/DRAIN IDs required')
    if not cfg['free_hugepages']:
        raise ValueError('explicit free hugepage budget required')
    for pool in cfg['free_hugepages']:
        absolute(pool['path'])
        if type(pool['count']) is not int or pool['count'] <= 0:
            raise ValueError('positive free hugepage budget required')
    absolute(cfg['pci_root'])
    if len(set(cfg['live'])) != len(cfg['live']) or any(op not in LIVE for op in cfg['live']):
        raise ValueError('invalid producer live operations')
    for op in LIVE:
        if (op in cfg['live']) == (op in cfg['not_applicable']):
            raise ValueError(f'{op}: select operation or give not_applicable reason')
        if op in cfg['not_applicable'] and not str(cfg['not_applicable'][op]).strip():
            raise ValueError(f'{op}: empty not_applicable reason')
    if 'e810' in cfg['live'] and set(config['e810']['bdfs']) != {m['bdf'] for m in cfg['masters']}:
        raise ValueError('E810 tuning must cover the six master BDFs')
    if 'master-knobs' in cfg['live']:
        keys = config['master-knobs']['values']
        if not all(any(k.startswith(f"sr/dpdkmaster/{m['name']}/") for k in keys) for m in cfg['masters']):
            raise ValueError('master-knobs must cover all six masters')
    if 'tx-rate' in cfg['live'] and not 0 <= float(cfg['tx-rate']['percent']) <= 100:
        raise ValueError('synthetic TX percent must be in [0,100]')
    env = environment(config, 'ebpm')
    if env.get('DPDK_EBPM_TX_SYNTH') not in ('0', '1'):
        raise ValueError('explicit DPDK_EBPM_TX_SYNTH=0 or 1 required')
    if env.get('DPDK_EBPM_TX_SYNTH') == '1':
        ports = cpu_set(env['DPDK_EBPM_TX_SYNTH_PORTS'])
        if not ports <= set(range(6)):
            raise ValueError('synthetic TX ports must be explicit global ports 0..5')
    return sorted(set(mounts))


def log_path(config, name):
    return Path(config['log_directory']) / f'{name}.log'


def environment(config, name):
    cfg = config['producers']
    item = entries(config)[name]
    env = {**cfg.get('common_environment', {}), **item['environment']}
    managed = {}
    if name.startswith('master'):
        managed = {'DPDK_FILE_PREFIX': item['prefix'], 'DPDK_SHM_SUFFIX': '_' + item['prefix'],
                   'DPDK_PORT_MASK': '0x1', 'DPDK_TAG_PORT_MASK': '0x1',
                   'DPDK_PORT00_IP_ADDR': item['ip'],
                   'DPDK_ALLOW': item['bdf'] + ',' + item.get('devargs', 'safe-mode-support=1'),
                   'DPDK_MASTER_CTRL_CORES': item['control_cpus'],
                   'DPDK_MASTER_CRUDA_CONF': str(log_path(config, name).with_suffix('.conf'))}
    elif name == 'ebpm':
        managed = {'DPDK_EBPM_MASTERS': ','.join(m['shm'] for m in cfg['masters']),
                   'DPDK_EBPM_SPLIT_PORTS': '0,1,2,3,4,5', 'DPDK_SLAVE_SHM_BASE': item['shm'],
                   'DPDK_SLAVE_ID': str(item['fill_id']), 'DPDK_SLAVE_ID_DRAIN': str(item['drain_id']),
                   'DPDK_EBPM_CALIBRATION_BASE': item['calibration_base'],
                   'DPDK_EBPM_CRUDA_CONF': item['cruda_conf'],
                   'DPDK_EBPM_TEST_UNITS_PER_PORT': str(item['units_per_port'])}
    if name.startswith('master'):
        # The callback library executes inside each master. Give all six the
        # same decoder/catalog settings that ebpm uses, with no lost variables.
        managed.update({k: v for k, v in environment(config, 'ebpm').items()
                        if k.startswith('DPDK_EBPM_') and k != 'DPDK_EBPM_MASTERS'})
    if name != 'tsc':
        managed['DPDK_NUMA_NODE'] = str(item['node'])
    managed['LD_LIBRARY_PATH'] = ':'.join(config['library_path'])
    for key, value in managed.items():
        if key in env and env[key] != value:
            raise ValueError(f'{name}: conflicting managed environment {key}')
    return {**env, **managed}


def command(config, tool, *args, timeout=120):
    result = subprocess.run([config['tools'].get(tool, tool), *map(str, args)],
                            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout,
                            env={**os.environ, 'LD_LIBRARY_PATH': ':'.join(config['library_path'])})
    if result.returncode:
        raise ValueError(f'{tool}: exit {result.returncode}: {result.stdout.strip()}')
    return result.stdout.strip()


def catalog_rows(path):
    rows = []
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if line and not line.startswith('#'):
            rows.append(dict(re.findall(r'-(\w+)\s*=\s*([^;\s]+)', line)))
    return rows


def preflight(config):
    cfg = config['producers']
    for name, item in entries(config).items():
        if not os.access(item['executable'], os.X_OK):
            raise ValueError(f'{name}: missing executable {item["executable"]}')
        if Path(item['shm']).exists() or (name.startswith('master') and Path(item['eal']).exists()):
            raise ValueError(f'{name}: existing resource; start requires a clean boot')
        for path in item['files']:
            if not Path(path).is_file():
                raise ValueError(f'{name}: missing input {path}')
    for pool in cfg['free_hugepages']:
        if int(Path(pool['path']).read_text()) < pool['count']:
            raise ValueError(f'insufficient free hugepages: {pool["path"]}')
    ebpm = cfg['ebpm']
    if b'DPDK_EBPM_MASTERS' not in Path(ebpm['executable']).read_bytes():
        raise ValueError('ebpm binary does not advertise multi-master support')
    count = ebpm['catalog_units']
    expected = set(range(1, count+1))
    chain = [int(row['fpgaidx']) for row in catalog_rows(ebpm['calibration_base'] + '_chain.csv')]
    pickups = [(int(row['fpgaidx']), row['pos']) for row in catalog_rows(ebpm['calibration_base'] + '.csv')]
    if len(chain) != count or set(chain) != expected or len(pickups) != 2*count or set(pickups) != {
            (i, pos) for i in expected for pos in ('A', 'B')}:
        raise ValueError('catalog does not cover all generated FPGA indices and A/B pickups')
    if not Path(ebpm['cruda_conf']).is_file():
        raise ValueError('missing ebpm cruda schema')
    # Check every group before changing any driver. A group cannot be split
    # between independent master processes.
    for master in cfg['masters']:
        device = Path(cfg['pci_root']) / master['bdf']
        if sorted(p.name for p in (device / 'iommu_group/devices').iterdir()) != [master['bdf']]:
            raise ValueError(f'{master["bdf"]}: requires its own IOMMU group')
    for master in cfg['masters']:
        device = Path(cfg['pci_root']) / master['bdf']
        if (device / 'driver').resolve().name != 'vfio-pci':
            command(config, 'dpdk_devbind', '--bind=vfio-pci', master['bdf'])
        if (device / 'driver').resolve().name != 'vfio-pci':
            raise ValueError(f'{master["bdf"]}: vfio-pci binding not effective')
    for name, master in entries(config).items():
        if not name.startswith('master'):
            continue
        text = Path(master['cruda_template']).read_text()
        for key, value in (('name', master['name']), ('app_id', master['app_id']),
                           ('manifest', log_path(config, name).with_suffix('.tsv'))):
            text, replaced = re.subn(r'(?m)(?<!\S)-' + key + r'=[^;\n]*',
                                     f'-{key}={value}', text)
            if replaced != 1:
                raise ValueError(f'{name}: cruda template must contain one -{key}= field')
        log_path(config, name).with_suffix('.conf').write_text(text)
    print('producer preflight completed', flush=True)


def launch(config, name):
    item = entries(config)[name]
    if Path(item['shm']).exists() or (name.startswith('master') and Path(item['eal']).exists()):
        raise ValueError(f'{name}: refusing existing resources; clean boot required')
    # Explicit environment, no inherited DPDK variables or historical defaults.
    env = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', **environment(config, name)}
    fd = os.open(log_path(config, name), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    os.dup2(fd, 1)
    os.dup2(fd, 2)
    os.close(fd)
    # Both qualified helpers must exec their command, preserving MainPID.
    os.execve(config['tools']['libhot_run'], [config['tools']['libhot_run'], str(item['node']), '--',
              config['tools']['numactl'], f"--membind={item['node']}", f"--physcpubind={item['cpus']}",
              '--', item['executable'], *item['args']], env)


def process_environment(pid):
    fields = Path(f'/proc/{pid}/environ').read_bytes().split(b'\0')
    return dict(field.decode().split('=', 1) for field in fields if b'=' in field)


def alive(pid):
    # kill(0) also succeeds for zombies, which cannot publish anything further.
    state = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[0]
    if state == 'Z':
        raise ValueError(f'producer {pid} exited')


def evidence(config, name, pid, deadline=None):
    item = entries(config)[name]
    if deadline is None:
        deadline = time.monotonic() + item['timeout']
    alive(pid)
    log = log_path(config, name).read_text() if log_path(config, name).exists() else ''
    if 'FATAL' in log:
        raise ValueError(f'{name}: FATAL in current startup log')
    pending = []
    if not Path(item['shm']).is_file() or Path(item['shm']).stat().st_size == 0:
        pending.append('shared resource ' + item['shm'])
    if name.startswith('master') and not Path(item['eal']).is_dir():
        pending.append('EAL prefix ' + item['eal'])
    pending.extend('log evidence ' + text for text in item['ready_log'] if text not in log)
    if name == 'ebpm':
        for port in range(6):
            master_log = log_path(config, f'master{port}').read_text()
            for kind in ('fill', 'drain'):
                slave = item[kind + '_id']
                if not re.search(r'\bslave ' + str(slave) + r': registrato\b', master_log):
                    pending.append(f'master{port} {kind.upper()} registration')
        env = environment(config, name)
        if env.get('DPDK_EBPM_TX_SYNTH') == '1':
            mask = sum(1 << p for p in cpu_set(env['DPDK_EBPM_TX_SYNTH_PORTS']))
            masks = re.findall(r'porte del generatore: (0x[0-9a-fA-F]+) .*?port_base=(\d+)', log)
            if sorted((int(p), int(m, 16)) for m, p in masks) != [(p, mask) for p in range(6)]:
                pending.append('six explicit synthetic TX masks/port bases')
            if log.count('non genera, e non deve') != 6 - mask.bit_count():
                pending.append('explicit non-generating instances outside TX mask')
    if not pending:
        current = process_environment(pid)
        for key, value in environment(config, name).items():
            if current.get(key) != value:
                raise ValueError(f'{name}: live environment mismatch for {key}')
        for key in item['ready_keys']:
            output = command(config, 'crudaknob', key, timeout=max(0.001, deadline - time.monotonic()))
            if not re.search(r'^' + re.escape(key) + r'\s*=', output, re.MULTILINE):
                pending.append('unpublished key ' + key)
    if not pending and time.monotonic() >= deadline:
        raise ValueError(f'{name}: readiness timeout')
    return pending


def wait(config, name, pid):
    deadline = time.monotonic() + entries(config)[name]['timeout']
    while True:
        pending = evidence(config, name, pid, deadline)
        if not pending:
            print(f'{name}: resources verified' + ('; ebpm registered on six masters' if name == 'ebpm' else ''))
            return
        if time.monotonic() >= deadline:
            raise ValueError(f'{name}: timeout waiting for ' + ', '.join(pending))
        time.sleep(0.05)


def main_pid(config, name):
    unit = f'rtchain-master@{name[-1]}.service' if name.startswith('master') else f'rtchain-{name}.service'
    pid = int(command(config, 'systemctl', 'show', '-p', 'MainPID', '--value', unit))
    if pid <= 0:
        raise ValueError(f'{unit}: no live MainPID')
    return pid


def live(config):
    cfg = config['producers']
    for name in NAMES:
        pending = evidence(config, name, main_pid(config, name))
        if pending:
            raise ValueError(f'{name}: ' + ', '.join(pending))
    for master in cfg['masters']:
        name = f"master{master['port']}"
        pid = main_pid(config, name)
        tasks = list(Path(f'/proc/{pid}/task').iterdir())
        for pattern in master['control_threads']:
            matched = [t for t in tasks if fnmatch.fnmatchcase((t / 'comm').read_text().strip(), pattern)]
            if not matched:
                raise ValueError(f'{name}: missing control thread {pattern}')
            for task in matched:
                cpus = cpu_set(master['control_cpus'])
                os.sched_setaffinity(int(task.name), cpus)
                if os.sched_getaffinity(int(task.name)) != cpus:
                    raise ValueError(f'{name}: control thread affinity not applied')
    ops = runpy.run_path(str(Path(__file__).with_name('rtchain-ops')))
    os.environ['LD_LIBRARY_PATH'] = ':'.join(config['library_path'])
    for op in cfg['live']:
        if op == 'tx-rate':
            rate(config, ops)
        else:
            ops['OPERATIONS'][op][1](config)
    print('producer live steps completed; naff and final readiness still required')


def rate(config, ops):
    cfg = config['producers']['tx-rate']
    value = float(cfg['percent'])
    if not 0 <= value <= 100:
        raise ValueError('synthetic TX percent must be in [0,100]')
    at = int(time.time()) + 3
    for master in config['producers']['masters']:
        base = f"sr/dpdkmaster/{master['name']}/txsynth/"
        ops['write_knob'](config, base + 'ApplyAtS', at)
        ops['write_knob'](config, base + 'RatePct', value)
    if time.time() >= at:
        raise ValueError('missed common synthetic TX application time')
    deadline = time.monotonic() + config['producers']['ebpm']['timeout']
    while True:
        applied = [float(ops['knob'](config, f"sr/dpdkmaster/{m['name']}/txsynth/RateAppliedPct"))
                   for m in config['producers']['masters']]
        if time.time() >= at and all(actual == value for actual in applied):
            return
        if time.monotonic() >= deadline:
            raise ValueError('synthetic TX rate not applied on all six masters')
        time.sleep(0.05)


def generate(config, root):
    """Host drop-ins only; all services themselves are installed statically."""
    mounts = validate(config)
    for name, item in entries(config).items():
        unit = f'rtchain-master@{name[-1]}' if name.startswith('master') else f'rtchain-{name}'
        target = root / f'{unit}.service.d/60-producer.conf'
        target.parent.mkdir(parents=True, exist_ok=True)
        content = '[Unit]\nRequiresMountsFor=' + ' '.join(mounts) + '\n'
        if name.startswith('master') and name != 'master0':
            previous = f'rtchain-master@{int(name[-1])-1}.service'
            content += f'Requires={previous}\nAfter={previous}\n'
        content += (f"[Service]\nCPUAffinity={item['cpus'].replace(',', ' ')}\n"
                    f"CPUSchedulingPolicy={item['policy']}\nCPUSchedulingPriority={item['priority']}\n")
        target.write_text(content)
    target = root / 'rtchain-producer-preflight.service.d/60-producer.conf'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('[Unit]\nRequiresMountsFor=' + ' '.join(mounts) + '\n')
