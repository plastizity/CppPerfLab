"""Budget is non-executing; passing non-regression is not proof of improvement."""
import unittest
from unittest import mock

from cppperflab.budget import execution_budget
from cppperflab.comparison import compare, decisions
from cppperflab.errors import LabError
from test_core import comparison_result, request_template


class BudgetTests(unittest.TestCase):
    def request(self,tool='perf-stat'):
        r=request_template('/tmp');r['measurement'].update(tool=tool,repetitions=9,warmups=2,timeout_seconds=30)
        return r

    def candidate(self,r):return execution_budget(r)['candidates'][0]

    def test_no_process_or_probe_is_started(self):
        with mock.patch('cppperflab.execution.execute',side_effect=AssertionError('executed')),mock.patch('cppperflab.lab.probe_tools',side_effect=AssertionError('probed')):
            result=execution_budget(self.request())
        self.assertFalse(result['execution_performed'])
        c=result['candidates'][0]
        self.assertEqual(c['known_target_computation_runs'],12)
        self.assertEqual(c['main_execution_groups'],13)
        self.assertEqual(c['configured_timeout_budget_seconds'],390)
        self.assertIsNone(result['estimated_runtime_seconds'])

    def test_hyperfine_managed_launches_count_warmup(self):
        c=self.candidate(self.request('hyperfine'))
        self.assertEqual(c['main_execution_groups'],3) # oracle, preflight, batch
        self.assertEqual(c['known_target_computation_runs'],12)
        self.assertEqual(c['stages'][3]['commands'],0)
        self.assertEqual(c['stages'][3]['target_computation_runs'],2)

    def test_adaptive_iterations_are_unknown(self):
        r=self.request('google-benchmark');r['measurement'].update(warmups=0,options={'filter':'Case'})
        c=self.candidate(r)
        self.assertEqual(c['stages'][0]['target_registration_listings'],1)
        self.assertIsNone(c['known_target_computation_runs'])
        self.assertIsNone(c['stages'][4]['function_invocations'])
        self.assertEqual(c['main_execution_groups'],2)

    def test_validators_and_batch_restriction(self):
        r=self.request();r['correctness']['sample_validation']={}
        c=self.candidate(r)
        self.assertEqual(c['main_execution_groups'],22)
        r['measurement']['tool']='hyperfine'
        self.assertFalse(self.candidate(r)['eligible'])

    def test_auto_is_not_a_selection_or_availability_claim(self):
        r=self.request();r['measurement']['tool']='auto'
        result=execution_budget(r)
        self.assertIsNone(result['selected_tool'])
        self.assertTrue(any(c['eligible'] for c in result['candidates']))
        self.assertTrue(all(c.get('availability')=='not_probed' for c in result['candidates'] if c['eligible']))

    def test_unknown_tool_and_bad_options_rejected(self):
        for tool in ('imaginary','perf-stat'):
            r=self.request(tool);r['measurement']['options']={'unknown':1}
            with self.assertRaises(LabError):execution_budget(r)

    def test_postprocessing_and_metadata_are_not_hidden(self):
        for tool,expected in [('perf-record',9),('massif',None),('perf-stat',0)]:
            c=self.candidate(self.request(tool))
            stages={s['stage']:s for s in c['stages']}
            self.assertEqual(stages['postprocessing']['commands'],expected)
            self.assertEqual(stages['binary_metadata']['maximum_commands'],1)

    def test_profiled_precheck_reuses_preflight_instead_of_adding_a_target_launch(self):
        plain=self.request('perf-record')
        sampled=self.request('perf-record')
        sampled['measurement']['options']={'sample_trace':True,'call_graph':'fp','symbolizer':'perf',
            'precheck':dict(min_samples=20,min_resolved_self_fraction=.8,min_resolved_stack_fraction=.5,require_phase_clock=True)}
        without=self.candidate(plain);with_sampling=self.candidate(sampled)
        self.assertEqual(with_sampling['main_execution_groups'],without['main_execution_groups'])
        self.assertEqual(with_sampling['known_target_computation_runs'],without['known_target_computation_runs'])
        self.assertEqual(with_sampling['sampling']['profiled_launches'],sampled['measurement']['repetitions']+1)
        self.assertEqual(with_sampling['sampling']['additional_target_launches'],0)
        sampled['measurement']['options']['switch_events']=True
        with_switches=self.candidate(sampled)
        self.assertEqual(with_switches['known_target_computation_runs'],without['known_target_computation_runs'])
        self.assertEqual(with_switches['sampling']['known_perf_postprocessing_commands'],
                         4*(sampled['measurement']['repetitions']+1))



class DecisionTests(unittest.TestCase):
    def result(self,value=1,policy=None):
        return compare(comparison_result('a'),comparison_result('b',values=[value]*7),policy=policy)

    def test_pass_without_change_is_not_acceleration(self):
        r=self.result()
        self.assertEqual(r['acceptance']['status'],'PASS')
        self.assertEqual(r['classification'],'no_clear_change')
        d=r['decisions']
        self.assertEqual(d['correctness']['status'],'PASS')
        self.assertEqual(d['non_regression']['status'],'PASS')
        self.assertEqual(d['improvement']['status'],'NOT_ESTABLISHED')
        self.assertFalse(d['optimization_accepted'])

    def test_target_failure_can_still_pass_regression_gate(self):
        policy=dict(schema_version=1,min_samples=7,max_cv=.1,max_regression_fraction=.05,min_speedup=2)
        d=self.result(.8,policy)['decisions']
        self.assertEqual(d['performance_target']['status'],'FAIL')
        self.assertEqual(d['non_regression']['status'],'PASS')
        self.assertEqual(d['improvement']['status'],'SUPPORTED_IMPROVEMENT')

    def test_higher_direction_is_handled(self):
        policy=dict(schema_version=1,min_samples=7,max_cv=.1,max_regression_fraction=.05,direction='higher')
        self.assertEqual(self.result(2,policy)['decisions']['improvement']['status'],'SUPPORTED_IMPROVEMENT')
        self.assertEqual(self.result(.5,policy)['decisions']['improvement']['status'],'SUPPORTED_REGRESSION')

    def test_noise_and_legacy_do_not_acquire_certainty(self):
        r=compare(comparison_result('a',values=[.5,1,2]*3),comparison_result('b'))
        self.assertEqual(r['decisions']['statistics']['status'],'INCONCLUSIVE')
        self.assertEqual(r['decisions']['improvement']['status'],'NOT_ESTABLISHED')
        del r['correctness_status']
        self.assertEqual(decisions(r)['correctness']['status'],'INCONCLUSIVE')

    def test_rejected_correctness_and_diagnostic(self):
        r=dict(metric='time.wall',comparable=False,correctness_status='failed')
        self.assertEqual(decisions(r)['correctness']['status'],'FAIL')
        r=self.result();r['metric']='cpu.ipc'
        self.assertEqual(decisions(r)['improvement']['status'],'NOT_APPLICABLE')
