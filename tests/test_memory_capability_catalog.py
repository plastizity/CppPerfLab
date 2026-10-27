import unittest
from unittest.mock import patch

from cppperflab.adapters import REGISTRY
from cppperflab.metrics import catalog


class MemoryCapabilityCatalogTest(unittest.TestCase):
    def test_gap_is_platform_only_and_static(self):
        with patch('pathlib.Path.read_text', side_effect=AssertionError('catalog must not inspect the host')):
            view = catalog(REGISTRY)
        gap = view['capability_gaps'][0]
        self.assertEqual(gap['capability_id'], 'memory_controller_traffic')
        self.assertEqual(gap['collection_status'], 'Unsupported')
        self.assertEqual(gap['reason'], 'collector_not_implemented')
        self.assertIn('not process', gap['scope'])
        self.assertIn('historical run', gap['interpretation'])
        self.assertIn('memory-controller-traffic', gap['discovery_command'])
        self.assertFalse(any(m['metric_id'].startswith('memory.dram') for m in view['metrics']))

    def test_gap_filter_does_not_attach_to_an_individual_metric(self):
        for domain in ('D05', 'D10', 'D12'):
            self.assertEqual(catalog(REGISTRY, domain=domain)['capability_gaps'][0]['capability_id'], 'memory_controller_traffic')
        self.assertNotIn('capability_gaps', catalog(REGISTRY, domain='D01'))
        self.assertNotIn('capability_gaps', catalog(REGISTRY, metric_id='cpu.cache_misses'))


if __name__ == '__main__':
    unittest.main()
