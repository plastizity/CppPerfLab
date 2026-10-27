import json
import unittest
from copy import deepcopy

from cppperflab.adapters import REGISTRY
from cppperflab.metrics import catalog, derive_sample, derive_grouped_sample, metric_samples, run_observations


def counter(name, event, value):
    return dict(name=name, value=value, unit='count', evidence_state='Measured', evidence_version=1,
                method='hardware-counter', scope='target process and inherited threads',
                tool='perf-stat', tool_version='perf 1', event=event, raw_event=event,
                evidence='measurement-000/perf.csv', collection_evidence='measurement-000/perf-collection.json',
                capture_scope='target_process_and_inherited_threads', capture_window='whole-target-invocation',
                counter_scaling='none', event_group='group-0', group_events=['cache-misses', 'cache-references', 'instructions'],
                event_group_status='PASS', counter_runtime_ns=100, running_percent=100)


def work(count=10):
    contract = dict(definition_id='mesh.cells', unit='cell', scope='target-invocation', count_kind='observed', completed=True)
    return dict(name='work.items', value=count, unit='cell', evidence_state='Measured', evidence_version=1,
                method='target-reported-invocation-work', scope='target invocation; completed business work',
                capture_scope='target_process_and_inherited_threads', capture_window='whole-target-invocation',
                work_definition_id='mesh.cells', work_unit='cell', counter_contract=json.dumps(contract),
                count_kind='observed', completed=True, evidence='measurement-000/work.json')


def sample():
    return {'index': 0, 'metrics': [counter('cpu.cache_misses', 'cache-misses', 20),
        counter('cpu.cache_references', 'cache-references', 100),
        counter('cpu.instructions', 'instructions', 1000), work()]}


def results(s):
    return {m['name']: m for m in derive_grouped_sample(s)}


class GroupedMetricsTest(unittest.TestCase):
    def test_values_and_projection(self):
        s = sample()
        r = results(s)
        for name, value in [('cpu.cache_miss_ratio', .2), ('cpu.cache_mpki', 20),
                            ('cpu.instructions_per_work', 100), ('cpu.cache_misses_per_work', 2)]:
            self.assertEqual(r[name]['evidence_state'], 'Estimated')
            self.assertEqual(r[name]['value'], value)
            self.assertEqual(len(r[name]['sources']), 2)
        self.assertEqual(r['cpu.instructions_per_work']['unit'], 'count/cell')
        self.assertEqual(r['cpu.instructions_per_work']['work_definition_id'], 'mesh.cells')
        self.assertEqual(len(derive_sample(s)), 4)
        run = dict(run_id='run-1', samples=[s])
        self.assertEqual(metric_samples(run, 'cpu.cache_mpki')[0]['value'], 20)
        self.assertIn('cpu.cache_mpki', {m['name'] for m in run_observations(run)})
        self.assertIn('grouped', catalog(REGISTRY, metric_id='cpu.cache_mpki')['metrics'][0]['collection_method'])
        self.assertIn('target work report', catalog(REGISTRY, metric_id='cpu.instructions_per_work')['metrics'][0]['collection_method'])

    def test_zero_and_large_ratio(self):
        s = sample()
        s['metrics'][0]['value'] = 0
        self.assertEqual(results(s)['cpu.cache_mpki']['value'], 0)
        s['metrics'][0]['value'] = 200
        r = results(s)['cpu.cache_miss_ratio']
        self.assertEqual(r['value'], 2)
        self.assertIn('not a probability', ' '.join(r['limitations']))
        s['metrics'][1]['value'] = 0
        self.assertEqual(results(s)['cpu.cache_miss_ratio']['evidence_state'], 'Missing')
        s['metrics'][3]['value'] = 0
        self.assertEqual(results(s)['cpu.instructions_per_work']['evidence_state'], 'Missing')

    def test_group_provenance(self):
        for key, value in [('scope', 'elsewhere'), ('tool', 'other'), ('tool_version', '2'),
                           ('evidence', 'other.csv'), ('collection_evidence', 'other.json'),
                           ('capture_scope', 'other'), ('capture_window', 'other'),
                           ('counter_scaling', 'scaled'), ('event_group', 'group-1'),
                           ('counter_runtime_ns', 99), ('running_percent', 98.9),
                           ('event_group_status', 'INCONCLUSIVE'), ('evidence_state', 'Modeled')]:
            s = sample(); s['metrics'][1][key] = value
            with self.subTest(key=key):
                self.assertEqual(results(s)['cpu.cache_miss_ratio']['evidence_state'], 'Invalid')
        s = sample(); del s['metrics'][0]['event_group']
        self.assertEqual(results(s)['cpu.cache_mpki']['evidence_state'], 'Missing')
        s = sample(); s['metrics'][0]['event_group_status'] = 'UNSUPPORTED'
        self.assertEqual(results(s)['cpu.cache_mpki']['evidence_state'], 'Unsupported')
        s = sample(); s['metrics'].pop(1)
        self.assertEqual(results(s)['cpu.cache_miss_ratio']['evidence_state'], 'Missing')
        s = sample(); s['metrics'][1]['event'] = 'cache-references:u'; s['metrics'][1]['raw_event'] = 'cache-references:u'
        self.assertEqual(results(s)['cpu.cache_miss_ratio']['evidence_state'], 'Invalid')

    def test_matching_event_modifiers_and_same_measurement_folder(self):
        s = sample()
        for item in s['metrics'][:3]:
            item['event'] += ':u'
            item['raw_event'] += ':u'
        self.assertEqual(results(s)['cpu.cache_mpki']['value'], 20)
        self.assertEqual(results(s)['cpu.instructions_per_work']['value'], 100)
        for index, key, value in [(3, 'evidence', 'measurement-001/work.json'),
                                  (2, 'collection_evidence', 'measurement-001/perf-collection.json')]:
            s = sample(); s['metrics'][index][key] = value
            with self.subTest(key=key):
                self.assertEqual(results(s)['cpu.instructions_per_work']['evidence_state'], 'Invalid')

    def test_work_requires_full_coverage_and_valid_contract(self):
        s = sample(); s['metrics'][2]['running_percent'] = 99.9
        self.assertEqual(results(s)['cpu.instructions_per_work']['evidence_state'], 'Invalid')
        for key, value in [('completed', False), ('count_kind', 'estimated'), ('unit', 'other'),
                           ('work_definition_id', 'different'), ('capture_window', 'other'),
                           ('evidence_state', 'Modeled'), ('value', 1.5)]:
            s = sample(); s['metrics'][3][key] = value
            with self.subTest(key=key):
                self.assertEqual(results(s)['cpu.instructions_per_work']['evidence_state'], 'Invalid')
        s = sample(); s['metrics'].pop()
        self.assertEqual(results(s)['cpu.instructions_per_work']['evidence_state'], 'Missing')


if __name__ == '__main__':
    unittest.main()
