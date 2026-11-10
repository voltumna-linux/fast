"""Exercise the provisioning CLI and database boundary without a boot claim."""
import copy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

import test_preparation

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rtchain_tango import CGROUP, configure_db


class Database:
    def __init__(self, host):
        self.host = host
        self.data = {}

    def get_server_info(self, server):
        return SimpleNamespace(host=self.host)

    def get_device_name(self, server, klass):
        return ['dserver/' + server]

    def put_device_property(self, device, properties):
        self.data[device] = properties

    def get_device_property(self, device, names):
        return self.data[device]


class TangoTest(unittest.TestCase):
    def setUp(self):
        self.fixture = test_preparation.PreparationTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.config = self.fixture.config
        self.config['tango_servers'] = {
            'tango_host': 'db.example:20001', 'starter_cpus': '4',
            'shared_memory_nodes': '1', 'profiles': [
                {'server': 'Example/a', 'group': 'a', 'cpus': '4', 'numa_node': 0,
                 'scheduler': 'other', 'priority': 0},
                {'server': 'Example/b', 'group': 'b', 'cpus': '5', 'numa_node': 0,
                 'scheduler': 'fifo', 'priority': 20}]}

    def test_cli_plan_is_per_instance_and_does_not_require_pytango(self):
        result = self.fixture.invoke('tango-profile', 'configure-db')
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(result.stdout)
        self.assertEqual(set(plan), {'dserver/Example/a', 'dserver/Example/b'})
        self.assertIn(f'cgroup={CGROUP}/b', plan['dserver/Example/b']['NativeLaunchProfile'])
        self.assertIn('numa_node=0', plan['dserver/Example/a']['NativeLaunchProfile'])

    def test_wrong_host_stops_all_database_writes(self):
        database = Database('another-host')
        with self.assertRaisesRegex(ValueError, 'registration belongs'):
            configure_db(self.config, True, database)
        self.assertFalse(database.data)

    def test_repeatable_write_and_readback_preserves_distinct_instances(self):
        database = Database(self.config['host'])
        first = configure_db(self.config, True, database)
        self.assertEqual(first, configure_db(self.config, True, database))
        self.assertNotEqual(database.data['dserver/Example/a'], database.data['dserver/Example/b'])

    def test_missing_admin_device_prevents_all_writes(self):
        database = Database(self.config['host'])
        database.get_device_name = lambda server, klass: [] if server.endswith('/b') else ['dserver/' + server]
        with self.assertRaisesRegex(ValueError, 'administrative device missing'):
            configure_db(self.config, True, database)
        self.assertFalse(database.data)

    def test_invalid_inventory_fails_before_database_access(self):
        for changes in ({'cpus': '6'}, {'numa_node': 2}, {'priority': 100},
                        {'scheduler': 'rr', 'priority': 0}, {'group': '../escape'},
                        {'group': 'supervisor'}, {'server': 'Example/b'}):
            with self.subTest(changes=changes):
                config = copy.deepcopy(self.config)
                config['tango_servers']['profiles'][0].update(changes)
                with self.assertRaises(ValueError):
                    configure_db(config)

    def test_shared_memory_boundary_cannot_exclude_registry(self):
        self.config['tango_servers']['shared_memory_nodes'] = '0'
        result = self.fixture.invoke('tango-profile', 'configure-db')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('include the registry', result.stderr)

    def test_launch_outside_delegation_never_executes_command(self):
        marker = self.fixture.root / 'executed'
        command = self.fixture.root / 'command'
        command.write_text('#!/bin/sh\ntouch ' + str(marker) + '\n')
        command.chmod(0o755)
        result = self.fixture.invoke('tango-profile', 'launch', '--', command)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('delegated starter.service root', result.stderr)
        self.assertFalse(marker.exists())
