import json
from pathlib import Path
import tempfile
import unittest

from cppperflab.phases import normalize
from cppperflab.comparison import compare
from cppperflab.errors import LabError
from test_core import comparison_result


def row(id='p',parent=None,lo=10,hi=90,complete=True,track='t'):
    return dict(id=id,name=id,parent_id=parent,track_id=track,call_id=id,input_id='input-1',start_ns=lo,end_ns=hi,
                children_complete=complete,result='uncertified',correctness='passed',work={'searches':{'value':1,'unit':'count','evidence_state':'Measured'}})

def report():return dict(schema_version=1,scope='compute',start_ns=0,end_ns=100,stages=[row(),row('a','p',20,60),row('b','p',40,70)])


class PhaseTests(unittest.TestCase):
    def parse(self,r):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'run.phases.json';p.write_text(json.dumps(r));return normalize(p)

    def test_child_union_no_double_count_and_result_not_failure(self):
        r=self.parse(report());p=r['phases'][0]
        self.assertAlmostEqual(p['inclusive_seconds'],80e-9)
        self.assertAlmostEqual(p['self_seconds'],30e-9)
        self.assertFalse(r['correctness_failed'])
        m={m['name']:m for m in r['metrics'] if m['scope']=='compute'}
        self.assertAlmostEqual(m['phase.uncovered_wall']['value'],20e-9)
        self.assertEqual(r['phases'][1]['path'],['p','a'])

    def test_incomplete_coverage_is_missing_not_self_time(self):
        r=report();r['stages'][0]['children_complete']=False
        result=self.parse(r)
        self.assertIsNone(result['phases'][0]['self_seconds'])
        self.assertAlmostEqual(result['phases'][0]['unattributed_seconds'],30e-9)
        self.assertEqual(result['unavailable_observations'][0]['evidence_state'],'Missing')

    def test_parallel_roots_are_wall_union_not_thread_sum(self):
        r=report();r['stages']=[row('x',None,10,80,track='one'),row('y',None,40,90,track='two')]
        metrics={m['name']:m for m in self.parse(r)['metrics'] if m['scope']=='compute'}
        self.assertAlmostEqual(metrics['phase.covered_wall']['value'],80e-9)

    def test_malformed_records_rejected(self):
        for change in ('cycle','parent','track','range','duplicate','unit'):
            r=report()
            if change=='cycle':r['stages'][0]['parent_id']='a'
            if change=='parent':r['stages'][1]['parent_id']='absent'
            if change=='track':r['stages'][1]['track_id']='other'
            if change=='range':r['stages'][1]['end_ns']=101
            if change=='duplicate':r['stages'][1]['id']='p'
            if change=='unit':r['stages'][1]['work']['searches']['value']=True
            with self.subTest(change=change),self.assertRaises(LabError):self.parse(r)

    def test_failed_and_unchecked_correctness(self):
        r=report();r['stages'][1]['correctness']='failed'
        self.assertTrue(self.parse(r)['correctness_failed'])
        r['stages'][1]['correctness']='not_checked'
        result=self.parse(r)
        self.assertTrue(any(m.get('correctness_status')=='not_checked' for m in result['metrics']))

    def test_scope_selects_one_stage_without_relaxing_semantics(self):
        base,candidate=comparison_result('a'),comparison_result('b')
        for run in (base,candidate):
            for sample in run['samples']:
                sample['metrics']=[dict(name='phase.inclusive_time',value=.1,unit='seconds',method='target-phase-events',scope=scope,
                                        evidence='raw',evidence_state='Measured',correctness_status='passed') for scope in ('stage-a','stage-b')]
        with self.assertRaises(LabError):compare(base,candidate,'phase.inclusive_time')
        r=compare(base,candidate,'phase.inclusive_time',metric_scope='stage-a')
        self.assertEqual(r['normalized_cost']['value'],1)
        candidate['samples'][0]['metrics'][0]['correctness_status']='not_checked'
        with self.assertRaises(LabError):compare(base,candidate,'phase.inclusive_time',metric_scope='stage-a')

    def test_work_models_remain_modeled(self):
        r=report()
        for e in r['stages']:e['work']['searches']['evidence_state']='Modeled'
        metrics=[m for m in self.parse(r)['metrics'] if m['name']=='phase.work']
        self.assertTrue(all(m['evidence_state']=='Modeled' for m in metrics))

    def test_input_and_result_filters_are_explicit(self):
        from cppperflab.views import base_view,query_view
        view=base_view('run','id','completed',None)
        view['phases']=self.parse(report())['phases']
        self.assertEqual(query_view(view,'phases',input_id='input-1',result_class='uncertified')['pagination']['total'],3)
        self.assertEqual(query_view(view,'phases',input_id='absent')['pagination']['total'],0)
        with self.assertRaises(LabError):query_view(view,'metrics',input_id='input-1')

    def test_orchestration_preserves_phases_and_rejects_correctness_failure(self):
        import sys
        from unittest import mock
        from cppperflab.lab import run
        from cppperflab.storage import Store
        from test_core import request_template
        class Adapter:
            batch=False
            def describe(self):return {'id':'test-phase','mode':'test'}
            def prepare(self,command,*args,**kwargs):return command
            def parse(self,folder,options,record):
                if record['returncode']:raise LabError('target_failed','test target exit')
                return {'metrics':[],'hotspots':[]}
        with tempfile.TemporaryDirectory() as d:
            directory=Path(d);target=directory/'target.py'
            request=request_template(d)
            request['measurement'].update(tool='test-phase',phase_report='test.phases.json')
            request['target']['argv']=[sys.executable,str(target),'{run_dir}/test.phases.json']
            plan={'selected':'test-phase','probes':{'tools':{'test-phase':{'version':'test'}}}}
            for failed,exit_code in ((False,0),(True,0),(True,2)):
                data=report()
                if failed:data['stages'][1]['correctness']='failed'
                target.write_text('import sys\nfrom pathlib import Path\nPath(sys.argv[1]).write_text('+repr(json.dumps(data))+')\nsys.exit('+str(exit_code)+')\n')
                with mock.patch('cppperflab.lab.plan',return_value=plan),mock.patch.dict('cppperflab.lab.REGISTRY',{'test-phase':Adapter()}):
                    result=run(request,Store(directory/'store'))
                self.assertEqual(result['status'],'failed' if failed else 'completed',result.get('error'))
                self.assertTrue(any(e['path']==('preflight/test.phases.json' if exit_code else 'measurement-000/test.phases.json') for e in result['evidence']))
                if exit_code:self.assertTrue(all(p['execution_stage']=='preflight' and p['sample'] is None for p in result['phases']))
                if failed:
                    self.assertEqual(result['error']['category'],'correctness_failed')
                    self.assertEqual(result['correctness']['status'],'failed')
                else:self.assertEqual(len(result['phases']),9)

    def test_explicit_monotonic_clock_maps_each_track_without_guessing_legacy_origins(self):
        r=report();legacy=self.parse(r)
        self.assertIsNone(legacy['clock']);self.assertNotIn('absolute_start_ns',legacy['phases'][0])
        r['clock']=dict(name='CLOCK_MONOTONIC',origin_ns=1000,boot_id='boot',pid_namespace='pidns',time_namespace='timens',tracks={'t':dict(pid=7,tid=9)})
        parsed=self.parse(r)
        self.assertEqual(parsed['phases'][0]['absolute_start_ns'],1010)
        self.assertEqual(parsed['phases'][0]['absolute_end_ns'],1090)
        self.assertEqual((parsed['phases'][0]['pid'],parsed['phases'][0]['tid']),(7,9))
        for issue in ('track','origin','name'):
            changed=json.loads(json.dumps(r))
            if issue=='track':changed['clock']['tracks']={}
            if issue=='origin':changed['clock']['origin_ns']=True
            if issue=='name':changed['clock']['name']='unverified_relative_clock'
            with self.subTest(issue=issue),self.assertRaises(LabError):self.parse(changed)
