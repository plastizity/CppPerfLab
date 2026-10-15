import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from cppperflab.solver import normalize, validate_contract
from cppperflab.protocol import load_request
from cppperflab.storage import Store
from cppperflab.lab import run
from cppperflab.comparison import compare
from cppperflab.errors import LabError
from test_core import request_template, comparison_result


def contract():
    return dict(argv=[sys.executable,'check.py','{sample_dir}/solve.solver.json'],report='solve.solver.json',residual_norm='l2_absolute',max_residual=1e-8,max_absolute_error=1e-7,max_relative_error=1e-7,max_iterations=100)

def reports():
    return (dict(schema_version=1,problem_id='p',termination='converged',iterations=2,residual_norm='l2_absolute',residual_history=[1,.1,1e-9],setup_seconds=.01,solve_seconds=.02,total_seconds=.04,solution=[1,2]),
            dict(schema_version=1,problem_id='p',residual_norm='l2_absolute',residual=1e-9,absolute_error=1e-9,relative_error=1e-10))

class SolverTests(unittest.TestCase):
    def normalize(self,r=None,v=None):
        original_r,original_v=reports()
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'solve.solver.json').write_text(json.dumps(original_r if r is None else r));(p/'stdout.txt').write_text(json.dumps(original_v if v is None else v))
            return normalize(p/'solve.solver.json',p/'stdout.txt',contract(),'p')

    def test_valid_independent_metrics_and_full_history(self):
        result=self.normalize()
        self.assertEqual(result['status'],'PASS')
        self.assertEqual(result['residual_history'],[1,.1,1e-9])
        values={m['name']:m for m in result['metrics']}
        self.assertEqual(values['solver.total_time']['value'],.04)
        self.assertEqual(values['numerical.residual']['method'],'independent-solver-validation')
        self.assertTrue(all(m['evidence_state']=='Measured' for m in values.values()))

    def test_failed_hard_gates_are_not_missing_or_pass(self):
        r,v=reports();r['termination']='max_iterations';v['residual']=.1
        result=self.normalize(r,v)
        self.assertEqual(result['status'],'FAIL')
        self.assertEqual(result['violations'],['termination','max_residual'])
        for key in ('absolute_error','relative_error'):
            r,v=reports();v[key]=1
            self.assertIn('max_'+key,self.normalize(r,v)['violations'])

    def test_malformed_reports_rejected(self):
        for key,value in [('iterations',True),('iterations',3),('residual_history',[1,-1,0]),('solution',[float('nan')]),('total_seconds',.001),('schema_version',2),('problem_id','other')]:
            r,v=reports();r[key]=value
            with self.subTest(key=key),self.assertRaises(LabError):self.normalize(r,v)
        for value in (None,True,-1,float('inf')):
            r,v=reports();v['residual']=value
            with self.assertRaises(LabError):self.normalize(r,v)

    def test_contract_and_legacy_request_compatibility(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'request.json';request=request_template(d)
            path.write_text(json.dumps(request));self.assertNotIn('sample_validation',load_request(path)['correctness'])
            request['correctness']['sample_validation']=contract()
            path.write_text(json.dumps(request));self.assertIn('sample_validation',load_request(path)['correctness'])
        for key,value in [('report','../escape'),('max_iterations',0),('max_residual',-1),('residual_norm','unknown')]:
            c=contract();c[key]=value
            with self.assertRaises(LabError):validate_contract(c)

    def test_missing_per_solve_proof_cannot_score(self):
        a,b=comparison_result('a'),comparison_result('b')
        with self.assertRaises(LabError):compare(a,b,'solver.total_time')
        a['request']['correctness']['sample_validation']=contract()
        with self.assertRaises(LabError):compare(a,b)

    def test_run_calls_validator_for_each_measured_execution(self):
        # A test-only adapter exercises orchestration; it is not measurement evidence.
        class Adapter:
            batch=False
            def describe(self):return dict(id='test-solver',mode='test')
            def prepare(self,command,folder,options,**kwargs):return command
            def parse(self,folder,options,record):return dict(metrics=[],hotspots=[])
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);r,v=reports()
            target=root/'target.py'
            target.write_text('import json,sys\nfrom pathlib import Path\nPath(sys.argv[1]).write_text('+repr(json.dumps(r))+')\n')
            checker=root/'checker.py';checker.write_text('print('+repr(json.dumps(v))+')\n')
            request=request_template(d);request['workload']['id']='p';request['measurement']['tool']='test-solver'
            request['target']['argv']=[sys.executable,str(target),'{run_dir}/solve.solver.json']
            request['correctness']['sample_validation']={**contract(),'argv':[sys.executable,str(checker)]}
            plan=dict(selected='test-solver',probes={'tools':{'test-solver':{'version':'test'}}})
            with mock.patch('cppperflab.lab.plan',return_value=plan),mock.patch.dict('cppperflab.lab.REGISTRY',{'test-solver':Adapter()}):
                result=run(request,Store(root/'store'))
            self.assertEqual(result['status'],'completed',result.get('error'))
            self.assertEqual(len(result['solver_samples']),3)
            self.assertEqual(result['correctness']['sample_validation_status'],'passed')
            self.assertEqual(len(result['samples']),3)
            self.assertTrue(all(len(s['metrics'])==7 for s in result['samples']))
            v['residual']=1
            checker.write_text('print('+repr(json.dumps(v))+')\n')
            with mock.patch('cppperflab.lab.plan',return_value=plan),mock.patch.dict('cppperflab.lab.REGISTRY',{'test-solver':Adapter()}):
                failed=run(request,Store(root/'store'))
            self.assertEqual(failed['error']['category'],'correctness_failed')
            self.assertEqual(failed['correctness']['status'],'failed')
            self.assertEqual(len(failed['solver_samples']),1)
            self.assertEqual(failed['solver_samples'][0]['status'],'FAIL')
            self.assertTrue(any(e['path']=='measurement-000/validation/stdout.txt' for e in failed['evidence']))
            with self.assertRaises(LabError):compare(result,failed,'solver.total_time')

    def test_clock_conversion_roundoff_is_not_an_overlap(self):
        for scale in (1e-9,1,1e9):
            r,v=reports();r.update(setup_seconds=.1*scale,solve_seconds=.2*scale,total_seconds=.3*scale)
            self.assertEqual(self.normalize(r,v)['status'],'PASS')
        r,v=reports();r.update(setup_seconds=.1,solve_seconds=.2,total_seconds=.299)
        with self.assertRaises(LabError):self.normalize(r,v)

    def test_comparison_checks_identical_numerical_contracts_and_coverage(self):
        a,b=comparison_result('a'),comparison_result('b',values=[.5]*7)
        for r in (a,b):
            r['request']['correctness']['sample_validation']=contract()
            r['identity']['artifacts']={sys.executable:'interpreter-hash','check.py':'oracle-hash'}
            r['correctness']['sample_validation_status']='passed'
            r['solver_samples']=[{'status':'PASS'} for _ in r['samples']]
            for sample in r['samples']:sample['metrics'][0]['name']='solver.total_time'
        self.assertEqual(compare(a,b,'solver.total_time')['normalized_cost']['value'],.5)
        b['request']['correctness']['sample_validation']['max_residual']=1e-6
        with self.assertRaises(LabError):compare(a,b,'solver.total_time')
        b['request']['correctness']['sample_validation']=contract()
        b['identity']['artifacts']['check.py']='changed-oracle'
        with self.assertRaises(LabError):compare(a,b,'solver.total_time')
        b['identity']['artifacts']['check.py']='oracle-hash'
        b['solver_samples'].pop()
        with self.assertRaises(LabError):compare(a,b,'solver.total_time')

    def test_batch_tool_cannot_validate_only_final_output(self):
        from cppperflab.lab import plan
        with tempfile.TemporaryDirectory() as d:
            request=request_template(d)
            request['correctness']['sample_validation']=contract()
            probe={'tools':{'hyperfine':{'available':True}}}
            with mock.patch('cppperflab.lab.probe_tools',return_value=probe):
                result=plan(request,Store(Path(d)/'store'))
            self.assertIsNone(result['selected'])
            self.assertIn('per_sample_validation_requires_one_target_execution_per_sample',result['candidates'][0]['reasons'])
