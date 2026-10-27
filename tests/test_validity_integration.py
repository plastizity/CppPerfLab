"""Contract integration regressions; controlled fixtures are not real measurements."""
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from cppperflab.assessment import assess_rows
from cppperflab.comparison import compare
from cppperflab.lab import plan
from cppperflab.protocol import metric
from cppperflab.storage import Store,write_json
from cppperflab.studies import load_study
from test_core import comparison_result,request_template
from test_validity import claim
from test_assessment import assessment_spec,POLICY
import test_assessment as assessment_fixture


class ValidityIntegrationTests(unittest.TestCase):
    def test_unsupported_claim_is_rejected_before_integrated_target_probe(self):
        request=request_template(Path('/tmp'))
        request['measurement'].update(tool='google-benchmark',warmups=0,options={'filter':'^Fixture$'})
        request['claim']=claim();request['claim']['metric']='cpu.instructions'
        with tempfile.TemporaryDirectory() as temp,mock.patch('cppperflab.lab.probe_tools') as probe:
            result=plan(request,Store(temp))
        probe.assert_not_called()
        self.assertIsNone(result['selected'])
        self.assertEqual(result['candidates'][0]['reasons'],['claim_metrics_unavailable'])

    def test_auxiliary_work_and_memory_do_not_block_primary_claim_assessment(self):
        a,b=comparison_result('baseline'),comparison_result('candidate')
        for run in (a,b):
            run['request']['claim']=claim()
            for index,sample in enumerate(run['samples']):
                sample['index']=index
                for name,value,unit in (('algorithm.operations',100,'count'),('memory.heap_peak_observed',500,'bytes')):
                    sample['metrics'].append(metric(name,value,unit,'explicit-test-counter','fixture scope','raw.txt'))
        rows=[]
        for i,(name,role,gate,profile) in enumerate((('time.wall','end_to_end','non_regression','native'),
                ('algorithm.operations','work','informational','native'),
                ('memory.heap_peak_observed','resource','non_regression','heap'))):
            row=compare(a,b,name,policy=POLICY)
            row.update(case_id='a',profile_id=profile,assessment_role=role,assessment_gate=gate,comparison_id=str(i))
            rows.append(row)
        for row in rows:
            self.assertEqual(row['validity']['status'],'PASS')
        for row in rows[1:]:
            self.assertFalse(row['validity']['claim_applicable'])
            self.assertEqual(row['claim_acceptance']['status'],'INCONCLUSIVE')
            if row['metric']=='memory.heap_peak_observed':
                self.assertIsNotNone(row['normalized_cost']['value'])
            else:
                self.assertNotIn('Required claim',row['normalized_cost']['reason'])
            self.assertNotEqual(row['decisions']['claim_support']['status'],'SUPPORTED_UNDER_CONTRACT')
        spec=assessment_spec('fixture');spec['samples']=['a']
        result=assess_rows(spec,rows)
        self.assertEqual(result['acceptance'],'PASS')
        self.assertEqual(result['end_to_end_improvement'],'NOT_ESTABLISHED')

    def test_profile_claim_override_is_explicit_and_preserves_correctness(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);path,store,spec=assessment_fixture.AssessmentTests().prepare(root)
            for variant in ('baseline','candidate'):
                p=root/(variant+'.json');import json
                request=json.loads(p.read_text());request['claim']=claim();write_json(p,request)
            spec['profiles'][1]['claim']=None;write_json(path,spec)
            _,jobs=load_study(path,store)
            for job in jobs:
                for request in job['requests'].values():
                    self.assertEqual('claim' in request,job['profile_id']=='native')
                    self.assertIn('argv',request['correctness'])
            spec['profiles'][0]['claim']={**claim(),'hypothesis':'explicit profile claim'};write_json(path,spec)
            _,jobs=load_study(path,store)
            self.assertTrue(all(request['claim']['hypothesis']=='explicit profile claim'
                for job in jobs if job['profile_id']=='native' for request in job['requests'].values()))

    def test_mechanism_cost_is_descriptive_and_cannot_be_claim_acceptance(self):
        a,b=comparison_result('baseline'),comparison_result('candidate',values=[.5]*7)
        for run in (a,b):run['request']['claim']=claim('mechanism_hypothesis')
        result=compare(a,b)
        self.assertEqual(result['validity']['status'],'PASS')
        self.assertEqual(result['claim_acceptance']['status'],'INCONCLUSIVE')
        self.assertEqual(result['normalized_cost']['claim_acceptance_status'],'INCONCLUSIVE')
        self.assertIn('not causal proof',result['normalized_cost']['meaning'])
        self.assertEqual(result['decisions']['claim_support']['status'],'HYPOTHESIS_ONLY')

if __name__=='__main__':unittest.main()
