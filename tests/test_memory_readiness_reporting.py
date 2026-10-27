"""Readiness reporting fixtures; no hardware or performance measurements."""
import json
import tempfile
import unittest
from pathlib import Path

from cppperflab.agent_report import build_agent_report
from cppperflab.html_report import data_movement_section
from cppperflab.lab import summarize
from cppperflab.protocol import metric
from cppperflab.views import base_view, run_view
from test_views import BASELINE, run_result


class MemoryReadinessReportingTests(unittest.TestCase):
    def test_gap_is_not_a_measurement_and_reports_share_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            run=run_result(BASELINE,root/'run',[1.0]*7)
            run['samples'][0]['metrics'].append(metric('cpu.cache_misses',23,'count',
                'hardware-counter','target process','perf.csv',evidence_state='Measured'))
            run['summary']=summarize(run['samples'])
            view=run_view(run)
            section=view['data_movement']
            gap=section['capability_gaps'][0]
            self.assertEqual(gap['measurement_evidence_state'],'Missing')
            self.assertEqual((gap['collector_status'],gap['collector_reason']),
                             ('Unsupported','collector_not_implemented'))
            self.assertNotIn('value',gap)
            self.assertNotIn('probe_id',gap)
            raw=next(r for r in section['columns']['data_movement']['observations']
                     if r['name']=='cpu.cache_misses')
            self.assertEqual((raw['value'],raw['evidence_state']),(23,'Measured'))
            agent=build_agent_report(view,root)
            self.assertEqual(agent['data_movement']['capability_gaps'],section['capability_gaps'])
            html=data_movement_section(view)
            self.assertIn(gap['discovery_command'],html)
            self.assertIn('collector_not_implemented',html)
            self.assertIn('不代表当前或历史硬件状态',html)

    def test_no_source_and_legacy_schemas_remain_missing_not_pass(self):
        view=base_view('summary',BASELINE,'completed',None)
        self.assertEqual(view['data_movement']['capability_gaps'][0]['measurement_evidence_state'],'Missing')
        for name in ('analysis','agent-report'):
            schema=json.loads((Path(__file__).parents[1]/'src/cppperflab/schemas'/(name+'.schema.json')).read_text())
            spec=schema['properties']['data_movement']
            self.assertIn('capability_gaps',spec['properties'])
            self.assertNotIn('capability_gaps',spec['required'])

if __name__ == '__main__':
    unittest.main()
