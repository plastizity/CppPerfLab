"""Control fixtures test assessment semantics; these are not performance measurements."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from cppperflab.assessment import assess_rows, assessment_budget, run_assessment
from cppperflab.comparison import compare
from cppperflab.environment import identity_snapshot
from cppperflab.errors import LabError
from cppperflab.lab import summarize
from cppperflab.samples import capture
from cppperflab.storage import Store, write_json
from cppperflab.studies import load_study
from test_core import comparison_result, request_template
from test_samples import manifest, sample

POLICY=dict(schema_version=1,min_samples=2,max_cv=.1,max_regression_fraction=.05)


def descriptor(id,role,metric,gate='informational'):
    return dict(id=id,role=role,metric=metric,gate=gate,policy=dict(POLICY))


def assessment_spec(collection):
    return dict(schema_version=1,analysis='assessment',name='synthetic assessment',collection_id=collection,samples=['a','b'],
        requests=dict(baseline='baseline.json',candidate='candidate.json'),profiles=[
            dict(id='native',tool='perf-stat',options=dict(events=['duration_time']),warmups=0,design=dict(kind='paired',blocks=3,order='random_ab_ba'),
                 comparisons=[descriptor('process','end_to_end','time.wall','non_regression'),descriptor('work','work','algorithm.operations')]),
            dict(id='heap',tool='massif',options={},warmups=0,design=dict(kind='unpaired',repetitions=3),
                 comparisons=[descriptor('heap','resource','memory.heap_peak_observed','non_regression')])],seed=17,budget=16,
        constraints='Synthetic fixture controls only',resource_conditions=dict(concurrent_runs=1,background_load='uncontrolled',notes='test'))


def row(metric,role,gate,baseline,candidate,case='a'):
    a=comparison_result('a',values=baseline);b=comparison_result('b',values=candidate)
    for run in (a,b):
        for sample in run['samples']:
            sample['metrics'][0].update(name=metric,unit='bytes' if role=='resource' else 'count' if role=='work' else 'seconds')
    c=compare(a,b,metric,policy=POLICY)
    c.update(case_id=case,profile_id='profile-'+role,assessment_role=role,assessment_gate=gate,comparison_id=case+'-'+role)
    return c


class AssessmentTests(unittest.TestCase):
    def prepare(self,root):
        store=Store(root/'store');path=root/'samples.json';write_json(path,manifest([sample('a'),sample('b')]))
        collection=capture(path,store)
        request=request_template(root);request['target']['argv']+=['{case_input}']
        for variant in ('baseline','candidate'):write_json(root/(variant+'.json'),request)
        spec=assessment_spec(collection['collection_id']);path=root/'assessment.json';write_json(path,spec)
        return path,store,spec

    def test_loader_budget_composes_existing_designs_without_execution_or_writes(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);path,store,_=self.prepare(root);before=list(store.root.rglob('*'))
            with mock.patch('cppperflab.execution.execute',side_effect=AssertionError('Executed target')):
                spec,jobs=load_study(path,store);budget=assessment_budget(spec,jobs)
            self.assertEqual(budget['run_calls'],16);self.assertFalse(budget['execution_performed'])
            self.assertEqual(before,list(store.root.rglob('*')))
            self.assertEqual(len(jobs),4)
            a=[j for j in jobs if j['sample_id']=='a']
            self.assertEqual(a[0]['requests']['baseline']['workload'],a[1]['requests']['baseline']['workload'])

    def test_bad_budget_role_outcome_work_and_tool_rejected_before_execution(self):
        for issue in ('budget','role','outcome','work','tool','lifecycle','target','batch','direction'):
            with tempfile.TemporaryDirectory() as d:
                root=Path(d);path,store,spec=self.prepare(root)
                if issue=='budget':spec['budget']=15
                if issue=='role':spec['profiles'][1]['comparisons'][0]['role']='stage'
                if issue=='outcome':spec['profiles'][0]['comparisons'][0]['gate']='informational'
                if issue=='work':spec['profiles'][0]['comparisons'].pop()
                if issue=='tool':spec['profiles'][1]['tool']='auto'
                if issue=='batch':spec['profiles'][1]['tool']='hyperfine'
                if issue=='target':spec['profiles'][0]['comparisons'][0]['gate']='performance_target'
                if issue=='direction':spec['profiles'][0]['comparisons'][0]['policy']['direction']='higher'
                if issue=='lifecycle':
                    r=json.loads((root/'candidate.json').read_text());r['workload']['parameters']['lifecycle']='changed'
                    write_json(root/'candidate.json',r)
                write_json(path,spec)
                with self.subTest(issue=issue),self.assertRaises(LabError):load_study(path,store)

    def rows(self):
        spec=assessment_spec('collection');spec['samples']=['a']
        # The root spec supplies exactly three comparison definitions for this pure gate test.
        return spec,[row('time.wall','end_to_end','non_regression',[1]*7,[.94,1.02,.98,1.04,.99,1.01,1]),
                     row('algorithm.operations','work','informational',[100]*7,[80]*7),
                     row('memory.heap_peak_observed','resource','non_regression',[100]*7,[100]*7)]

    def test_pass_does_not_claim_acceleration_and_changed_work_blocks_attribution(self):
        spec,rows=self.rows();r=assess_rows(spec,rows)
        self.assertEqual(r['acceptance'],'PASS');self.assertEqual(r['end_to_end_improvement'],'NOT_ESTABLISHED')
        self.assertFalse(r['optimization_accepted'])
        self.assertIn('no single-step',r['cases'][0]['work_attribution'])

    def test_memory_regression_is_not_hidden_by_faster_time(self):
        spec,rows=self.rows()
        rows[0]=row('time.wall','end_to_end','non_regression',[1]*7,[.7]*7)
        rows[2]=row('memory.heap_peak_observed','resource','non_regression',[100]*7,[120]*7)
        r=assess_rows(spec,rows)
        self.assertEqual(r['acceptance'],'FAIL');self.assertEqual(r['end_to_end_improvement'],'SUPPORTED_IMPROVEMENT')
        self.assertFalse(r['optimization_accepted'])

    def test_correctness_and_every_case_remain_hard_gates(self):
        spec,rows=self.rows();rows[1]['correctness_status']='failed'
        self.assertEqual(assess_rows(spec,rows)['acceptance'],'FAIL')
        with self.assertRaises(LabError):assess_rows(spec,rows[:-1])
        spec,rows=self.rows();spec['samples']=['a','b']
        other=copy.deepcopy(rows)
        for r in other:r['case_id']='b';r['comparison_id']='b-'+r['assessment_role']
        other[2]=row('memory.heap_peak_observed','resource','non_regression',[100]*7,[120]*7,case='b')
        self.assertEqual(assess_rows(spec,rows+other)['acceptance'],'FAIL')

    def execute(self,root,fail_at=None,mutate=None,mutate_at=None,failure='correctness_failed',failure_acceptance=None):
        path,store,spec=self.prepare(root)
        if mutate:
            for v in ('baseline','candidate'):
                p=root/(v+'.json');r=json.loads(p.read_text());r['target']['build']['artifacts']=[str(mutate)];write_json(p,r)
        spec,jobs=load_study(path,store);calls=[]
        def measured(request,store):
            id,directory=store.create();calls.append(id)
            r=comparison_result(id,values=[1]*request['measurement']['repetitions'])
            for i,s in enumerate(r['samples']):
                s['index']=i;s['metrics'][0]['evidence']='raw'
                for name,value,unit in [('algorithm.operations',100,'count'),('memory.heap_peak_observed',100,'bytes')]:
                    s['metrics'].append(dict(name=name,value=value,unit=unit,method='test fixture',scope='test',evidence='raw',evidence_state='Measured'))
            r.update(schema_version=1,request=copy.deepcopy(request),identity=identity_snapshot(request),started_at='start',ended_at='end',
                     error=None,summary=summarize(r['samples']),hotspots=[],evidence=[],limitations=[])
            r['environment']['limitations']=[]
            if len(calls)==fail_at:r.update(status='failed',error=LabError(failure,'fixture rejected',{'acceptance':failure_acceptance} if failure_acceptance else {}).as_dict());r['correctness']['status']='failed' if failure=='correctness_failed' else 'passed'
            write_json(directory/'result.json',r)
            if mutate and (mutate_at is None or len(calls)==mutate_at):mutate.write_text('changed')
            return r
        with mock.patch('cppperflab.paired.run',side_effect=measured),mock.patch('cppperflab.cases.run',side_effect=measured):
            result=run_assessment(spec,jobs,store)
        return result,calls,store

    def test_full_matrix_delegates_runs_and_saves_child_provenance(self):
        with tempfile.TemporaryDirectory() as d:
            r,calls,store=self.execute(Path(d))
            self.assertEqual(r['status'],'completed',r['error']);self.assertEqual(len(calls),16)
            self.assertEqual(r['assessment']['acceptance'],'PASS')
            self.assertEqual(len(r['comparisons']),6);self.assertEqual(len(r['identities']),4)
            for c in r['comparisons']:self.assertTrue(store.record_path(c['comparison_id'],'comparison').is_file())
            from cppperflab.views import build_view
            view=build_view(store,r['study_id'],'study')
            self.assertEqual(view['summary']['assessment'],r['assessment'])
            self.assertEqual({m['profile_id'] for m in view['metrics']},{'native','heap'})
            self.assertEqual({f['case_id'] for f in view['summary']['findings']},{'a','b'})
            from cppperflab.html_report import render_html
            from test_views import SnapshotParser
            document=render_html(view,store,Path(d)/'assessment.html')
            parser=SnapshotParser();parser.feed(document)
            self.assertEqual(json.loads(parser.snapshot),view)
            self.assertIn('联合性能评估',document)
            self.assertIn('全过程加速',document)
            # Saved child configurations are ordinary reproducible public Studies.
            configs=store.record_path(r['study_id'],'study').parent.glob('job-*/study.json')
            self.assertEqual(len([load_study(p,store) for p in configs]),4)

    def test_failed_profile_stops_without_retries_partial_acceptance_or_dropping_case(self):
        with tempfile.TemporaryDirectory() as d:
            r,calls,_=self.execute(Path(d),fail_at=1)
            self.assertEqual(r['status'],'failed');self.assertEqual(len(calls),1)
            self.assertEqual(r['assessment']['acceptance'],'FAIL')
            self.assertEqual(r['assessment']['correctness'],'FAIL')
            self.assertEqual(len(r['runs']),1);self.assertTrue(any(j['status']=='pending' for j in r['jobs']))

    def test_changed_identity_cannot_link_profiles(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'artifact';path.write_text('original')
            r,calls,_=self.execute(Path(d),mutate=path)
            self.assertEqual(r['status'],'failed');self.assertIn('changed',str(r['error']))
            self.assertNotEqual(r['assessment']['acceptance'],'PASS')

    def test_cli_budget_and_schema_expose_assessment_without_launches(self):
        from cppperflab.cli import dispatch,parser
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);path,store,_=self.prepare(root)
            with mock.patch('cppperflab.execution.execute',side_effect=AssertionError('executed')):
                result=dispatch(parser().parse_args(['budget','--kind','study','--config',str(path),'--store',str(store.root)]))
            self.assertEqual(result['analysis'],'assessment');self.assertEqual(result['run_calls'],16)
            schema=dispatch(parser().parse_args(['schema','study']))
            self.assertTrue(any(b['properties'].get('analysis',{}).get('const')=='assessment' for b in schema['oneOf']))

    def test_denied_profile_remains_unsupported_and_cancellation_is_terminal(self):
        for category in ('permission_denied','cancelled'):
            with tempfile.TemporaryDirectory() as d:
                r,calls,_=self.execute(Path(d),fail_at=1,failure=category)
                self.assertEqual(len(calls),1)
                if category=='permission_denied':self.assertEqual(r['assessment']['acceptance'],'UNSUPPORTED')
                else:self.assertEqual(r['status'],'cancelled')
                self.assertNotEqual(r['assessment']['acceptance'],'PASS')

    def test_parent_rejects_artifact_change_after_successful_child_before_next_profile(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'artifact';path.write_text('original')
            r,calls,store=self.execute(Path(d),mutate=path,mutate_at=6)
            self.assertEqual(len(calls),6)
            self.assertEqual(r['status'],'failed')
            self.assertIn('after profile execution',r['error']['message'])
            child=store.read(r['jobs'][0]['study_id'],'study')
            self.assertEqual(child['status'],'completed')
            self.assertNotEqual(r['assessment']['acceptance'],'PASS')

    def test_explicit_failed_identity_gate_survives_child_context_wrapping(self):
        with tempfile.TemporaryDirectory() as d:
            gate=dict(status='FAIL',reason='Runtime identity mismatch fixture')
            r,calls,_=self.execute(Path(d),fail_at=1,failure='incomparable',failure_acceptance=gate)
            self.assertEqual(r['status'],'failed');self.assertEqual(len(calls),1)
            self.assertEqual(r['assessment']['acceptance'],'FAIL')
            self.assertEqual(r['error']['details']['error']['details']['acceptance'],gate)
