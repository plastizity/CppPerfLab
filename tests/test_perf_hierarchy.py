"""Hierarchy aliases preserve real perf evidence and explicit availability."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from cppperflab.adapters import EVENTS, REGISTRY, parse_stat
from cppperflab.errors import LabError
from cppperflab.perf_events import CACHE_ALIASES


class PerfHierarchyTests(unittest.TestCase):
    def setUp(self):
        self.adapter = REGISTRY['perf-stat']
        self.selectors = ['L1-dcache-loads', 'L1-dcache-load-misses']
        self.options = {'events': self.selectors, 'event_groups': [self.selectors]}
        self.record = {'status': 'completed', 'returncode': 0, 'tool_version': 'perf version fixture'}
        self.context = {
            'machine':'x86_64', 'kernel':'fixture', 'perf_version':'fixture',
            'cpuinfo_source':{'evidence_state':'Missing'}, 'cpu_identity':{},
            'pmu_inventory':{'evidence_state':'Missing'},
            'cpuinfo_evidence':'cpuinfo-context.txt', 'pmu_inventory_evidence':'pmu-inventory.json',
            'capture_note':'test fixture'}

    def test_all_six_aliases_are_distinct_and_explicitly_described(self):
        self.assertEqual(len(CACHE_ALIASES), 6)
        ids = [EVENTS[event][0] for event in CACHE_ALIASES]
        self.assertEqual(len(set(ids)), 6)
        for event in CACHE_ALIASES:
            self.assertEqual(EVENTS[event][1:], ('count', 'hardware-counter'))
            self.adapter.validate({'events':[event]})
        description = self.adapter.describe()
        self.assertEqual(set(description['hierarchy_event_aliases']), set(CACHE_ALIASES))
        self.assertIn('probe', description['hierarchy_availability'])

    def test_group_keeps_raw_modifier_and_matched_descriptor(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory/'perf.csv').write_text('12;;L1-dcache-loads:u;200;100\n3;;L1-dcache-load-misses:u;200;100\n')
            with mock.patch('cppperflab.perf_events._context', return_value=self.context):
                result = self.adapter.parse(directory, self.options, self.record)
            first, second = result['metrics']
            self.assertEqual(first['name'], 'cpu.l1d_loads')
            self.assertEqual(second['name'], 'cpu.l1d_load_misses')
            self.assertEqual(first['event'], 'L1-dcache-loads:u')
            self.assertEqual(first['event_group_status'], 'PASS')
            self.assertEqual(first['event_descriptor_evidence'], 'event-descriptors.json')
            self.assertIn('event_semantics_key', first)
            doc = json.loads((directory/'event-descriptors.json').read_text())
            self.assertEqual(doc, result['event_descriptors'])
            self.assertEqual(doc['events'][0]['observed_selector'], 'L1-dcache-loads:u')
            self.assertEqual(doc['events'][0]['filters']['value'], 'u')
            self.assertEqual(doc['events'][0]['physical_mapping']['evidence_state'], 'Missing')

    def test_unavailable_retains_raw_modifier_and_archives_descriptor(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory/'perf.csv').write_text('12;;L1-dcache-loads:u;200;100\n<not supported>;;L1-dcache-load-misses:u;0;0\n')
            with mock.patch('cppperflab.perf_events._context', return_value=self.context):
                with self.assertRaises(LabError) as caught:
                    self.adapter.parse(directory, self.options, self.record)
            self.assertEqual(caught.exception.category, 'unavailable')
            unavailable = caught.exception.details['unavailable_observations'][0]
            self.assertEqual(unavailable['raw_event'], 'L1-dcache-load-misses:u')
            self.assertEqual(unavailable['evidence_state'], 'Unsupported')
            self.assertEqual(caught.exception.details['metrics'][0]['event_group_status'], 'UNSUPPORTED')
            self.assertTrue((directory/'event-descriptors.json').exists())
            self.assertEqual(len(caught.exception.details['event_descriptors']['events']), 2)

    def test_missing_raw_and_missing_executable_preserve_requests(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch('cppperflab.perf_events._context', return_value=self.context):
            directory = Path(tmp)
            with self.assertRaises(LabError) as caught:
                self.adapter.parse(directory, self.options, self.record)
            self.assertEqual(caught.exception.category, 'parse_failed')
            self.assertTrue((directory/'event-descriptors.json').exists())
            events = caught.exception.details['event_descriptors']['events']
            self.assertEqual({r['requested_selector'] for r in events}, set(self.selectors))
            self.assertTrue(all(r['observed_selector'] is None for r in events))
            with mock.patch.object(type(self.adapter), 'tool_path', return_value=None):
                result = self.adapter.probe(directory/'probe', self.options)
            self.assertFalse(result['available'])
            self.assertEqual(set(result['event_descriptors_requested']), set(self.selectors))
            self.assertEqual(result['event_descriptors_collected'], [])
            self.assertTrue(Path(result['event_descriptors_evidence']).is_absolute())
            self.assertTrue(Path(result['event_descriptors_evidence']).exists())

    def test_nonfinite_and_incompatible_units_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'perf.csv'
            for text in ('nan;;dTLB-loads;100;100\n', '1;bytes;dTLB-loads;100;100\n'):
                path.write_text(text)
                with self.assertRaises(LabError):
                    parse_stat(path, ['dTLB-loads'])

    def test_probe_distinguishes_explicit_enodev_from_malformed_output(self):
        messages = [
            ('Error:\nNo supported events found.\n'
             'The sys_perf_event_open() syscall failed for event (LLC-loads:u): No such device or address\n',
             'unavailable', 'Unsupported'),
            ('malformed unexpected data\n', 'parse_failed', None),
        ]
        for stderr, expected_reason, expected_state in messages:
            def fake_execute(args, folder, **kwargs):
                folder = Path(folder)
                folder.mkdir(parents=True, exist_ok=True)
                (folder/'stdout.txt').write_text('perf version fixture' if folder.name == 'version' else '')
                (folder/'stderr.txt').write_text(stderr if folder.name == 'execution' else '')
                if folder.name == 'execution':
                    (folder/'perf.csv').write_text('# no counter output\n')
                return {'status':'completed', 'returncode':0 if folder.name == 'version' else 1}
            with self.subTest(stderr=stderr), tempfile.TemporaryDirectory() as tmp, \
                 mock.patch.object(type(self.adapter), 'tool_path', return_value='/usr/bin/perf'), \
                 mock.patch('cppperflab.adapters.execute', side_effect=fake_execute), \
                 mock.patch('cppperflab.perf_events._context', return_value=self.context):
                result = self.adapter.probe(Path(tmp)/'probe', {'events':['LLC-loads']})
                self.assertFalse(result['available'])
                self.assertEqual(result['reason'], expected_reason)
                self.assertEqual(result.get('evidence_state'), expected_state)
                self.assertEqual(result['stderr'], stderr)
                self.assertIn('Missing perf events', result['diagnostic'])
                self.assertEqual(result['event_descriptors_requested'], ['LLC-loads'])
                self.assertEqual(result['event_descriptors_collected'], [])
                self.assertIsNone(result['event_descriptors']['events'][0]['observed_selector'])
                self.assertTrue(Path(result['event_descriptors_evidence']).is_file())


if __name__ == '__main__':
    unittest.main()
