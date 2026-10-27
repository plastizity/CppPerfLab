"""Compact report semantics: no raw samples, hidden regressions, or inferred PASS."""
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from cppperflab.agent_report import build_agent_report
from cppperflab.comparison import compare
from cppperflab.errors import LabError
from cppperflab.views import base_view, run_view
from test_views import BASELINE, CANDIDATE, run_result


class AgentReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.baseline = run_result(BASELINE, self.path / 'baseline', [1.0]*7)
        self.candidate = run_result(CANDIDATE, self.path / 'candidate', [1.0]*7)
        self.view = run_view(self.baseline)
        self.view['record_id'] = BASELINE
        self.view['source_records'] = []

    def test_run_has_no_invented_assessment_and_omits_raw_samples(self):
        report = build_agent_report(self.view, self.path)
        self.assertIsNone(report['assessment'])
        self.assertEqual(report['subject']['correctness']['status'], 'passed')
        self.assertFalse(report['optimization_accepted'])
        self.assertTrue(report['metrics']['items'])
        self.assertNotIn('samples', json.dumps(report))
        self.assertEqual(report['metrics']['items'][0]['evidence_ids'], [BASELINE + ':raw.txt'])
        self.assertEqual(report['follow_up'][0]['argv'][-3:], ['--store', str(self.path), '--json'])

    def test_late_regression_and_incomparable_are_not_hidden_by_limit(self):
        ordinary = compare(self.baseline, self.candidate)
        self.assertEqual(ordinary['acceptance']['status'], 'PASS')
        self.assertEqual(ordinary['decisions']['improvement']['status'], 'NOT_ESTABLISHED')
        worse = deepcopy(self.candidate)
        for sample in worse['samples']:
            sample['metrics'][0]['value'] = 2.0
        regression = compare(self.baseline, worse)
        self.assertEqual(regression['classification'], 'observed_regression')
        broken = {'comparable': False, 'metric': 'time.wall', 'baseline_id': BASELINE,
                  'candidate_id': CANDIDATE, 'comparison_id': 'bad',
                  'error': {'category':'incomparable', 'message':'scope mismatch', 'exit_code':7},
                  'acceptance': {'status':'INCONCLUSIVE', 'reason':'scope mismatch'}}
        self.view['comparisons'] = [dict(ordinary, comparison_id=str(i)) for i in range(3)] + [dict(regression, comparison_id='late'), broken]
        report = build_agent_report(self.view, self.path, 1)
        comparisons = report['comparisons']
        self.assertEqual((comparisons['total'], comparisons['returned'], comparisons['omitted']), (5, 3, 2))
        items = {item['comparison_id']: item for item in comparisons['items']}
        self.assertIn('late', items)
        self.assertIn('bad', items)
        self.assertEqual(items['0']['acceptance']['status'], 'PASS')
        self.assertEqual(items['0']['decisions']['improvement']['status'], 'NOT_ESTABLISHED')
        self.assertNotIn('baseline_samples', items['0'])
        self.assertEqual(items['0']['sample_counts'], {'baseline':7, 'candidate':7})
        self.assertEqual(items['bad']['error']['exit_code'], 7)

    def test_states_quality_and_failed_source_remain_visible(self):
        measured = self.view['metrics'][0]
        modeled = deepcopy(measured)
        modeled['summary'].update(name='cpu.cache_misses', evidence_state='Modeled', median=123)
        self.view['metrics'] = [measured, modeled]
        self.view['observations'] = [{'name':'cpu.ipc','evidence_state':'Unsupported','reason':'event denied'},
                                     {'name':'cpu.ipc','evidence_state':'Unsupported','reason':'event denied'}]
        self.view['quality'] += [{'code':'missing_counter','severity':'review','message':'event denied'}]
        self.view['source_records'] = [{'run_id': CANDIDATE, 'status':'failed',
            'error':{'category':'correctness_failed','message':'wrong output','exit_code':5}, 'correctness_status':'failed'}]
        report=build_agent_report(self.view, self.path, 5)
        self.assertEqual({m['evidence_state'] for m in report['metrics']['items']}, {'Measured', 'Modeled'})
        unavailable=report['attention']['unavailable_observations'][0]
        self.assertEqual((unavailable['evidence_state'], unavailable['count']), ('Unsupported',2))
        self.assertEqual(report['attention']['sources_requiring_review'][0]['error']['exit_code'],5)
        self.assertIn('missing_counter', {q['code'] for q in report['attention']['quality']})

    def test_failed_source_retains_withdrawal_and_supersession(self):
        self.view['source_records']=[{'run_id': CANDIDATE, 'status':'failed',
            'error':{'category':'correctness_failed','message':'wrong output','exit_code':5},
            'correctness_status':'failed'}]
        self.view['conditions']['source_candidates']=[{'run_id':CANDIDATE,'candidate_state':'withdrawn',
            'superseded_by':BASELINE,'revision':2}]
        sources=build_agent_report(self.view,self.path)['attention']['sources_requiring_review']
        self.assertEqual(len(sources),1)
        self.assertEqual(sources[0]['error']['exit_code'],5)
        self.assertEqual(sources[0]['candidate_state'],'withdrawn')
        self.assertEqual(sources[0]['superseded_by'],BASELINE)
        self.assertEqual(sources[0]['revision'],2)

    def test_single_concurrent_run_preserves_context_without_inventing_overlap(self):
        self.view['conditions']['execution_context']={'kind':'concurrent_ab','concurrent_runs':2,
            'status':'PASS','overlap_fraction':0.8,'overlap':{'evidence_state':'Measured'},
            'resource_conditions':{'threads_per_variant':{'baseline':1,'candidate':1}},
            'competition_pair_identity':{'baseline':'full fingerprint'}}
        subject=build_agent_report(self.view,self.path)['subject']
        self.assertEqual(subject['execution_context']['kind'],'concurrent_ab')
        self.assertEqual(subject['execution_context']['overlap_fraction'],0.8)
        self.assertEqual(subject['execution_context']['overlap']['evidence_state'],'Measured')
        self.assertNotIn('competition_pair_identity',subject['execution_context'])
        self.assertIn('not establish isolated serial',subject['execution_context_note'])
        del self.view['conditions']['execution_context']['overlap']
        del self.view['conditions']['execution_context']['overlap_fraction']
        self.assertNotIn('overlap',build_agent_report(self.view,self.path)['subject']['execution_context'])
        del self.view['conditions']['execution_context']
        self.assertNotIn('execution_context',build_agent_report(self.view,self.path)['subject'])

    def test_study_source_conditions_include_execution_context(self):
        self.view['view_kind']='study'
        self.view['source_records']=[{'run_id':BASELINE,'status':'completed',
            'execution_context':{'kind':'concurrent_ab','status':'PASS','overlap_fraction':0.8}}]
        subject=build_agent_report(self.view,self.path)['subject']
        self.assertEqual(subject['source_conditions']['items'][0]['execution_context']['overlap_fraction'],0.8)

    def test_assessment_is_existing_conclusion_and_every_case_remains(self):
        self.view['summary']['assessment']={'schema_version':1,'correctness':'PASS','acceptance':'FAIL',
            'end_to_end_improvement':'SUPPORTED_IMPROVEMENT','optimization_accepted':False,
            'cases':[{'case_id':'fast','correctness':'PASS','acceptance':'PASS','end_to_end_improvement':'SUPPORTED_IMPROVEMENT'},
                     {'case_id':'regressed','correctness':'PASS','acceptance':'FAIL','end_to_end_improvement':'SUPPORTED_REGRESSION'}],
            'limitations':['Recorded gates only']}
        report=build_agent_report(self.view,self.path,1)
        self.assertEqual(report['assessment']['acceptance'],'FAIL')
        self.assertEqual(report['assessment']['end_to_end_improvement'],'SUPPORTED_IMPROVEMENT')
        self.assertEqual(report['assessment']['cases'][1]['case_id'],'regressed')

    def test_legacy_comparison_reuses_decisions_from_shared_finding(self):
        result=compare(self.baseline,self.candidate)
        legacy=deepcopy(result)
        legacy.pop('decisions')
        legacy.pop('normalized_cost')
        self.view['comparisons']=[legacy]
        self.view['summary']['findings']=[{'metric':result['metric'], 'baseline_id':result['baseline_id'],
            'candidate_id':result['candidate_id'], 'decisions':result['decisions'],
            'normalized_cost':result['normalized_cost']}]
        row=build_agent_report(self.view,self.path)['comparisons']['items'][0]
        self.assertEqual(row['decisions']['improvement']['status'],result['decisions']['improvement']['status'])
        self.assertEqual(row['normalized_cost'],result['normalized_cost'])

    def test_source_conditions_are_deduplicated_and_queryable_when_truncated(self):
        self.view['view_kind']='study'
        conditions={'workload':{'id':'case','parameters':{'n':10}},
                    'tool':{'id':'perf-stat','version':'1','mode':'counting'},
                    'measurement':{'tool':'perf-stat','repetitions':1,'warmups':0},
                    'build':{'revision':'rev-a','compiler':'gcc','flags':[],'mode':'Release'}}
        self.view['source_records']=[{'run_id':BASELINE,'status':'completed',**conditions},
            {'run_id':CANDIDATE,'status':'completed',**deepcopy(conditions)},
            {'run_id':'20261014T120000-000000000003','status':'completed',**{**deepcopy(conditions),
             'workload':{'id':'case','parameters':{'n':20}}}}]
        report=build_agent_report(self.view,self.path,1)
        source=report['subject']['source_conditions']
        self.assertEqual((source['total'],source['returned'],source['omitted']),(2,1,1))
        self.assertEqual(source['items'][0]['run_count'],2)
        self.assertIn('all', {item['section'] for item in report['follow_up']})
        self.assertTrue(any(BASELINE in item['argv'] and item['section']=='conditions' for item in report['follow_up']))

    def test_invalid_limit_is_rejected(self):
        for limit in (0, 201, True, 1.5):
            with self.subTest(limit=limit), self.assertRaises(LabError):
                build_agent_report(self.view,self.path,limit)

    def test_schema_has_the_projection_contract(self):
        schema=json.loads((Path(__file__).parents[1]/'src/cppperflab/schemas/agent-report.schema.json').read_text())
        report=build_agent_report(self.view,self.path)
        self.assertLessEqual(set(schema['required']),set(report))
        self.assertLessEqual(set(report),set(schema['properties']))
        for field in ('validity','workflow','execution_state_evidence'):
            self.assertIn(field,schema['properties'])
            self.assertNotIn(field,schema['required'])  # Older version-1 exports remain valid.
        self.assertEqual(schema['properties']['schema_version']['const'],report['schema_version'])
        self.assertEqual(schema['properties']['report_type']['const'],report['report_type'])


if __name__ == '__main__':
    unittest.main()
