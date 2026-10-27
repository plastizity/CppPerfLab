import unittest

from cppperflab.adapters import REGISTRY
from cppperflab.metrics import catalog
from cppperflab.perf_events import CACHE_ALIASES, descriptor_definition


class HierarchyMetricsTest(unittest.TestCase):
    def test_all_hardware_cache_aliases_registered(self):
        expected = {'cpu.l1d_loads', 'cpu.l1d_load_misses', 'cpu.llc_loads',
                    'cpu.llc_load_misses', 'cpu.dtlb_loads', 'cpu.dtlb_load_misses'}
        self.assertEqual({v['metric_id'] for v in CACHE_ALIASES.values()}, expected)
        for selector, descriptor in CACHE_ALIASES.items():
            with self.subTest(selector=selector):
                item = catalog(REGISTRY, metric_id=descriptor['metric_id'])['metrics'][0]
                self.assertEqual(item['unit'], 'count')
                self.assertEqual(item['categories'], ['Diagnostic'])
                self.assertIn('D05', item['domains'])
                self.assertEqual(item['source_capability'], [descriptor['metric_id']])
                self.assertEqual(item['event_descriptor'], descriptor_definition(selector))
                self.assertEqual(item['event_descriptor']['linux_abi']['type'], 3)
                self.assertIn('perf-stat', item['providers'])
                self.assertIn('CPU', item['reliability'])
                if descriptor['metric_id'].startswith('cpu.dtlb_'):
                    self.assertIn('D10', item['domains'])

    def test_generic_and_cachegrind_not_relabelled(self):
        for name in ('cpu.cache_misses', 'cpu.cache_references'):
            item = catalog(REGISTRY, metric_id=name)['metrics'][0]
            self.assertNotIn('event_descriptor', item)
        cachegrind = [m for m in catalog(REGISTRY, domain='D05')['metrics'] if m['metric_id'].startswith('cachegrind.')]
        self.assertTrue(cachegrind)
        self.assertTrue(all(('simulated' in m['measurement_scope'] or 'Cachegrind model' in m['measurement_scope']) and 'event_descriptor' not in m for m in cachegrind))


if __name__ == '__main__':
    unittest.main()
