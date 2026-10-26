"""Paired estimators and lifecycle tests. Fabricated fixtures are never real measurements."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from cppperflab.comparison import compare
from cppperflab.environment import identity_snapshot
from cppperflab.errors import LabError
from cppperflab.paired import compare_blocks, paired_budget, run_paired, schedule
from cppperflab.protocol import load_request
from cppperflab.storage import Store,write_json
from cppperflab.studies import load_study
from test_core import comparison_result,request_template

POLICY=dict(schema_version=1,min_samples=2,max_cv=.1,max_regression_fraction=.05)


def measured(id,value,variant):
    r=comparison_result(id,values=[value]);r['identity']['artifacts']={'binary':variant}
    r['samples'][0].update(index=0)
    r['samples'][0]['metrics'][0]['evidence']='raw.txt'
    return r


def blocks(order='AB',n=4):
    result=[]
    for i in range(n):
        members=[]
        for slot,letter in enumerate(order):
            v='baseline' if letter=='A' else 'candidate'
            members.append({'variant':v,'slot':slot,'run':measured(str(i)+'-'+str(slot),(i+1)*(1 if letter=='A' else .5),v)})
        result.append({'block_id':str(i),'order':order,'status':'completed','members':members})
    return result


def spec():
    return dict(schema_version=1,analysis='paired',name='paired-test',baseline='a.json',candidate='b.json',blocks=3,
                order='abba',seed=17,budget=12,metric='time.wall',policy=dict(POLICY),constraints='same declared workload',
                resource_conditions=dict(concurrent_runs=1,background_load='uncontrolled',notes='synthetic test'))


class PairedTests(unittest.TestCase):
    def test_joint_resampling_preserves_shared_drift(self):
        r=compare_blocks(blocks(),'time.wall',POLICY)
        self.assertEqual(r['speedup'],2)
        self.assertEqual(r['bootstrap_95_percent_speedup'],[2,2])
        self.assertTrue(r['policy']['paired'])
        self.assertEqual(r['paired_ratio_cv'],0)
        self.assertTrue(r['stable_samples'])
        self.assertGreater(r['coefficient_of_variation'][0],.1)
        self.assertEqual(r['sample_unit'],'block')
        self.assertEqual(len(r['baseline_run_ids']),4)

    def test_abba_has_one_representative_per_block(self):
        data=blocks('ABBA',3)
        for b in data:
            for m,v in zip(b['members'],[10,4,6,14]):m['run']['samples'][0]['metrics'][0]['value']=v
        r=compare_blocks(data,'time.wall',POLICY)
        self.assertEqual(r['baseline_samples'],[12]*3)
        self.assertEqual(r['candidate_samples'],[5]*3)
        self.assertEqual(r['speedup'],2.4)
        self.assertEqual(len(r['baseline_run_ids']),6)
        self.assertEqual(len(r['block_pairs']),3)

    def test_bad_evidence_or_incomplete_blocks_rejected(self):
        for issue in ('duplicate','missing','failed','modeled','correctness','semantics','artifact'):
            data=blocks()
            if issue=='duplicate':data[1]['members'][0]['run']['run_id']=data[0]['members'][0]['run']['run_id']
            if issue=='missing':data[0]['members'].pop()
            if issue=='failed':data[0]['status']='failed'
            if issue=='modeled':data[0]['members'][0]['run']['samples'][0]['metrics'][0]['evidence_state']='Modeled'
            if issue=='correctness':data[0]['members'][0]['run']['correctness']['status']='failed'
            if issue=='semantics':data[0]['members'][1]['run']['samples'][0]['metrics'][0]['scope']='other'
            if issue=='artifact':data[1]['members'][0]['run']['identity']['artifacts']['binary']='changed'
            with self.subTest(issue=issue),self.assertRaises(LabError):compare_blocks(data,'time.wall',POLICY)

    def test_ba_first_block_still_checks_cross_variant_conditions(self):
        data=blocks('BA',2)
        data[0]['members'][0]['run']['request']['workload']['parameters']['n']=100
        with self.assertRaises(LabError):compare_blocks(data,'time.wall',POLICY)

    def test_seeded_schedule_and_serial_only_boundary(self):
        s=spec();s.update(order='random_ab_ba',blocks=20,budget=40)
        self.assertEqual(schedule(s),schedule(s))
        self.assertEqual({b['order'] for b in schedule(s)},{'AB','BA'})
        self.assertEqual(len({b['block_id'] for b in schedule(s)}),20)

    def make_config(self,root):
        r=request_template(root);r['measurement'].update(tool='perf-stat',repetitions=1,warmups=0)
        for name in ('a','b'):write_json(root/(name+'.json'),r)
        write_json(root/'study.json',spec())
        return root/'study.json'

    def test_loader_budget_and_rejected_designs_do_not_execute(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);path=self.make_config(root)
            with mock.patch('cppperflab.execution.execute',side_effect=AssertionError('Executed')):
                s,requests=load_study(path);budget=paired_budget(s,requests)
            self.assertEqual(budget['run_calls'],12)
            self.assertEqual(budget['variants']['baseline']['run_calls'],6)
            self.assertFalse(budget['execution_performed'])
            for issue in ('budget','concurrent','repetitions','auto','unknown','workload','null_scope','tool'):
                self.make_config(root);s=spec()
                if issue=='budget':s['budget']=11
                if issue=='concurrent':s['resource_conditions']['concurrent_runs']=2
                if issue=='unknown':s['extra']=0
                if issue=='null_scope':s['metric_scope']=None
                if issue=='tool':
                    r=json.loads((root/'b.json').read_text());r['measurement']['tool']='hyperfine'
                    write_json(root/'b.json',r)
                if issue=='workload':
                    r=json.loads((root/'b.json').read_text());r['workload']['parameters']['size']=999
                    write_json(root/'b.json',r)
                if issue in ('repetitions','auto'):
                    r=json.loads((root/'a.json').read_text())
                    r['measurement']['repetitions']=9 if issue=='repetitions' else 1
                    if issue=='auto':r['measurement']['tool']='auto';r['goal']=dict(metrics=['time.wall'],max_overhead='low',allow_instrumentation=False)
                    write_json(root/'a.json',r)
                write_json(path,s)
                with self.subTest(issue=issue),self.assertRaises(LabError):load_study(path)

    def run_fixture(self,root,fail_at=None,mutate=None,failure_category="correctness_failed"):
        path=self.make_config(root);s,requests=load_study(path);calls=[]
        if mutate is not None:
            for r in requests.values():r['target']['build']['artifacts']=[str(mutate)]
        def execute(request,store):
            index=len(calls);calls.append(request['name'])
            id,directory=store.create()
            variant='baseline' if request['name'].endswith('/baseline') else 'candidate'
            r=measured(id,1 if variant=='baseline' else .8,variant)
            r.update(schema_version=1,request=copy.deepcopy(request),identity=identity_snapshot(request),started_at='start',ended_at='end',error=None,summary=[],hotspots=[],evidence=[],limitations=[])
            r['environment']['limitations']=[]
            if index==fail_at:
                r.update(status='cancelled' if failure_category=='cancelled' else 'failed',error=LabError(failure_category,'test member rejected').as_dict())
                r['correctness']['status']='failed'
            write_json(directory/'result.json',r)
            if mutate is not None:mutate.write_text('changed')
            return r
        with mock.patch('cppperflab.paired.run',side_effect=execute):
            result=run_paired(s,requests,Store(root/'store'))
        return result,calls

    def test_lifecycle_stores_all_runs_and_comparisons(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);r,calls=self.run_fixture(root)
            self.assertEqual(r['status'],'completed',r['error'])
            self.assertEqual(len(calls),12)
            self.assertEqual(len(r['comparisons'][0]['block_pairs']),3)
            cid=r['comparisons'][0]['comparison_id']
            self.assertTrue(Store(root/'store').record_path(cid,'comparison').is_file())
            from cppperflab.views import build_view,create_summary
            self.assertEqual(build_view(Store(root/'store'),r['study_id'],'study')['overview']['analysis'],'paired')
            summary=create_summary(Store(root/'store'),[],[cid])
            self.assertEqual(len(summary['overview']['runs']),12)

    def test_failure_stops_without_retries_or_partial_score(self):
        with tempfile.TemporaryDirectory() as d:
            r,calls=self.run_fixture(Path(d),fail_at=1)
            self.assertEqual(r['status'],'failed')
            self.assertEqual(len(calls),2)
            self.assertEqual(r['comparisons'],[])
            self.assertEqual(r['blocks'][0]['status'],'failed')
            self.assertEqual(r['blocks'][1]['status'],'pending')

    def test_changed_artifact_is_rejected_before_next_launch(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);artifact=root/'artifact';artifact.write_text('original')
            r,calls=self.run_fixture(root,mutate=artifact)
            self.assertEqual(r['status'],'failed')
            self.assertEqual(len(calls),1)
            self.assertIn('before paired member launch',r['error']['message'])

    def test_child_cancellation_is_not_reported_as_success_or_retry(self):
        with tempfile.TemporaryDirectory() as d:
            r,calls=self.run_fixture(Path(d),fail_at=1,failure_category='cancelled')
            self.assertEqual(r['status'],'cancelled')
            self.assertEqual(r['blocks'][0]['status'],'cancelled')
            self.assertEqual(len(calls),2)
            self.assertEqual(r['comparisons'],[])
