"""Case matrix control fixtures are not real performance evidence."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from cppperflab.cases import case_budget,run_cases
from cppperflab.environment import identity_snapshot
from cppperflab.errors import LabError
from cppperflab.lab import summarize
from cppperflab.samples import capture
from cppperflab.storage import Store,write_json
from cppperflab.studies import load_study
from test_core import request_template,comparison_result
from test_samples import manifest,sample


class CaseStudyTests(unittest.TestCase):
    def prepare(self,root):
        store=Store(root/'store');source=root/'samples.json';write_json(source,manifest([sample('a'),sample('b')]))
        collection=capture(source,store)
        request=request_template(root);request['target']['argv']+=['{case_input}']
        for variant in ('baseline','candidate'):write_json(root/(variant+'.json'),request)
        policy=dict(schema_version=1,min_samples=2,max_cv=.1,max_regression_fraction=.05)
        spec=dict(schema_version=1,analysis='cases',name='case fixtures',collection_id=collection['collection_id'],samples=['a','b'],
            requests=dict(baseline='baseline.json',candidate='candidate.json'),comparisons=[dict(baseline='baseline',candidate='candidate',metric='time.wall',policy=policy)],
            seed=7,budget=4,constraints='test controller only')
        path=root/'study.json';write_json(path,spec)
        return path,store,spec

    def test_budget_is_read_only_and_case_boundaries_remain_distinct(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);path,store,_=self.prepare(root)
            before=list(store.root.rglob('*'))
            with mock.patch('cppperflab.execution.execute',side_effect=AssertionError('exec')):
                spec,bound=load_study(path,store);budget=case_budget(spec,bound)
            self.assertEqual(budget['run_calls'],4)
            self.assertFalse(budget['execution_performed'])
            self.assertEqual(before,list(store.root.rglob('*')))
            self.assertNotEqual(bound[('a','baseline')]['workload']['inputs'],bound[('b','baseline')]['workload']['inputs'])
            self.assertEqual(bound[('a','baseline')]['workload']['inputs'],bound[('a','candidate')]['workload']['inputs'])

    def test_bad_matrix_or_different_lifecycles_rejected_before_execution(self):
        for issue in ('budget','case','variant','lifecycle'):
            with tempfile.TemporaryDirectory() as d:
                root=Path(d);path,store,spec=self.prepare(root)
                if issue=='budget':spec['budget']=3
                if issue=='case':spec['samples']=['missing']
                if issue=='variant':spec['comparisons'][0]['candidate']='missing'
                if issue=='lifecycle':
                    r=request_template(root);r['target']['argv']+=['{case_input}'];r['workload']['parameters']['lifecycle']='reuse_reset'
                    write_json(root/'candidate.json',r)
                write_json(path,spec)
                with self.subTest(issue=issue),self.assertRaises(LabError):load_study(path,store)

    def execute_fixture(self,root,fail=False):
        path,store,_=self.prepare(root);spec,bound=load_study(path,store);calls=[]
        def measured(request,store):
            id,directory=store.create();calls.append(id)
            r=comparison_result(id,values=[.8 if request['name'].endswith('/candidate') else 1]*3)
            for i,s in enumerate(r['samples']):s['index']=i;s['metrics'][0]['evidence']='raw'
            r.update(schema_version=1,request=copy.deepcopy(request),identity=identity_snapshot(request),started_at='start',ended_at='end',
                     error=None,summary=summarize(r['samples']),hotspots=[],evidence=[],limitations=[])
            r['environment']['limitations']=[]
            if fail=='identity':r.update(status='failed',error=LabError('incomparable','identity fixture failed',{'acceptance':dict(status='FAIL',reason='Runtime identity mismatch')}).as_dict())
            elif fail:r.update(status='failed',error=LabError('correctness_failed','fixture failed').as_dict());r['correctness']['status']='failed'
            write_json(directory/'result.json',r);return r
        with mock.patch('cppperflab.cases.run',side_effect=measured):result=run_cases(spec,bound,store)
        return result,calls,store

    def test_complete_matrix_reuses_comparison_and_views(self):
        with tempfile.TemporaryDirectory() as d:
            r,calls,store=self.execute_fixture(Path(d))
            self.assertEqual(r['status'],'completed',r['error']);self.assertEqual(len(calls),4)
            self.assertEqual(len(r['comparisons']),2)
            self.assertTrue(all(c['normalized_cost']['value']==.8 and not c['policy']['paired'] for c in r['comparisons']))
            from cppperflab.views import build_view
            view=build_view(store,r['study_id'],'study')
            self.assertEqual(view['overview']['analysis'],'cases')
            self.assertEqual({m['case_id'] for m in view['metrics']},{'a','b'})

    def test_failed_member_stops_without_successful_subset_score(self):
        with tempfile.TemporaryDirectory() as d:
            r,calls,_=self.execute_fixture(Path(d),True)
            self.assertEqual(r['status'],'failed');self.assertEqual(len(calls),1)
            self.assertEqual(r['comparisons'],[])
            self.assertTrue(any(s['status']=='pending' for s in r['cases']))

    def test_public_cli_queries_budget_and_schema_without_running_programs(self):
        from cppperflab.cli import parser,dispatch
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);path,store,spec=self.prepare(root)
            collection=spec['collection_id']
            commands=[['budget','--kind','study','--config',str(path)],
                      ['samples','list',collection,'--correctness','passed'],
                      ['samples','verify',collection,'--sample','a'],
                      ['replay',collection,'--sample','a','--config',str(root/'baseline.json'),'--mode','budget'],
                      ['schema','study'],['schema','collection'],['schema','sample-manifest']]
            with mock.patch('cppperflab.execution.execute',side_effect=AssertionError('program executed')):
                rows=[dispatch(parser().parse_args([*args,'--store',str(store.root)])) for args in commands]
            self.assertEqual(rows[0]['run_calls'],4)
            self.assertEqual(rows[1]['pagination']['total'],2)
            self.assertEqual(rows[2]['status'],'verified')
            self.assertFalse(rows[3]['execution_performed'])
            self.assertEqual(next(b for b in rows[4]['oneOf'] if b['properties'].get('analysis',{}).get('const')=='cases')['properties']['analysis']['const'],'cases')
            output=root/'prepared.json'
            dispatch(parser().parse_args(['replay',collection,'--sample','a','--config',str(root/'baseline.json'),
                     '--mode','prepare','--output',str(output),'--store',str(store.root)]))
            from cppperflab.protocol import load_request
            self.assertEqual(len(load_request(output)['workload']['expected_hashes']),2)
            with self.assertRaises(LabError):
                dispatch(parser().parse_args(['samples','list',collection,'--sample','a','--store',str(store.root)]))

    def test_result_embedded_request_schema_tracks_current_optional_contracts(self):
        import json
        from importlib.resources import files
        schemas=files('cppperflab').joinpath('schemas')
        request=json.loads(schemas.joinpath('request.schema.json').read_text())
        for key in ('$schema','$id','title'):request.pop(key,None)
        result=json.loads(schemas.joinpath('result.schema.json').read_text())
        self.assertEqual(result['properties']['request'],request)
        self.assertIn('expected_hashes',request['properties']['workload']['properties'])
        self.assertIn('phase_report',request['properties']['measurement']['properties'])

    def test_identity_gate_verdict_is_preserved_for_parent_assessments(self):
        with tempfile.TemporaryDirectory() as d:
            r,calls,_=self.execute_fixture(Path(d),fail='identity')
            self.assertEqual(len(calls),1)
            self.assertEqual(r['error']['details']['acceptance']['status'],'FAIL')
