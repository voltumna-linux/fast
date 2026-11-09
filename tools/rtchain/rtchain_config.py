"""Local host contract shared by the boot generator and preparation command."""
import json
from pathlib import Path
import re
import socket


PREPARE = ('mounts', 'hugepages', 'libraries', 'registry', 'kernel',
           'system-cpuset', 'kernel-jail', 'network', 'x710')


def cpu_set(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*', value):
        raise ValueError(f'invalid resource list: {value!r}')
    result = set()
    for item in value.split(','):
        first, _, last = item.partition('-')
        if int(last or first) < int(first):
            raise ValueError(f'reversed resource range: {item}')
        result.update(range(int(first), int(last or first) + 1))
    return result


def absolute(value):
    # Keep unit syntax unambiguous, including specifiers and word separation.
    if not isinstance(value, str) or not re.fullmatch(r'/[a-zA-Z0-9_./:+-]+', value):
        raise ValueError(f'expected absolute path without unit metacharacters: {value!r}')
    if '..' in Path(value).parts:
        raise ValueError(f'parent traversal: {value}')
    return value


def load(path=None):
    host = socket.gethostname().split('.')[0]
    config = json.loads(Path(path or f'/etc/fast/hosts/{host}.json').read_text())
    if config['host'] != host or config.get('commissioned') is not True:
        raise ValueError('host mismatch or configuration not commissioned')
    names = config['phases']['prepare']
    omitted = config['not_applicable']
    if not names or len(names) != len(set(names)) or any(n not in PREPARE for n in names):
        raise ValueError('invalid prepare phase')
    for name in PREPARE:
        if (name in names) == (name in omitted):
            raise ValueError(f'{name}: select operation or give not_applicable reason')
        if name in names and not isinstance(config[name], dict):
            raise ValueError(f'{name}: missing settings')
        if name in omitted and not str(omitted[name]).strip():
            raise ValueError(f'{name}: empty not_applicable reason')
    for required in ('mounts', 'hugepages', 'libraries', 'registry', 'system-cpuset'):
        if required not in names:
            raise ValueError(f'{required}: required for FAST preparation')
    if names.index('mounts') > min(names.index(n) for n in ('libraries', 'registry')):
        raise ValueError('mounts must precede libraries and registry')
    if names.index('hugepages') > names.index('registry'):
        raise ValueError('hugepages must precede registry')
    if 'x710' in names and ('kernel-jail' not in names or names.index('kernel-jail') > names.index('x710')):
        raise ValueError('kernel-jail must precede x710')
    if not config['mounts']['entries'] or not config['hugepages']['pools'] or not config['libraries']['nodes']:
        raise ValueError('empty mounts, hugepage pools or library nodes')
    for entry in config['mounts']['entries']:
        absolute(entry['target'])
        if entry['type'] not in ('tmpfs', 'hugetlbfs'):
            raise ValueError('external filesystems belong in mount units/fstab')
    if not any(e['type'] == 'hugetlbfs' and e['target'] == config['registry']['mount']
               for e in config['mounts']['entries']):
        raise ValueError('registry hugetlbfs must be in mounts')
    log_directory = absolute(config['log_directory'])
    if not any(e['type'] == 'tmpfs' and e['target'] == log_directory
               for e in config['mounts']['entries']):
        raise ValueError('log_directory requires a tmpfs mount')
    for pool in config['hugepages']['pools']:
        absolute(pool['path'])
        if type(pool['count']) is not int or pool['count'] <= 0:
            raise ValueError('hugepage count must be positive')
    mounts = [absolute(p) for p in config['requires_mounts_for']]
    if not mounts:
        raise ValueError('requires_mounts_for must declare external resources')
    for tool in config['tools'].values():
        mounts.append(absolute(tool))
    for tool in ('libhot_setup', 'cruda_init'):
        absolute(config['tools'][tool])
    if 'kernel-jail' in names:
        absolute(config['tools']['kernel_jail'])
    if not config['library_path']:
        raise ValueError('explicit library_path required')
    for path in config['library_path']:
        mounts.append(absolute(path))
    resources = config['resources']
    system = cpu_set(config['system-cpuset']['cpus'])
    if set(config['system-cpuset']['units']) != {'system.slice', 'user.slice', 'init.scope'}:
        raise ValueError('system-cpuset must cover system.slice, user.slice and init.scope')
    for slice_name in ('rtchain', 'tango'):
        resource = resources[slice_name]
        cpus = cpu_set(resource['cpus'])
        nodes = cpu_set(resource['memory_nodes'])
        if cpus & system:
            raise ValueError(f'{slice_name}: CPU overlap with system confinement')
        if int(config['registry']['node']) not in nodes:
            raise ValueError(f'{slice_name}: shared registry node must remain accessible')
    if not set(map(int, config['libraries']['nodes'])) <= cpu_set(resources['rtchain']['memory_nodes']):
        raise ValueError('preparation slice must allow library NUMA nodes')
    if not set(config['time_sync_units']):
        raise ValueError('explicit clock synchronization units required')
    for unit in config['time_sync_units']:
        if not re.fullmatch(r'[a-zA-Z0-9_.@-]+\.service', unit):
            raise ValueError(f'invalid time sync unit: {unit}')
    return config, sorted(set(mounts))
