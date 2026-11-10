#!/usr/bin/env python3
"""Run the readiness gate against real systemd, Starter and Tango test servers.

Requires root in a disposable systemd VM and a disposable Tango database.
See STARTUP.md. Never run on FAST in service.
Only uniquely named runtime units and database registrations are created.
"""
import argparse
import json
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import sys
import tempfile
import time
import uuid


def until(check, description, timeout=90):
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            if check():
                return
        except Exception as error:
            last_error = error
        time.sleep(0.1)
    raise AssertionError(f'Timeout: {description}; last error: {last_error}')


def indices(value):
    result = set()
    for part in value.split(','):
        first, _, last = part.partition('-')
        result.update(range(int(first), int(last or first) + 1))
    return sorted(result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tango-host', required=True, help='disposable database host:port')
    parser.add_argument('--starter', required=True, type=Path)
    parser.add_argument('--probe', required=True, type=Path)
    parser.add_argument('--fixtures', required=True, type=Path)
    parser.add_argument('--gate', required=True, type=Path, help='installed 70-realtime-ready.conf')
    parser.add_argument('--user', default='controls')
    args = parser.parse_args()
    if os.geteuid() != 0 or Path('/proc/1/comm').read_text().strip() != 'systemd':
        parser.error('requires root and systemd as PID 1 in a disposable VM')
    import tango

    identity = pwd.getpwnam(args.user)
    assert identity.pw_uid != 0, 'Starter must use an unprivileged test identity'
    profiles = json.loads(args.fixtures.read_text())['profiles']
    assert len(profiles) == 2
    assert indices(profiles[0]['cpus']) != indices(profiles[1]['cpus'])
    token = 'fastgate-' + uuid.uuid4().hex[:10]
    names = {part: token + '-' + part + '.service' for part in ('prepare', 'ready', 'starter', 'legacy')}
    for index, profile in enumerate(profiles):
        # Test cgroups live in the unit's own delegation, allowing controls to
        # migrate a child without write access to ancestors owned by systemd.
        profile['cgroup'] = f'/sys/fs/cgroup/system.slice/{names["starter"]}/probe{index}'
    root = Path(tempfile.mkdtemp(prefix=token + '-', dir='/var/tmp'))
    root.chmod(0o755)
    reports = root / 'reports'
    reports.mkdir()
    os.chown(reports, identity.pw_uid, identity.pw_gid)
    print(f'Artifacts: {root}', flush=True)
    # Copy helpers to a stable path readable by the service identity.
    helper = root / 'gate.py'
    helper.write_text('''import sys,time
from pathlib import Path
root, phase = Path(sys.argv[1]), sys.argv[2]
(root / (phase + '-entered')).write_text(str(time.time_ns()))
if phase == 'starter': sys.exit(0)
if phase == 'ready':
    deadline = time.monotonic() + 60
    while not (root / 'release').exists():
        if time.monotonic() > deadline: sys.exit(33)
        time.sleep(0.05)
if (root / 'mode').read_text() == phase + '-fail':
    sys.exit(31 if phase == 'prepare' else 32)
(root / (phase + '-success')).write_text(str(time.time_ns()))
''')
    launcher = root / 'launch.py'
    launcher.write_text('''import json,os,sys
from pathlib import Path
profiles = json.loads(Path(sys.argv[1]).read_text())
membership = Path('/proc/self/cgroup').read_text().strip().removeprefix('0::/')
group = Path('/sys/fs/cgroup') / membership
supervisor = group / 'supervisor'
supervisor.mkdir()
(supervisor / 'cgroup.procs').write_text('0\\n')
(group / 'cgroup.subtree_control').write_text('+cpuset\\n')
nodes = (group / 'cpuset.mems.effective').read_text()
for profile in profiles:
    leaf = Path(profile['cgroup'])
    assert leaf.parent == group
    leaf.mkdir()
    (leaf / 'cpuset.mems').write_text(nodes)
    (leaf / 'cpuset.cpus').write_text(profile['cpus'])
    def indices(value):
        result = set()
        for part in value.strip().split(','):
            first, _, last = part.partition('-')
            result.update(range(int(first), int(last or first) + 1))
        return result
    assert indices((leaf / 'cpuset.cpus.effective').read_text()) == indices(profile['cpus'])
    assert profile['numa_node'] in indices(nodes)
os.execv(sys.argv[2], sys.argv[2:])
''')
    profile_file = root / 'profiles.json'
    profile_file.write_text(json.dumps(profiles))
    for path in (helper, launcher, profile_file):
        path.chmod(0o644)
    # Starter's ExecStartPre runs as controls; gate stage files use a separate
    # service-owned directory, while the root-owned helper cannot be modified.
    state = root / 'state'
    state.mkdir()
    os.chown(state, identity.pw_uid, identity.pw_gid)
    for path in (args.starter, args.probe, args.gate):
        assert path.is_absolute() and path.is_file(), path
    python = Path(sys.executable).resolve()
    for path in (root, python, args.starter, args.probe):
        assert not any(c in str(path) for c in ' \t\n%"\\'), 'unit-unsafe path'
    assert not any(c in args.tango_host for c in ' \t\n%"\\'), 'unit-unsafe database address'
    db_host, db_port = args.tango_host.rsplit(':', 1)
    db = tango.Database(db_host, int(db_port))
    starter_device = f'test/starter/{token}'
    servers = [f'NativeProfileProbe/{token}{i}' for i in range(2)]
    created, unit_paths = [], []
    runtime = Path('/run/systemd/system')

    def ctl(*arguments, check=True):
        return subprocess.run(['systemctl', *arguments], text=True, capture_output=True,
                              check=check, timeout=100)

    def show(name):
        result = ctl('show', names[name], '-p', 'ActiveState', '-p', 'Result',
                     '-p', 'MainPID', '-p', 'ExecMainStartTimestampMonotonic', '-p', 'ExecMainStatus')
        return dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)

    def register(server, device, klass, level):
        info = tango.DbDevInfo()
        info.name, info.server, info._class = device, server, klass
        created.append(server)
        db.add_server(server, [info])
        info = tango.DbServerInfo()
        info.name, info.host, info.mode, info.level = server, token, 1, level
        db.put_server_info(info)

    def write_unit(name, content):
        path = runtime / name
        assert not path.exists()
        unit_paths.append(path)
        path.write_text(content)

    def no_consumers():
        assert not (state / 'starter-entered').exists(), 'Starter command entered before readiness'
        assert not list(reports.glob('*.json')), 'Tango server entered before readiness'
        status = show('starter')
        assert status['MainPID'] == '0' and status['ExecMainStartTimestampMonotonic'] == '0', status

    try:
        register('Starter/' + token, starter_device, 'Starter', 0)
        for index, server in enumerate(servers):
            register(server, f'test/profile/{token}{index}', 'NativeProfileProbe', index + 1)
            db.put_device_property('dserver/' + server, {'NativeLaunchProfile':
                ['version=1'] + [f'{key}={value}' for key, value in profiles[index].items()]})
        db.put_device_property(starter_device, {
            'StartDsPath': [str(args.probe.parent)], 'LogFileHome': str(reports),
            'StartServersAtStartup': True, 'EnableParallelStartup': False,
            'InterStartupLevelWait': 2, 'InterStartupServerWait': 0,
            'WaitForDriverStartup': 0,
        })
        for phase in ('prepare', 'ready'):
            dependency = (f'Requires={names["prepare"]}\nAfter={names["prepare"]}\n'
                          if phase == 'ready' else '')
            write_unit(names[phase], '[Unit]\n' + dependency +
                       f'[Service]\nType=oneshot\nUser={args.user}\nRemainAfterExit=yes\n'
                       f'ExecStart={python} {helper} {state} {phase}\nTimeoutStartSec=75\n')
        write_unit(names['starter'], '[Unit]\nDescription=Isolated real Starter readiness test\n'
                   f'[Service]\nType=exec\nUser={args.user}\nGroup={identity.pw_gid}\n'
                   f'Environment="TANGO_HOST={args.tango_host}" "TANGO_CLUSTER={token}" "DEBUG=true" '
                   f'"STARTER_PROFILE_TEST_DIR={reports}"\n'
                   f'ExecStartPre={python} {helper} {state} starter\n'
                   f'ExecStart={python} {launcher} {profile_file} {args.starter} {token}\n'
                   'Slice=system.slice\nDelegate=cpuset\nLimitRTPRIO=99\nLimitMEMLOCK=infinity\n'
                   'KillMode=control-group\nTimeoutStopSec=10\nRestart=no\n')
        gate_dir = runtime / (names['starter'] + '.d')
        gate_dir.mkdir()
        unit_paths.append(gate_dir)
        # Exercise the shipped dependency contract, translating only unit names
        # so a test cannot start the production realtime chain or Starter.
        gate = args.gate.read_text().replace('rtchain-ready.service', names['ready'])
        gate = gate.replace('rtchain.service', names['legacy'])
        (gate_dir / args.gate.name).write_text(gate)
        ctl('daemon-reload')
        for mode in ('prepare-fail', 'ready-fail', 'success'):
            for path in state.iterdir():
                path.unlink()
            (state / 'mode').write_text(mode)
            job = subprocess.Popen(['systemctl', 'start', names['starter']],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                if mode != 'prepare-fail':
                    until(lambda: (state / 'ready-entered').exists(), 'readiness entered')
                    # A held oneshot must prevent both Starter and its automatic
                    # levels, even while every upstream start has succeeded.
                    deadline = time.monotonic() + 2
                    while time.monotonic() < deadline:
                        no_consumers()
                        assert job.poll() is None, 'start job did not wait for readiness'
                        time.sleep(0.1)
                    (state / 'release').touch()
                output, error = job.communicate(timeout=100)
                (root / (mode + '-job.log')).write_text(output + error)
                if mode.endswith('-fail'):
                    assert job.returncode != 0, 'dependency failure was ignored'
                    no_consumers()
                    phase = mode.removesuffix('-fail')
                    failed = show(phase)
                    assert failed['Result'] == 'exit-code', failed
                    assert failed['ExecMainStatus'] == ('31' if phase == 'prepare' else '32'), failed
                    if phase == 'prepare':
                        assert not (state / 'ready-entered').exists()
                else:
                    assert job.returncode == 0, error
                    until(lambda: len(list(reports.glob('*.json'))) == 2, 'automatic startup levels')
                    ready_time = int((state / 'ready-success').read_text())
                    assert int((state / 'starter-entered').read_text()) >= ready_time
                    entry_times = []
                    for index, server in enumerate(servers):
                        path, = reports.glob(server.split('/')[1] + '.*.json')
                        entry_times.append(path.stat().st_mtime_ns)
                        value = json.loads(path.read_text())
                        profile = profiles[index]
                        wanted = indices(profile['cpus'])
                        assert value['cpus'] == value['thread_cpus'] == value['child_cpus'] == wanted, value
                        assert value['memory_policy'] == 2 and value['private_nodes'] == [profile['numa_node']], value
                        assert value['scheduler'] == {'other': 0, 'fifo': 1, 'rr': 2}[profile['scheduler']], value
                        assert value['priority'] == profile['priority'], value
                        assert value['thread_widen_result'] == value['child_widen_result'] == 0, value
                        assert value['cgroup'] == '0::/' + str(Path(profile['cgroup']).relative_to('/sys/fs/cgroup')), value
                        proxy = tango.DeviceProxy(f'tango://{args.tango_host}/dserver/{server}#dbase=yes')
                        until(lambda: proxy.ping() is not None, 'probe Tango device')
                    assert ready_time <= entry_times[0] < entry_times[1], entry_times
                    assert show('ready')['Result'] == 'success'
                snapshot = {part: show(part) for part in ('prepare', 'ready', 'starter')}
                (root / (mode + '-units.json')).write_text(json.dumps(snapshot, indent=2))
                print('PASS: ' + mode, flush=True)
            finally:
                ctl('stop', names['starter'], names['ready'], names['prepare'], check=False)
                if job.poll() is None:
                    job.kill()
                    job.communicate()
                # KillMode covers the delegated subtree. Also identify exact
                # test PIDs if a failed test left an unexpected survivor.
                for path in reports.glob('*.json'):
                    value = json.loads(path.read_text())
                    pid = value['pid']
                    try:
                        command = Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')
                        if any(s.split('/')[1].encode() in command for s in servers):
                            os.kill(pid, 9)
                    except (FileNotFoundError, ProcessLookupError):
                        pass
                journal = subprocess.run(['journalctl', '--no-pager', '-b',
                    *[arg for name in names.values() for arg in ('-u', name)]],
                    text=True, capture_output=True, timeout=30)
                (root / (mode + '-journal.log')).write_text(journal.stdout + journal.stderr)
                ctl('reset-failed', *names.values(), check=False)
    finally:
        ctl('stop', names['starter'], names['ready'], names['prepare'], check=False)
        for path in reversed(unit_paths):
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)
        ctl('daemon-reload')
        for server in reversed(created):
            db.delete_server(server)
    print(f'PASS: systemd → Starter → two automatic levels with native profiles; artifacts: {root}')


if __name__ == '__main__':
    main()
