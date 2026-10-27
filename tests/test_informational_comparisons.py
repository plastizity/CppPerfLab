"""Informational comparison fixtures never represent performance evidence."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from cppperflab.comparison import compare, rejected_informational_comparison
from cppperflab.assessment import assess_rows
from cppperflab.environment import identity_snapshot
from cppperflab.errors import LabError
from cppperflab.lab import summarize
from cppperflab.paired import run_paired
from cppperflab.protocol import metric
from cppperflab.storage import Store, write_json
from cppperflab.studies import load_study
import test_assessment
import test_cases
import test_paired
import test_validity
from test_core import comparison_result, request_template

POLICY=dict(schema_version=1,min_samples=2,max_cv=.1,max_regression_fraction=.05)


class InformationalComparisonTests(unittest.TestCase):
    def fixture(self,root,gate='informational',secondary_error=None):
        request=request_template(root);request['measurement'].update(tool='perf-stat',repetitions=1,warmups=0)
        for name in ('a','b'):write_json(root/(name+'.json'),request)
        spec=test_paired.spec();spec.update(blocks=2,budget=8)
        descriptor=dict(metric='time.cpu.system',policy=dict(POLICY))
        if gate is not None:descriptor['gate']=gate
        spec['secondary_metrics']=[descriptor]
        path=root/'study.json';write_json(path,spec);spec,requests=load_study(path)
        store=Store(root/'store')
        def run(request,store):
            identifier,directory=store.create()
            r=comparison_result(identifier,values=[1])
            r['samples'][0].update(index=0)
            r['samples'][0]['metrics'][0]['evidence']='raw'
            r['samples'][0]['metrics'].append(metric('time.cpu.system',0,'seconds','fixture','same target scope','raw'))
            r.update(schema_version=1,request=copy.deepcopy(request),identity=identity_snapshot(request),started_at='start',ended_at='end',
                     error=None,summary=summarize(r['samples']),hotspots=[],evidence=[],limitations=[])
            r['environment']['limitations']=[]
            write_json(directory/'result.json',r);return r
        actual=test_paired.compare_blocks
        def compare_blocks(blocks,name,*args,**kwargs):
            if secondary_error is not None and name=='time.cpu.system':raise secondary_error
            return actual(blocks,name,*args,**kwargs)
        with mock.patch('cppperflab.paired.run',side_effect=run),mock.patch('cppperflab.paired.compare_blocks',side_effect=compare_blocks):
            return run_paired(spec,requests,store),store

    def test_zero_informational_retained_with_all_members_and_no_score(self):
        with tempfile.TemporaryDirectory() as temp:
            result,store=self.fixture(Path(temp))
            self.assertEqual(result['status'],'completed',result['error'])
            self.assertEqual(result['comparisons'][0]['comparison_gate'],'required')
            info=result['comparisons'][1]
            self.assertFalse(info['comparable']);self.assertEqual(info['comparison_gate'],'informational')
            self.assertEqual(info['acceptance']['status'],'INCONCLUSIVE')
            self.assertIsNone(info['normalized_cost']['value']);self.assertIsNone(info['speedup'])
            self.assertEqual(len(info['baseline_run_ids']),4);self.assertEqual(len(info['candidate_run_ids']),4)
            self.assertEqual(store.read(info['comparison_id'],'comparison'),info)

    def test_required_or_default_zero_keeps_atomic_failure(self):
        for gate in ('required',None):
            with self.subTest(gate=gate),tempfile.TemporaryDirectory() as temp:
                result,_=self.fixture(Path(temp),gate)
                self.assertEqual(result['status'],'failed')
                self.assertEqual(result['error']['category'],'incomparable')
                self.assertEqual(result['comparisons'],[])

    def test_non_incomparable_is_not_suppressed(self):
        with tempfile.TemporaryDirectory() as temp:
            result,_=self.fixture(Path(temp),secondary_error=LabError('storage_failed','fixture'))
            self.assertEqual(result['status'],'failed')
            self.assertEqual(result['error']['category'],'storage_failed')
            self.assertEqual(result['comparisons'],[])

    def test_rejected_work_is_unknown_but_does_not_block_primary(self):
        a,b=comparison_result('a'),comparison_result('b')
        primary=compare(a,b,policy=POLICY)
        primary.update(case_id='case',profile_id='p',assessment_role='end_to_end',assessment_gate='non_regression',comparison_id='primary')
        error=LabError('incomparable','zero ratio')
        work=rejected_informational_comparison([a],[b],'algorithm.operations',None,POLICY,error)
        work.update(case_id='case',profile_id='p',assessment_role='work',assessment_gate='informational',comparison_id='work')
        spec={'samples':['case'],'profiles':[{'comparisons':[{},{}]}]}
        assessment=assess_rows(spec,[primary,work])
        self.assertEqual(assessment['acceptance'],'PASS')
        self.assertEqual(assessment['cases'][0]['work'][0]['status'],'UNKNOWN')
        self.assertIn('unknown',assessment['cases'][0]['work_attribution'])

    def test_required_claim_failure_remains_failure_on_informational_row(self):
        a,b=comparison_result('a'),comparison_result('b')
        condition=test_validity.condition('work_metric',metric='algorithm.operations')
        for run,value in ((a,10),(b,11)):
            run['request']['claim']=test_validity.claim(conditions=[condition])
            for sample in run['samples']:
                sample['metrics'][0]['evidence']='raw'
                sample['metrics'].append(metric('algorithm.operations',value,'count','fixture','same target scope','raw'))
                sample['metrics'].append(metric('time.cpu.system',1,'seconds','fixture','same target scope','raw',evidence_state='Estimated'))
        row=rejected_informational_comparison([a],[b],'time.cpu.system',None,POLICY,LabError('incomparable','zero ratio'))
        self.assertEqual(row['validity']['status'],'FAIL')
        self.assertFalse(next(c for c in row['validity']['conditions'] if c['id']=='auxiliary_metric')['required'])
        primary=compare(a,b,policy=POLICY)
        primary.update(case_id='case',profile_id='p',assessment_role='end_to_end',assessment_gate='non_regression',comparison_id='primary')
        row.update(case_id='case',profile_id='p',assessment_role='work',assessment_gate='informational',comparison_id='info')
        spec={'samples':['case'],'profiles':[{'comparisons':[{},{}]}]}
        self.assertEqual(assess_rows(spec,[primary,row])['acceptance'],'FAIL')

    def test_missing_or_estimated_auxiliary_does_not_block_primary_claim(self):
        for evidence in ('missing','estimated'):
            with self.subTest(evidence=evidence):
                a,b=comparison_result('a'),comparison_result('b')
                condition=test_validity.condition('work_metric',metric='algorithm.operations')
                for run in (a,b):
                    run['request']['claim']=test_validity.claim(conditions=[condition])
                    for sample in run['samples']:
                        sample['metrics'][0]['evidence']='raw'
                        sample['metrics'].append(metric('algorithm.operations',10,'count','fixture','same target scope','raw'))
                        if evidence=='estimated':
                            sample['metrics'].append(metric('time.cpu.system',1,'seconds','fixture','same target scope','raw',evidence_state='Estimated'))
                primary=compare(a,b,policy=POLICY)
                primary.update(case_id='case',profile_id='p',assessment_role='end_to_end',assessment_gate='non_regression',comparison_id='primary')
                info=rejected_informational_comparison([a],[b],'time.cpu.system',None,POLICY,LabError('incomparable','auxiliary unavailable'))
                auxiliary=next(c for c in info['validity']['conditions'] if c['id']=='auxiliary_metric')
                self.assertFalse(auxiliary['required']);self.assertEqual(auxiliary['status'],'INCONCLUSIVE')
                self.assertEqual(info['validity']['status'],'PASS')
                self.assertFalse(info['comparable']);self.assertIsNone(info['speedup'])
                info.update(case_id='case',profile_id='p',assessment_role='work',assessment_gate='informational',comparison_id='info')
                spec={'samples':['case'],'profiles':[{'comparisons':[{},{}]}]}
                self.assertEqual(assess_rows(spec,[primary,info])['acceptance'],'PASS')

    def test_rejected_declared_primary_stays_required(self):
        a,b=comparison_result('a'),comparison_result('b')
        for run in (a,b):
            run['request']['claim']=test_validity.claim()
            for sample in run['samples']:sample['metrics'][0]['evidence']='raw'
        result=rejected_informational_comparison([a],[b],'time.wall',None,POLICY,LabError('incomparable','ratio invalid'))
        primary=next(c for c in result['validity']['conditions'] if c['id']=='primary_metric')
        self.assertTrue(primary['required'])
        self.assertTrue(result['validity']['claim_applicable'])

    def test_assessment_propagates_child_informational_gates(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);path,store,spec=test_assessment.AssessmentTests().prepare(root)
            spec['profiles'][0]['comparisons'][1]['gate']='informational'
            spec['profiles'][1]['comparisons'][0]['gate']='informational'
            write_json(path,spec);_,jobs=load_study(path,store)
            paired=next(job for job in jobs if job['kind']=='paired')
            unpaired=next(job for job in jobs if job['kind']=='unpaired')
            self.assertEqual(paired['spec']['secondary_metrics'][0]['gate'],'informational')
            self.assertEqual(unpaired['spec']['comparisons'][0]['gate'],'informational')

    def test_case_matrix_preserves_informational_zero_comparison(self):
        from cppperflab.cases import run_cases
        with tempfile.TemporaryDirectory() as temp:
            path,store,spec=test_cases.CaseStudyTests().prepare(Path(temp))
            spec['comparisons'][0].update(metric='time.cpu.system',gate='informational')
            write_json(path,spec);loaded,bound=load_study(path,store)
            def run(request,store):
                identifier,directory=store.create();r=comparison_result(identifier,values=[1]*3)
                for index,sample in enumerate(r['samples']):
                    sample['index']=index;sample['metrics'][0]['evidence']='raw'
                    sample['metrics'].append(metric('time.cpu.system',0,'seconds','fixture','same target scope','raw'))
                r.update(schema_version=1,request=copy.deepcopy(request),identity=identity_snapshot(request),started_at='start',ended_at='end',
                         error=None,summary=summarize(r['samples']),hotspots=[],evidence=[],limitations=[])
                r['environment']['limitations']=[];write_json(directory/'result.json',r);return r
            with mock.patch('cppperflab.cases.run',side_effect=run):result=run_cases(loaded,bound,store)
            self.assertEqual(result['status'],'completed',result['error'])
            self.assertEqual(len(result['comparisons']),2)
            self.assertTrue(all(not row['comparable'] and row['comparison_gate']=='informational' for row in result['comparisons']))
