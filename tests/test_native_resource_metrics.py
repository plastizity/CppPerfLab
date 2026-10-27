import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from cppperflab.adapters import REGISTRY
from cppperflab.errors import LabError
from cppperflab.metrics import catalog
from cppperflab.native_resource import RESOURCE_METRICS, parse_resource


def report():
    return dict(schema_version=1, collector='linux-wait4', version=1, child_pid=123,
                wait_status=0, exited=True, exit_code=0, signal=None, exec_errno=None,
                rusage=dict(max_rss_kib=101, user_time={'seconds': 2, 'microseconds': 250000},
                            system_time={'seconds': 0, 'microseconds': 1}, minor_faults=13, major_faults=0,
                            voluntary_context_switches=1, involuntary_context_switches=2,
                            filesystem_inputs=0, filesystem_outputs=0))


class NativeResourceTests(unittest.TestCase):
    def parse(self, item):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'resource.json'
            path.write_text(json.dumps(item))
            return parse_resource(path)

    def test_synthetic_values_and_catalog(self):
        result = self.parse(report())
        values = {m['name']: m for m in result['metrics']}
        self.assertEqual(values['memory.rss_peak']['value'], 101 * 1024)
        self.assertEqual(values['time.cpu.user']['value'], 2.25)
        self.assertEqual(values['time.cpu.system']['value'], .000001)
        self.assertEqual(values['os.minor_page_faults']['value'], 13)
        self.assertEqual(values['os.major_page_faults']['value'], 0)
        self.assertTrue(all(m['evidence_state'] == 'Measured' for m in values.values()))
        self.assertNotIn('time.wall', values)
        self.assertIn('not simultaneous', values['memory.rss_peak']['scope'])
        self.assertIn('do not guarantee', ' '.join(result['limitations']))
        for name, spec in RESOURCE_METRICS.items():
            item = catalog(REGISTRY, metric_id=name)['metrics'][0]
            self.assertEqual(item['unit'], spec['unit'])
            self.assertEqual(item['mathematical_definition'], spec['formula'])

    def test_failed_child_retains_raw_metrics_but_reports_status(self):
        item = report(); item.update(wait_status=127 << 8, exit_code=127, exec_errno=2)
        result = self.parse(item)
        self.assertEqual(result['child_status']['exec_errno'], 2)
        self.assertTrue(result['metrics'])
        item = report(); item.update(wait_status=9, exited=False, exit_code=None, signal=9)
        self.assertEqual(self.parse(item)['child_status']['signal'], 9)

    def test_rejects_invalid_external_report(self):
        cases = []
        x = report(); x['rusage']['max_rss_kib'] = -1; cases.append(x)
        x = report(); x['rusage']['minor_faults'] = True; cases.append(x)
        x = report(); x['rusage']['user_time']['microseconds'] = 1000000; cases.append(x)
        x = report(); x['exit_code'] = 1; cases.append(x)
        x = report(); x['exec_errno'] = 2; cases.append(x)
        x = report(); x['wait_status'] = 0x7f; cases.append(x)
        x = report(); x['unexpected'] = 1; cases.append(x)
        x = report(); x['schema_version'] = 2; cases.append(x)
        for item in cases:
            with self.subTest(item=item):
                with self.assertRaises(LabError) as context:
                    self.parse(item)
                self.assertEqual(context.exception.category, 'parse_failed')


if __name__ == '__main__':
    unittest.main()
