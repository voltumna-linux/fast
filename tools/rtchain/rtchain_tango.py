"""Provision the delegated native-launch boundary and Tango device properties."""
import os
from pathlib import Path
import re

from rtchain_config import cpu_set

CGROUP = Path('/sys/fs/cgroup/tango.slice/starter.service')


def validate(config):
    settings = config['tango_servers']
    if not re.fullmatch(r'[a-zA-Z0-9_.-]+:[0-9]+', settings['tango_host']):
        raise ValueError('tango_host must be an explicit database host:port')
    port = int(settings['tango_host'].rsplit(':', 1)[1])
    if not 1 <= port <= 65535:
        raise ValueError('invalid Tango database port')
    available = config['resources']['tango']
    cpus = cpu_set(available['cpus'])
    nodes = cpu_set(available['memory_nodes'])
    shared = cpu_set(settings['shared_memory_nodes'])
    if not shared <= nodes or int(config['registry']['node']) not in shared:
        raise ValueError('shared memory nodes must include the registry and fit tango.slice')
    if not cpu_set(settings['starter_cpus']) <= cpus:
        raise ValueError('Starter CPUs outside tango.slice')
    names, groups = set(), {'supervisor'}
    if not settings['profiles']:
        raise ValueError('empty Tango server inventory')
    for profile in settings['profiles']:
        server, group = profile['server'], profile['group']
        if not re.fullmatch(r'[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+', server):
            raise ValueError(f'invalid executable/instance: {server}')
        if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]*', group):
            raise ValueError(f'invalid cgroup leaf: {group}')
        if server.lower() in names or group in groups:
            raise ValueError('duplicate server or cgroup leaf')
        names.add(server.lower())
        groups.add(group)
        if not cpu_set(profile['cpus']) <= cpus or max(cpu_set(profile['cpus'])) >= 1024:
            raise ValueError(f'{server}: CPUs outside Tango boundary or CPU_SETSIZE')
        if type(profile['numa_node']) is not int or not 0 <= profile['numa_node'] < 4096:
            raise ValueError(f'{server}: invalid NUMA node')
        if profile['numa_node'] not in nodes:
            raise ValueError(f'{server}: allocation node outside Tango boundary')
        scheduler, priority = profile['scheduler'], profile['priority']
        if type(priority) is not int or not (
                scheduler == 'other' and priority == 0 or
                scheduler in ('fifo', 'rr') and 1 <= priority <= 99):
            raise ValueError(f'{server}: invalid scheduler/priority')
    return settings


def properties(profile):
    return ['version=1', f'cgroup={CGROUP / profile["group"]}'] + [
        f'{key}={profile[key]}' for key in ('cpus', 'numa_node', 'scheduler', 'priority')]


def configure_db(config, apply=False, database=None):
    settings = validate(config)
    plan = {f'dserver/{p["server"]}': {'NativeLaunchProfile': properties(p)}
            for p in settings['profiles']}
    if not apply:
        return plan
    if database is None:
        import tango
        host, port = settings['tango_host'].rsplit(':', 1)
        database = tango.Database(host, int(port))
    # Check the complete inventory before the first write. Never create servers
    # or change their host, levels or executable properties implicitly.
    for profile in settings['profiles']:
        info = database.get_server_info(profile['server'])
        if info.host.lower() != config['host'].lower():
            raise ValueError(f'{profile["server"]}: database registration belongs to {info.host}')
        devices = [str(d).lower() for d in database.get_device_name(profile['server'], 'DServer')]
        if f'dserver/{profile["server"]}'.lower() not in devices:
            raise ValueError(f'{profile["server"]}: administrative device missing')
    for device, data in plan.items():
        database.put_device_property(device, data)
        if list(database.get_device_property(device, ['NativeLaunchProfile'])['NativeLaunchProfile']) != data['NativeLaunchProfile']:
            raise RuntimeError(f'{device}: property read-back mismatch (previous writes retained)')
    return plan


def provision(config):
    settings = validate(config)
    membership = Path('/proc/self/cgroup').read_text().strip()
    if membership != '0::/tango.slice/starter.service':
        raise RuntimeError('launch must run in the delegated starter.service root')
    if (CGROUP / 'cgroup.type').read_text().strip() != 'domain':
        raise RuntimeError('Starter requires a cgroup v2 domain')
    if 'cpuset' not in (CGROUP / 'cgroup.controllers').read_text().split():
        raise RuntimeError('cpuset controller not delegated to Starter')
    if set((CGROUP / 'cgroup.procs').read_text().split()) != {str(os.getpid())}:
        raise RuntimeError('delegated root contains other processes')
    leaves = [('supervisor', settings['starter_cpus'], config['resources']['tango']['memory_nodes'])]
    for profile in settings['profiles']:
        nodes = cpu_set(settings['shared_memory_nodes']) | {profile['numa_node']}
        leaves.append((profile['group'], profile['cpus'], ','.join(map(str, sorted(nodes)))))
    # Never rewrite cpuset.mems on populated groups: it could migrate shared
    # pages. Restart with surviving processes is intentionally unsupported.
    for name, _, _ in leaves:
        leaf = CGROUP / name
        if leaf.exists():
            events = dict(line.split() for line in (leaf / 'cgroup.events').read_text().splitlines())
            if events['populated'] != '0':
                raise RuntimeError(f'{leaf}: populated; require a clean boot')
    supervisor = CGROUP / 'supervisor'
    supervisor.mkdir(exist_ok=True)
    (supervisor / 'cgroup.procs').write_text('0\n')
    (CGROUP / 'cgroup.subtree_control').write_text('+cpuset\n')
    for name, cpus, nodes in leaves:
        leaf = CGROUP / name
        leaf.mkdir(exist_ok=True)
        (leaf / 'cpuset.mems').write_text(nodes + '\n')
        (leaf / 'cpuset.cpus').write_text(cpus + '\n')
        for field, expected in (('cpus', cpus), ('mems', nodes)):
            actual = (leaf / f'cpuset.{field}.effective').read_text().strip()
            if cpu_set(actual) != cpu_set(expected):
                raise RuntimeError(f'{leaf}: effective {field} {actual} differs from {expected}')
    os.sched_setaffinity(0, cpu_set(settings['starter_cpus']))
    # Explicit host configuration wins over /etc/tangorc and ambient defaults.
    os.environ['TANGO_HOST'] = settings['tango_host']
    if os.environ.get('TANGO_CLUSTER', config['host']) != config['host']:
        raise ValueError('TANGO_CLUSTER differs from the commissioned host')
