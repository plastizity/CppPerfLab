"""Frozen workflow semantics; fabricated fixtures are not performance evidence."""
import copy
import json
from pathlib import Path
import tempfile
import time
import unittest

from cppperflab.environment import identity_snapshot
from cppperflab.errors import LabError
from cppperflab.storage import Store, write_json
from cppperflab.workflowphase import begin, current, finalize, session, verify_frozen_request


class WorkflowPhaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'store')
        self.counter = 0
        for name in ('binary-a', 'binary-b', 'oracle', 'build'):
            (self.root / name).write_bytes(name.encode())
        self.baseline = self.request('binary-a', 'baseline')
        self.candidate = self.request('binary-b', 'candidate')

    def tearDown(self):
        self.temp.cleanup()

    def identifier(self):
        self.counter += 1
        return '20261027T010000-%012x' % self.counter

    def request(self, executable, variant):
        return {'schema_version': 1, 'name': variant,
            'workload': {'id': 'fixture', 'parameters': {'size': 100}, 'inputs': []},
            'target': {'argv': [str(self.root / executable), '--strategy', variant], 'environment': {},
                       'build': {'revision': 'r', 'compiler': 'fixture', 'flags': [], 'mode': 'release',
                                 'artifacts': [str(self.root / 'build')]}},
            'measurement': {'tool': 'perf-stat', 'repetitions': 1, 'warmups': 0,
                            'timeout_seconds': 3, 'options': {}},
            'correctness': {'argv': [str(self.root / 'oracle')], 'description': 'fixture'}}

    def spec(self, phase, sources=(), selected=None):
        workflow = {'schema_version': 1, 'phase': phase, 'source_study_ids': list(sources),
                    'selection_reason': 'fixture choice'}
        if selected is not None:
            workflow['selected_run_id'] = selected
        return {'schema_version': 1, 'analysis': 'paired', 'name': 'fixture',
                'metric': 'time.wall', 'policy': {'schema_version': 1, 'min_samples': 2,
                    'max_cv': .1, 'max_regression_fraction': .05},
                'resource_conditions': {'concurrent_runs': 1, 'background_load': 'uncontrolled', 'notes': 'fixture'},
                'workflow': workflow}

    def run_record(self, request, workflow, *, started=None):
        rid = self.identifier()
        path = self.store.record_path(rid)
        path.parent.mkdir(parents=True)
        clock = workflow['clock']
        context = {key: workflow[key] for key in ('study_id', 'phase', 'freeze_digest', 'freeze_monotonic_ns', 'clock')}
        context['run_started_monotonic_ns'] = time.monotonic_ns() if started is None else started
        record = {'schema_version': 1, 'run_id': rid, 'status': 'completed', 'request': copy.deepcopy(request),
                  'identity': identity_snapshot(request), 'correctness': {'status': 'passed'},
                  'workflow_context': context, 'workflow_cost': {'clock': clock},
                  'samples': [{'index': 0, 'metrics': []}], 'tool': {'id': 'perf-stat', 'version': 'fixture', 'mode': 'counting'},
                  'environment': {key: 'fixture' for key in ('os','kernel','machine','hostname','cpu_model','cpu_flags','allowed_cpus','governor')}}
        write_json(path, record)
        execution = path.parent / 'measurement-000' / 'execution.json'
        execution.parent.mkdir()
        write_json(execution, {'status': 'completed', 'returncode': 0,
                               'started_monotonic_ns': context['run_started_monotonic_ns']})
        return record

    def complete(self, spec, requests, *, comparison=None):
        sid = self.identifier()
        directory = self.store.record_path(sid, 'study').parent
        directory.mkdir(parents=True)
        workflow = begin(spec, requests, self.store, sid, directory)
        with session(workflow):
            self.assertEqual(current()['freeze_digest'], workflow['freeze_digest'])
            runs = [self.run_record(request, workflow) for request in requests.values()]
        self.assertIsNone(current())
        if comparison is None and spec['workflow']['phase'] in ('confirmation', 'acceptance'):
            comparison = {'metric':spec['metric'], 'metric_scope':spec.get('metric_scope')}
        result = {'schema_version': 1, 'study_id': sid, 'status': 'completed', 'spec': spec,
                  'runs': [{'run_id': r['run_id'], 'variant': variant} for variant, r in zip(requests, runs)],
                  'comparisons': [] if comparison is None else [comparison], 'error': None}
        if comparison is not None:
            comparison.update(baseline_id=runs[0]['run_id'], candidate_id=runs[1]['run_id'])
        result['workflow'] = finalize(workflow, result, self.store)
        write_json(directory / 'study.json', result)
        return result, runs

    def test_unconfigured_study_keeps_parent_context_and_creates_no_freeze(self):
        directory = self.root / 'no-workflow'; directory.mkdir()
        self.assertIsNone(begin({}, {}, self.store, self.identifier(), directory))
        self.assertEqual(list(directory.iterdir()), [])
        with session(None):
            self.assertIsNone(current())

    def test_exploration_freezes_and_verifies_actual_formal_executions(self):
        spec = self.spec('exploration')
        result, runs = self.complete(spec, {'baseline': self.baseline, 'candidate': self.candidate})
        self.assertEqual(result['workflow']['status'], 'PASS')
        self.assertEqual(result['workflow']['run_ids'], [r['run_id'] for r in runs])
        self.assertFalse(result['workflow']['optimization_accepted'])
        freeze = self.store.record_path(result['study_id'], 'study').parent / 'workflow-freeze.json'
        data = json.loads(freeze.read_text())
        self.assertEqual(data['spec'], spec)
        self.assertEqual(data['freeze_digest'], result['workflow']['freeze_digest'])

    def test_preexecution_request_check_uses_supplied_identity_without_hashing(self):
        spec = self.spec('exploration')
        sid = self.identifier(); directory = self.store.record_path(sid, 'study').parent; directory.mkdir(parents=True)
        record = begin(spec, {'baseline': self.baseline, 'candidate': self.candidate}, self.store, sid, directory)
        identity = identity_snapshot(self.candidate)
        named = copy.deepcopy(self.candidate); named['name'] = 'dynamic presentation name'
        from unittest import mock
        with session(record), mock.patch('cppperflab.workflowphase.identity_snapshot', side_effect=AssertionError('unexpected hash')):
            verify_frozen_request(named, identity, self.store)
            named['target']['argv'][-1] = 'changed'
            with self.assertRaises(LabError):
                verify_frozen_request(named, identity, self.store)
            with self.assertRaises(LabError):
                verify_frozen_request(self.candidate, {'artifacts': {}}, self.store)
        verify_frozen_request({}, {}, self.store)

    def test_confirmation_requires_selected_exploration_candidate_identity(self):
        explored, runs = self.complete(self.spec('exploration'), {'baseline': self.baseline, 'candidate': self.candidate})
        selected = runs[1]['run_id']
        spec = self.spec('confirmation', [explored['study_id']], selected)
        result, new_runs = self.complete(spec, {'baseline': self.baseline, 'candidate': self.candidate})
        self.assertEqual(result['workflow']['status'], 'PASS')
        self.assertTrue(set(r['run_id'] for r in runs).isdisjoint(r['run_id'] for r in new_runs))
        self.assertEqual(result['workflow']['selected_run_id'], selected)
        invalid = copy.deepcopy(self.candidate)
        invalid['target']['argv'][0] = self.baseline['target']['argv'][0]
        sid = self.identifier(); directory = self.store.record_path(sid, 'study').parent; directory.mkdir(parents=True)
        with self.assertRaises(LabError) as error:
            begin(spec, {'baseline': self.baseline, 'candidate': invalid}, self.store, sid, directory)
        self.assertEqual(error.exception.category, 'incomparable')
        self.assertFalse((directory/'workflow-freeze.json').exists())

    def test_paired_confirmation_rejects_same_binary_with_changed_strategy_argv(self):
        explored, runs = self.complete(self.spec('exploration'), {'baseline': self.baseline, 'candidate': self.candidate})
        spec = self.spec('confirmation', [explored['study_id']], runs[1]['run_id'])
        changed = copy.deepcopy(self.candidate); changed['target']['argv'][-1] = 'different strategy'
        sid = self.identifier(); directory = self.store.record_path(sid, 'study').parent; directory.mkdir(parents=True)
        with self.assertRaises(LabError) as error:
            begin(spec, {'baseline': self.baseline, 'candidate': changed}, self.store, sid, directory)
        self.assertEqual(error.exception.category, 'incomparable')

    def test_reused_source_run_and_tampered_freeze_are_rejected(self):
        explored, runs = self.complete(self.spec('exploration'), {'baseline': self.baseline, 'candidate': self.candidate})
        spec = self.spec('confirmation', [explored['study_id']], runs[1]['run_id'])
        sid = self.identifier(); directory = self.store.record_path(sid, 'study').parent; directory.mkdir(parents=True)
        workflow = begin(spec, {'baseline': self.baseline, 'candidate': self.candidate}, self.store, sid, directory)
        result = {'schema_version': 1, 'study_id': sid, 'status': 'completed', 'spec': spec,
                  'runs': [{'run_id': runs[1]['run_id']}], 'comparisons': [], 'error': None}
        with self.assertRaises(LabError) as error:
            finalize(workflow, result, self.store)
        self.assertEqual(error.exception.category, 'incomparable')
        path = directory/'workflow-freeze.json'; frozen = json.loads(path.read_text()); frozen['spec']['policy']['max_cv'] = 1
        write_json(path, frozen)
        with self.assertRaises(LabError) as error:
            finalize(workflow, result, self.store)
        self.assertEqual(error.exception.category, 'storage_failed')

    def test_new_run_id_cannot_hide_pre_freeze_measurement(self):
        spec=self.spec('exploration');sid=self.identifier()
        directory=self.store.record_path(sid,'study').parent;directory.mkdir(parents=True)
        workflow=begin(spec,{'baseline':self.baseline,'candidate':self.candidate},self.store,sid,directory)
        baseline=self.run_record(self.baseline,workflow)
        candidate=self.run_record(self.candidate,workflow)
        execution=self.store.record_path(candidate['run_id']).parent/'measurement-000'/'execution.json'
        raw=json.loads(execution.read_text());raw['started_monotonic_ns']=workflow['freeze_monotonic_ns']-1
        write_json(execution,raw)
        result={'study_id':sid,'status':'completed','spec':spec,'comparisons':[],
                'runs':[{'run_id':baseline['run_id']},{'run_id':candidate['run_id']}]}
        with self.assertRaises(LabError) as error:
            finalize(workflow,result,self.store)
        self.assertEqual(error.exception.category,'incomparable')
        self.assertIn('after the freeze',str(error.exception))

    def test_new_comparison_cannot_reference_source_phase_sample(self):
        explored,old=self.complete(self.spec('exploration'),{'baseline':self.baseline,'candidate':self.candidate})
        spec=self.spec('confirmation',[explored['study_id']],old[1]['run_id']);sid=self.identifier()
        directory=self.store.record_path(sid,'study').parent;directory.mkdir(parents=True)
        workflow=begin(spec,{'baseline':self.baseline,'candidate':self.candidate},self.store,sid,directory)
        baseline=self.run_record(self.baseline,workflow);candidate=self.run_record(self.candidate,workflow)
        comparison={'baseline_id':baseline['run_id'],'candidate_id':candidate['run_id'],
            'block_pairs':[{'members':{'baseline':[{'run_id':baseline['run_id'],'sample_index':0}],
                                       'candidate':[{'run_id':old[1]['run_id'],'sample_index':0}]}}]}
        result={'study_id':sid,'status':'completed','spec':spec,'comparisons':[comparison],
                'runs':[{'run_id':baseline['run_id']},{'run_id':candidate['run_id']}]}
        with self.assertRaises(LabError) as error:
            finalize(workflow,result,self.store)
        self.assertEqual(error.exception.category,'incomparable')
        self.assertIn('sample outside',str(error.exception))

    def test_completed_confirmation_without_comparison_is_not_passed(self):
        explored,old=self.complete(self.spec('exploration'),{'baseline':self.baseline,'candidate':self.candidate})
        spec=self.spec('confirmation',[explored['study_id']],old[1]['run_id']);sid=self.identifier()
        directory=self.store.record_path(sid,'study').parent;directory.mkdir(parents=True)
        workflow=begin(spec,{'baseline':self.baseline,'candidate':self.candidate},self.store,sid,directory)
        runs=[self.run_record(self.baseline,workflow),self.run_record(self.candidate,workflow)]
        result={'study_id':sid,'status':'completed','spec':spec,'comparisons':[],
                'runs':[{'run_id':r['run_id']} for r in runs]}
        with self.assertRaises(LabError) as error:
            finalize(workflow,result,self.store)
        self.assertEqual(error.exception.category,'incomparable')
        self.assertEqual(workflow['status'],'FAIL')

    def test_acceptance_must_select_confirmed_candidate_not_baseline(self):
        explored,old=self.complete(self.spec('exploration'),{'baseline':self.baseline,'candidate':self.candidate})
        confirm,runs=self.complete(self.spec('confirmation',[explored['study_id']],old[1]['run_id']),
                                   {'baseline':self.baseline,'candidate':self.candidate})
        spec=self.spec('acceptance',[confirm['study_id']],runs[0]['run_id'])
        sid=self.identifier();directory=self.store.record_path(sid,'study').parent;directory.mkdir(parents=True)
        with self.assertRaises(LabError) as error:
            begin(spec,{'baseline':self.baseline,'candidate':self.candidate},self.store,sid,directory)
        self.assertEqual(error.exception.category,'invalid_request')
        self.assertFalse((directory/'workflow-freeze.json').exists())

    def test_calibration_reuses_existing_interval_and_exposes_nonpassing_outcomes(self):
        spec = self.spec('calibration')
        controls = {'baseline': self.baseline, 'candidate': copy.deepcopy(self.baseline)}
        comparison = {'metric':'time.wall','metric_scope':None,'bootstrap_95_percent_speedup':[.99, 1.01],
                      'sufficient_samples':True,'stable_samples':True,'comparison_id':self.identifier()}
        result, _ = self.complete(spec, controls, comparison=comparison)
        self.assertEqual(result['workflow']['calibration']['status'], 'PASS')
        self.assertEqual(result['workflow']['calibration']['ratio_interval_95_percent'], [.99, 1.01])
        # An A/A control with a clear discrepancy is recorded, never corrected away.
        comparison = {'metric':'time.wall','metric_scope':None,'bootstrap_95_percent_speedup':[1.10, 1.15],
                      'sufficient_samples':True,'stable_samples':True,'comparison_id':self.identifier()}
        bad, _ = self.complete(spec, controls, comparison=comparison)
        self.assertEqual(bad['workflow']['calibration']['status'], 'FAIL')
        with self.assertRaises(LabError):
            sid=self.identifier(); directory=self.store.record_path(sid,'study').parent;directory.mkdir(parents=True)
            begin(spec, {'baseline':self.baseline,'candidate':self.candidate},self.store,sid,directory)

    def test_failure_retains_original_study_outcome_without_successful_subset(self):
        spec = self.spec('exploration'); sid=self.identifier()
        directory=self.store.record_path(sid,'study').parent;directory.mkdir(parents=True)
        workflow=begin(spec,{'baseline':self.baseline,'candidate':self.candidate},self.store,sid,directory)
        result={'schema_version':1,'study_id':sid,'status':'failed','spec':spec,'runs':[],'comparisons':[],
                'error':{'category':'correctness_failed','message':'bad output','exit_code':5}}
        updated=finalize(workflow,result,self.store)
        self.assertEqual(updated['status'],'INCONCLUSIVE')
        self.assertEqual(result['error']['category'],'correctness_failed')
        self.assertIsNone(updated['calibration'])

    def test_confirmation_records_matching_aa_without_claiming_independence(self):
        controls={'baseline':self.baseline,'candidate':copy.deepcopy(self.baseline)}
        aa={'metric':'time.wall','metric_scope':None,'bootstrap_95_percent_speedup':[.99,1.01],
            'sufficient_samples':True,'stable_samples':True,'comparison_id':self.identifier()}
        calibration,_=self.complete(self.spec('calibration'),controls,comparison=aa)
        exploration,runs=self.complete(self.spec('exploration'),{'baseline':self.baseline,'candidate':self.candidate})
        spec=self.spec('confirmation',[exploration['study_id'],calibration['study_id']],runs[1]['run_id'])
        confirmation,_=self.complete(spec,{'baseline':self.baseline,'candidate':self.candidate})
        matched=confirmation['workflow']['calibrations'][0]
        self.assertEqual(matched['status'],'PASS')
        self.assertTrue(matched['applicable'])
        self.assertEqual(matched['matched_request_keys'],['baseline'])
        self.assertIn('independence remain unproved',matched['reason'])
        self.assertEqual(matched['source_ended_at'],calibration['workflow']['ended_at'])

    def test_bad_aa_is_not_claimed_as_applicable(self):
        controls={'baseline':self.baseline,'candidate':copy.deepcopy(self.baseline)}
        aa={'metric':'time.wall','metric_scope':None,'bootstrap_95_percent_speedup':[1.1,1.2],
            'sufficient_samples':True,'stable_samples':True,'comparison_id':self.identifier()}
        calibration,_=self.complete(self.spec('calibration'),controls,comparison=aa)
        exploration,runs=self.complete(self.spec('exploration'),{'baseline':self.baseline,'candidate':self.candidate})
        spec=self.spec('confirmation',[exploration['study_id'],calibration['study_id']],runs[1]['run_id'])
        confirmation,_=self.complete(spec,{'baseline':self.baseline,'candidate':self.candidate})
        recorded=confirmation['workflow']['calibrations'][0]
        self.assertEqual(recorded['status'],'FAIL')
        self.assertFalse(recorded['applicable'])

    def test_acceptance_preserves_source_confirmation_decisions(self):
        exploration,explored=self.complete(self.spec('exploration'),{'baseline':self.baseline,'candidate':self.candidate})
        spec=self.spec('confirmation',[exploration['study_id']],explored[1]['run_id'])
        comparison={'comparison_id':self.identifier(),'metric':'time.wall','metric_scope':None,
            'classification':'no_clear_change','acceptance':{'status':'PASS','reason':'declared gate passed'},
            'claim_acceptance':{'status':'PASS','reason':'conditions satisfied'},
            'validity':{'status':'PASS'},
            'decisions':{'improvement':{'status':'NOT_ESTABLISHED','reason':'interval includes no change'}}}
        confirmation,runs=self.complete(spec,{'baseline':self.baseline,'candidate':self.candidate},comparison=comparison)
        acceptance,_=self.complete(self.spec('acceptance',[confirmation['study_id']],runs[1]['run_id']),
                                   {'baseline':self.baseline,'candidate':self.candidate})
        source=acceptance['workflow']['source_confirmations'][0]
        self.assertEqual(source['study_id'],confirmation['study_id'])
        self.assertEqual(source['comparisons'][0]['validity']['status'],'PASS')
        self.assertEqual(source['comparisons'][0]['claim_acceptance']['status'],'PASS')
        self.assertEqual(source['comparisons'][0]['improvement']['status'],'NOT_ESTABLISHED')
        self.assertFalse(acceptance['workflow']['optimization_accepted'])

    def test_confirmation_case_matrix_freezes_one_candidate_variant(self):
        exploration,runs=self.complete(self.spec('exploration'),{'baseline':self.baseline,'candidate':self.candidate})
        spec=self.spec('confirmation',[exploration['study_id']],runs[1]['run_id'])
        spec['analysis']='cases'
        spec['comparisons']=[{'candidate':'candidate-1'},{'candidate':'candidate-2'}]
        requests={json.dumps(['sample','baseline']):self.baseline,
                  json.dumps(['sample','candidate-1']):self.candidate,
                  json.dumps(['sample','candidate-2']):self.candidate}
        sid=self.identifier();directory=self.store.record_path(sid,'study').parent;directory.mkdir(parents=True)
        with self.assertRaises(LabError) as error:
            begin(spec,requests,self.store,sid,directory)
        self.assertEqual(error.exception.category,'invalid_request')
        self.assertIn('exactly one candidate',str(error.exception))


if __name__ == '__main__':
    unittest.main()
