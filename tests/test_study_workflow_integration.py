"""Study integration uses synthetic fixture records, not performance measurements."""
import copy
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

from cppperflab.environment import identity_snapshot
from cppperflab.errors import LabError
from cppperflab.lab import summarize
from cppperflab.storage import Store, write_json
from cppperflab.studies import study, load_study
from cppperflab.workflowphase import current, verify_frozen_request
import test_assessment
import test_cases
from test_core import comparison_result, request_template
from test_paired import spec as paired_spec


def workflow(phase='exploration', sources=(), selected=None):
    value = dict(schema_version=1, phase=phase, source_study_ids=list(sources), selection_reason='fixture')
    if selected is not None:
        value['selected_run_id'] = selected
    return value


class StudyWorkflowIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / 'store')
        self.calls = []
        self.tamper = False
        self.failure = False

    def tearDown(self):
        self.tmp.cleanup()

    def measured(self, request, store):
        rid, directory = store.create()
        self.calls.append(rid)
        ctx = current()
        result = comparison_result(rid, values=[1] * request['measurement']['repetitions'])
        for index, sample in enumerate(result['samples']):
            sample['index'] = index
            sample['metrics'][0]['evidence'] = 'raw'
            for name, value, unit in [('algorithm.operations', 100, 'count'), ('memory.heap_peak_observed', 100, 'bytes')]:
                sample['metrics'].append(dict(name=name, value=value, unit=unit, method='fixture', scope='test', evidence='raw', evidence_state='Measured'))
        result.update(schema_version=1, request=copy.deepcopy(request), identity=identity_snapshot(request),
                      started_at='start', ended_at='end', error=None, summary=summarize(result['samples']),
                      hotspots=[], evidence=[], limitations=[])
        result['tool'] = dict(id=request['measurement']['tool'], version='fixture', mode='counting')
        result['environment']['limitations'] = []
        if ctx is not None:
            now = time.monotonic_ns()
            result['workflow_context'] = {**ctx, 'run_started_monotonic_ns': now}
            result['workflow_cost'] = {'clock': ctx['clock']}
            for index in range(request['measurement']['repetitions']):
                folder = directory / ('measurement-%03d' % index)
                folder.mkdir()
                write_json(folder / 'execution.json', dict(status='completed', returncode=0, started_monotonic_ns=now))
            if self.tamper:
                path = store.record_path(ctx['study_id'], 'study').parent / 'workflow-freeze.json'
                data = json.loads(path.read_text()); data['spec']['name'] = 'tampered'; write_json(path, data)
                self.tamper = False
        if self.failure:
            result.update(status='failed', error=LabError('correctness_failed', 'fixture failure').as_dict())
            result['correctness']['status'] = 'failed'
        write_json(directory / 'result.json', result)
        return result

    def paired(self, phase=None, sources=(), selected=None):
        request = request_template(self.root)
        request['measurement'].update(tool='perf-stat', repetitions=1, warmups=0)
        for variant in ('a', 'b'):
            write_json(self.root / (variant + '.json'), request)
        spec = paired_spec(); spec.update(blocks=2, budget=8)
        if phase:
            spec['workflow'] = workflow(phase, sources, selected)
        path = self.root / 'paired.json'; write_json(path, spec)
        with mock.patch('cppperflab.paired.run', side_effect=self.measured):
            return study(path, self.store)

    def test_legacy_and_phased_pair_then_confirmation(self):
        legacy = self.paired()
        self.assertEqual(legacy['status'], 'completed', legacy['error'])
        self.assertNotIn('workflow', legacy)
        explored = self.paired('exploration')
        self.assertEqual(explored['status'], 'completed', explored['error'])
        self.assertEqual(explored['workflow']['status'], 'PASS')
        chosen = next(r['run_id'] for r in explored['runs'] if r['variant'] == 'candidate')
        confirmation = self.paired('confirmation', [explored['study_id']], chosen)
        self.assertEqual(confirmation['status'], 'completed', confirmation['error'])
        self.assertEqual(confirmation['workflow']['status'], 'PASS')
        self.assertTrue(set(r['run_id'] for r in explored['runs']).isdisjoint(r['run_id'] for r in confirmation['runs']))
        for row in confirmation['runs']:
            self.assertEqual(self.store.read(row['run_id'])['workflow_context']['study_id'], confirmation['study_id'])
        selected = next(r['run_id'] for r in confirmation['runs'] if r['variant'] == 'candidate')
        acceptance = self.paired('acceptance', [confirmation['study_id']], selected)
        self.assertEqual(acceptance['status'], 'completed', acceptance['error'])
        self.assertEqual(acceptance['workflow']['status'], 'PASS')
        self.assertFalse(acceptance['workflow']['optimization_accepted'])
        self.assertEqual(acceptance['workflow']['source_confirmations'][0]['study_id'], confirmation['study_id'])

    def test_selected_non_candidate_is_rejected_before_running(self):
        explored = self.paired('exploration')
        chosen = next(r['run_id'] for r in explored['runs'] if r['variant'] == 'candidate')
        confirmed = self.paired('confirmation', [explored['study_id']], chosen)
        selected = next(r['run_id'] for r in confirmed['runs'] if r['variant'] == 'baseline')
        count = len(self.calls)
        result = self.paired('acceptance', [confirmed['study_id']], selected)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(len(self.calls), count)
        self.assertNotIn('workflow', result)
        selected = next(r['run_id'] for r in explored['runs'] if r['variant'] == 'baseline')
        result = self.paired('confirmation', [explored['study_id']], selected)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(len(self.calls), count)

    def test_calibration_and_frozen_input_rejection(self):
        calibrated = self.paired('calibration')
        self.assertEqual(calibrated['status'], 'completed', calibrated['error'])
        self.assertEqual(calibrated['workflow']['calibration']['status'], 'PASS')
        self.assertFalse(calibrated['workflow']['optimization_accepted'])

    def test_finalize_failure_is_not_success_and_preserves_prior_error(self):
        self.tamper = True
        result = self.paired('exploration')
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['workflow']['status'], 'FAIL')
        self.assertEqual(result['error']['category'], 'storage_failed')
        self.tamper = True; self.failure = True
        result = self.paired('exploration')
        self.assertEqual(result['error']['category'], 'correctness_failed')
        self.assertEqual(result['workflow_error']['category'], 'storage_failed')

    def test_assessment_children_inherit_root_context(self):
        path, store, config = test_assessment.AssessmentTests().prepare(self.root)
        self.store = store
        config['samples'] = ['a']
        config['profiles'] = [config['profiles'][0]]
        config['budget'] = 6
        config['workflow'] = workflow()
        write_json(path, config)
        with mock.patch('cppperflab.paired.run', side_effect=self.measured), mock.patch('cppperflab.cases.run', side_effect=self.measured):
            result = study(path, store)
        self.assertEqual(result['status'], 'completed', result['error'])
        self.assertEqual(result['workflow']['status'], 'PASS')
        for row in result['runs']:
            self.assertEqual(store.read(row['run_id'])['workflow_context']['study_id'], result['study_id'])
        child = store.read(result['jobs'][0]['study_id'], 'study')
        self.assertNotIn('workflow', child)

    def test_cases_freeze_bound_inputs(self):
        path, store, config = test_cases.CaseStudyTests().prepare(self.root)
        self.store = store
        config['workflow'] = workflow()
        write_json(path, config)
        # The fixture adapter is non-batch so execution files match each repetition.
        for variant in ('baseline', 'candidate'):
            path_request = self.root / (variant + '.json')
            request = json.loads(path_request.read_text())
            request['measurement']['tool'] = 'perf-stat'
            write_json(path_request, request)
        with mock.patch('cppperflab.cases.run', side_effect=self.measured):
            result = study(path, store)
        self.assertEqual(result['status'], 'completed', result['error'])
        self.assertEqual(result['workflow']['status'], 'PASS')
        frozen = json.loads((store.record_path(result['study_id'], 'study').parent / 'workflow-freeze.json').read_text())
        self.assertEqual(len(frozen['requests']), 4)
        self.assertNotEqual(frozen['requests'][json.dumps(['a', 'baseline'])]['workload']['inputs'],
                            frozen['requests'][json.dumps(['b', 'baseline'])]['workload']['inputs'])

    def test_growth_freezes_expanded_requests(self):
        request = request_template(self.root)
        request['measurement'].update(tool='perf-stat', repetitions=2, warmups=0)
        write_json(self.root / 'template.json', request)
        config = dict(schema_version=1, name='growth', template='template.json',
            axis=dict(parameter='size', values=[2, 4], argument_index=2),
            variants=[dict(id='base', executable=request['target']['argv'][0], revision='r', artifacts=[])],
            budget=2, seed=1, metric='time.wall', constraints='fixture', workflow=workflow())
        path = self.root / 'growth.json'; write_json(path, config)
        with mock.patch('cppperflab.studies.run', side_effect=self.measured):
            result = study(path, self.store)
        self.assertEqual(result['status'], 'completed', result['error'])
        self.assertEqual(result['workflow']['status'], 'PASS')
        frozen = json.loads((self.store.record_path(result['study_id'], 'study').parent / 'workflow-freeze.json').read_text())
        for scale in (2, 4):
            saved = frozen['requests'][json.dumps(['base', scale])]
            self.assertEqual(saved['target']['argv'][2], str(scale))
            self.assertEqual(saved['workload']['parameters']['size'], scale)

    def test_workflow_validation_rejects_null(self):
        request = request_template(self.root)
        request['measurement'].update(tool='perf-stat', repetitions=1, warmups=0)
        for variant in ('a', 'b'): write_json(self.root / (variant + '.json'), request)
        config = paired_spec(); config['workflow'] = None
        path = self.root / 'bad.json'; write_json(path, config)
        with self.assertRaises(LabError): load_study(path, self.store)
