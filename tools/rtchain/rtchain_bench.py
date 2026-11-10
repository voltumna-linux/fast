"""Functional post-Starter configuration, independent of realtime readiness."""
import math
import os
from pathlib import Path
import re
import runpy
import time


def device_name(name):
    if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+){2}', name):
        raise ValueError(f'expected database-relative Tango device: {name!r}')
    return name


def validate(config):
    bench = config['bench']
    if bench['enabled'] is not True:
        raise ValueError('bench configuration is not enabled')
    for name in ('timeout', 'poll_interval'):
        value = float(bench[name])
        if not math.isfinite(value) or not 0 < value <= 180:
            raise ValueError(f'bench {name} must be positive and at most 180 seconds')
    operations = bench['operations']
    if operations not in (['smart-rescan'], ['synthetic-beam', 'smart-rescan']):
        raise ValueError('bench operations must be optional synthetic-beam followed by smart-rescan')
    host = config['tango_servers']['tango_host']
    if not re.fullmatch(r'[A-Za-z0-9_.-]+:[0-9]+', host):
        raise ValueError('explicit host:port Tango database required')
    if config['smart-rescan']['tango_host'] != host:
        raise ValueError('bench and Starter Tango databases differ')
    devices = bench['devices']
    names = [device_name(d['name']) for d in devices]
    if not names or len(names) != len(set(names)):
        raise ValueError('explicit unique bench prerequisite devices required')
    for entry in devices:
        if not isinstance(entry['attributes'], list):
            raise ValueError('prerequisite attributes must be a list')
    rescans = config['smart-rescan']['devices']
    if not rescans or len(rescans) != len(set(rescans)):
        raise ValueError('explicit unique SmartRescan destinations required')
    if any(device_name(name) not in names for name in rescans):
        raise ValueError('every SmartRescan device must be a bench prerequisite')
    if not bench['keys'] or any(not isinstance(k, str) or not k.strip() for k in bench['keys']):
        raise ValueError('explicit published field keys required')
    if 'synthetic-beam' in operations:
        synth = config['synthetic-beam']
        if device_name(synth['device']) not in names:
            raise ValueError('simulator device must be a bench prerequisite')
        if not synth['sim_base'] or not synth['mode'] in (0, 1):
            raise ValueError('explicit simulator base and mode 0 or 1 required')
        # Validate every required setting before issuing the first write.
        for key in ('action_j', 'noise_nm', 'qs'):
            if not math.isfinite(float(synth[key])):
                raise ValueError(f'invalid synthetic setting: {key}')
    return host


def configure(config):
    host = validate(config)
    os.environ['TANGO_HOST'] = host
    import tango
    os.environ['LD_LIBRARY_PATH'] = ':'.join(config['library_path'])
    config = dict(config, command_timeout=20)
    ops = runpy.run_path(str(Path(__file__).with_name('rtchain-ops')))
    bench = config['bench']
    deadline = time.monotonic() + float(bench['timeout'])
    keys = list(bench['keys'])
    if 'synthetic-beam' in bench['operations']:
        keys.append(config['synthetic-beam']['sim_base'] + '__Qx')
    devices = {}
    while True:
        try:
            for entry in bench['devices']:
                name = entry['name']
                device = tango.DeviceProxy(name)
                device.set_timeout_millis(max(1, min(2000, int((deadline - time.monotonic()) * 1000))))
                device.ping()
                for attribute in entry['attributes']:
                    device.read_attribute(attribute)
                if name in config['smart-rescan']['devices']:
                    device.command_query('SmartRescan')
                devices[name] = device
            for key in keys:
                config['command_timeout'] = max(0.001, min(2, deadline - time.monotonic()))
                ops['knob'](config, key)
            if time.monotonic() >= deadline:
                raise TimeoutError('prerequisite deadline exceeded')
            break
        except Exception as error:
            if time.monotonic() >= deadline:
                raise RuntimeError(f'bench prerequisites unavailable: {error}') from error
            time.sleep(min(float(bench['poll_interval']), max(0, deadline - time.monotonic())))
    config['command_timeout'] = 20
    print('bench: prerequisites available', flush=True)
    for operation in bench['operations']:
        if operation == 'synthetic-beam':
            ops['synthetic_beam'](config)
        else:
            for name in config['smart-rescan']['devices']:
                device = devices[name]
                device.set_timeout_millis(20000)
                device.command_inout('SmartRescan')
        print(f'bench: {operation}: completed', flush=True)
    print('bench: ready', flush=True)
