"""Cross-contract tests use controlled fixtures, not performance measurements."""
import copy
from pathlib import Path
import tempfile
import unittest

from cppperflab.cli import parser
from cppperflab.comparison import compare, request_differences
from cppperflab.errors import LabError
from cppperflab.validity import evaluate_comparison_validity
from test_core import comparison_result
from test_dme_metrics import sample, work
from test_validity import claim, condition
from cppperflab.storage import write_json
from cppperflab.studies import load_study
import test_assessment as assessment_fixture


class DmeIntegrationTests(unittest.TestCase):
    def test_joint_diagnostic_role_is_informational_and_keeps_resource_meaning(self):
        with tempfile.TemporaryDirectory() as temp:
            path,store,spec=assessment_fixture.AssessmentTests().prepare(Path(temp))
            diagnostic=spec['profiles'][1]['comparisons'][0]
            diagnostic.update(role='diagnostic',metric='cpu.cache_misses',gate='informational')
            spec['profiles'][1].update(tool='perf-stat',options={'events':['cache-references','cache-misses'],
                'event_groups':[['cache-references','cache-misses']]})
            write_json(path,spec);load_study(path,store)
            diagnostic['gate']='non_regression';write_json(path,spec)
            with self.assertRaises(LabError):load_study(path,store)

    def test_estimated_diagnostic_cannot_pass_measured_acceptance(self):
        a,b=comparison_result('a'),comparison_result('b')
        for run in (a,b):run['samples']=[sample()]
        with self.assertRaises(LabError) as caught:compare(a,b,'cpu.cache_mpki')
        self.assertEqual(caught.exception.details['acceptance']['status'],'INCONCLUSIVE')
        self.assertEqual(caught.exception.details['evidence_states'],['Estimated','Estimated'])

    def test_work_definition_and_request_are_comparison_conditions(self):
        a,b=comparison_result('a'),comparison_result('b')
        for run in (a,b):
            for s in run['samples']:s['metrics'].append(work())
        compare(a,b,'work.items')
        for s in b['samples']:s['metrics'][-1]['work_definition_id']='different-task'
        with self.assertRaises(LabError):compare(a,b,'work.items')
        left=a['request'];right=copy.deepcopy(left)
        right['measurement']['work_report']={'schema_version':1,'path':'items.work.json','definition_id':'x'}
        self.assertIn('measurement.work_report',[v['condition'] for v in request_differences(left,right)])

    def test_custom_work_unit_requires_matching_capture_and_evidence_window(self):
        a,b=comparison_result('a'),comparison_result('b')
        for run in (a,b):
            run['request']['claim']=claim('unit_efficiency',[condition('work_metric',metric='work.items')])
            for index,s in enumerate(run['samples']):
                s['metrics'][0].update(capture_scope='target_process_and_inherited_threads',
                    capture_window='whole-target-invocation',evidence=f'measurement-{index:03d}/perf.csv')
                m=work();m['evidence']=f'measurement-{index:03d}/items.work.json';s['metrics'].append(m)
        self.assertEqual(evaluate_comparison_validity(a,b,'time.wall')['status'],'PASS')
        b['samples'][0]['metrics'][-1]['evidence']='another-measurement/items.work.json'
        self.assertEqual(evaluate_comparison_validity(a,b,'time.wall')['status'],'INCONCLUSIVE')

    def test_probe_accepts_explicit_group_without_changing_events(self):
        args=parser().parse_args(['tools','probe','--tool','perf-stat','--events',
            'instructions,cache-references,cache-misses','--event-group',
            'instructions,cache-references,cache-misses'])
        self.assertEqual(args.event_group,['instructions,cache-references,cache-misses'])


if __name__=='__main__':unittest.main()
