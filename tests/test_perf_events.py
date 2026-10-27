"""Linux cache ABI descriptor fixtures do not execute or claim hardware measurements."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from cppperflab import perf_events


class PerfEventsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.cpuinfo = self.root / 'cpuinfo'
        self.cpuinfo.write_text('processor : 0\nvendor_id : FixtureVendor\ncpu family : 6\nmodel : 12\nstepping : 3\nmodel name : Example CPU\nmicrocode : 0x1\n')
        self.pmus = self.root / 'devices'; cpu = self.pmus / 'cpu'; cpu.mkdir(parents=True)
        (cpu / 'type').write_text('4\n'); (cpu / 'caps').mkdir(); (cpu / 'format').mkdir()
        (cpu / 'caps' / 'branches').write_text('32\n'); (cpu / 'format' / 'event').write_text('config:0-7\n')
        (cpu / 'cpus').write_text('0-3\n')
        self.cpu_patch = mock.patch.object(perf_events, 'CPUINFO_PATH', self.cpuinfo); self.cpu_patch.start()
        self.pmu_patch = mock.patch.object(perf_events, 'PMU_ROOT', self.pmus); self.pmu_patch.start()
        self.addCleanup(self.pmu_patch.stop); self.addCleanup(self.cpu_patch.stop); self.addCleanup(self.temp.cleanup)
        self.counter = 0

    def attach(self, raw='LLC-load-misses:u', value=7, version='perf 1'):
        self.counter += 1; folder = self.root / str(self.counter); folder.mkdir()
        parsed = {'metrics':[{'event':raw,'raw_event':raw,'value':value}], 'unavailable_observations':[]}
        doc = perf_events.attach_descriptors(folder, parsed, version, ['LLC-load-misses'])
        return doc, parsed, folder

    def test_static_definitions_are_independent_and_match_linux_abi(self):
        expected = {'L1-dcache-loads':0, 'L1-dcache-load-misses':65536, 'LLC-loads':2,
                    'LLC-load-misses':65538, 'dTLB-loads':3, 'dTLB-load-misses':65539}
        self.assertEqual(set(perf_events.CACHE_ALIASES), set(expected))
        for selector, value in expected.items():
            definition = perf_events.descriptor_definition(selector)
            self.assertEqual((definition['linux_abi']['type'], definition['linux_abi']['config']), (3, value))
            definition['linux_abi']['type'] = 999
            self.assertEqual(perf_events.descriptor_definition(selector)['linux_abi']['type'], 3)

    def test_key_stable_across_paths_and_counts_and_context_is_explicit(self):
        doc, parsed, folder = self.attach(value=1)
        other, _, other_folder = self.attach(value=999)
        event = doc['events'][0]
        self.assertEqual(event['event_semantics_key'], other['events'][0]['event_semantics_key'])
        self.assertEqual(parsed['metrics'][0]['event_descriptor_evidence'], 'event-descriptors.json')
        self.assertEqual(event['observed_selector'], 'LLC-load-misses:u')
        self.assertEqual(event['filters']['value'], 'u')
        self.assertEqual(event['physical_mapping']['evidence_state'], 'Missing')
        self.assertEqual(event['linux_abi']['type'], 3)
        self.assertEqual(doc['context']['pmu_inventory']['candidates'][0]['type']['value'], '4')
        self.assertTrue((folder/'cpuinfo-context.txt').is_file())
        self.assertTrue((folder/'pmu-inventory.json').is_file())
        self.assertEqual(json.loads((other_folder/'event-descriptors.json').read_text()),other)
        self.assertEqual(doc['context']['pmu_inventory']['candidates'][0]['cpumask']['evidence_state'],'Missing')

    def test_cpu_filter_pmu_and_perf_version_change_key(self):
        base,_,_=self.attach();key=base['events'][0]['event_semantics_key']
        changed,_,_=self.attach(raw='LLC-load-misses:k')
        self.assertNotEqual(key,changed['events'][0]['event_semantics_key'])
        changed,_,_=self.attach(version='perf 2')
        self.assertNotEqual(key,changed['events'][0]['event_semantics_key'])
        self.cpuinfo.write_text(self.cpuinfo.read_text().replace('model : 12','model : 13'))
        changed,_,_=self.attach();self.assertNotEqual(key,changed['events'][0]['event_semantics_key'])
        self.cpuinfo.write_text(self.cpuinfo.read_text().replace('model : 13','model : 12'))
        (self.pmus/'cpu'/'format'/'event').write_text('config:0-8')
        changed,_,_=self.attach();self.assertNotEqual(key,changed['events'][0]['event_semantics_key'])

    def test_unavailable_and_probe_failure_are_not_reported_as_observed(self):
        folder=self.root/'missing';folder.mkdir()
        parsed={'metrics':[],'unavailable_observations':[{'event':'dTLB-load-misses','raw_event':'dTLB-load-misses:u'}]}
        doc=perf_events.attach_descriptors(folder,parsed,None,['dTLB-load-misses','L1-dcache-loads'])
        rows={row['requested_selector']:row for row in doc['events']}
        self.assertEqual(doc['context']['perf_version_state'],'Missing')
        self.assertIsNone(rows['L1-dcache-loads']['observed_selector'])
        self.assertNotIn('perf.csv',rows['L1-dcache-loads']['evidence'])
        self.assertEqual(rows['L1-dcache-loads']['filters']['evidence_state'],'Missing')
        self.assertEqual(rows['dTLB-load-misses']['observed_selector'],'dTLB-load-misses:u')
        self.assertIn('event_semantics_key',parsed['unavailable_observations'][0])
        parsed={'metrics':[],'unavailable_observations':[]}
        self.assertIsNone(perf_events.attach_descriptors(folder,parsed,None,['cycles']))
