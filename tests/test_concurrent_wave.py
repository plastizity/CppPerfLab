import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from cppperflab.concurrent import run_wave
from cppperflab.errors import LabError
from cppperflab.lifecycle import _record_created
from cppperflab.storage import Store, write_json


class ConcurrentWaveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root)
        self.context = {
            'kind': 'concurrent_ab', 'study_id': 'study-1', 'block_id': 'block-1', 'wave_id': 'wave-1',
            'concurrent_runs': 2, 'competition_pair_identity': {'baseline': {'fingerprint': 'a'}, 'candidate': {'fingerprint': 'b'}},
            'resource_conditions': {
                'concurrent_runs': 2, 'background_load': 'uncontrolled', 'notes': 'control fixture',
                'synchronization_timeout_seconds': 5, 'minimum_overlap_fraction': 0.5,
                'threads_per_variant': {'baseline': 1, 'candidate': 1},
            },
        }
        self.members = [
            {'slot': 0, 'variant': 'baseline', 'request': {'test_duration': .15}},
            {'slot': 1, 'variant': 'candidate', 'request': {'test_duration': .15}},
        ]

    def tearDown(self):
        self.temp.cleanup()

    def fake_run(self, request, store, *, measurement_hook):
        ready_marker = request.get('ready_marker')
        if ready_marker and request.get('role') == 'candidate':
            if not Path(ready_marker).is_file():
                raise RuntimeError('candidate started before baseline reached READY')
        run_id, folder = store.create()
        record = {'schema_version': 1, 'run_id': run_id, 'status': 'running', 'samples': [], 'error': None}
        write_json(folder / 'result.json', record)
        observer = _record_created.get()
        observer('run', run_id)
        if ready_marker and request.get('role') == 'baseline':
            time.sleep(.15)
            Path(ready_marker).write_text('ready')
        measurement_hook.ready(run_id, 0, folder / 'measurement-000')
        time.sleep(request.get('start_delay', 0))
        started = time.monotonic_ns()
        execution = {'pid': __import__('os').getpid(), 'started_monotonic_ns': started,
                     'ended_monotonic_ns': None, 'status': 'completed', 'returncode': 0}
        from cppperflab.execution import _execution_events
        events = _execution_events.get()
        events('start', {'pid': __import__('os').getpid(), 'argv': ['fixture'], 'directory': str(folder),
                         'started_monotonic_ns': started})
        measurement_hook.started(__import__('os').getpid(), started)
        time.sleep(request['test_duration'])
        execution['ended_monotonic_ns'] = time.monotonic_ns()
        events('end', execution)
        measurement_hook.finished(execution)
        record.update(status='completed', execution_context=measurement_hook.context)
        write_json(folder / 'result.json', record)
        return record

    def test_two_workers_share_release_and_record_overlap(self):
        created = []
        with mock.patch('cppperflab.lab.run', side_effect=self.fake_run):
            results, wave = run_wave(self.members, self.store, self.root / 'waves' / 'wave-1', self.context,
                                     lambda *args: created.append(args))
        self.assertEqual(len(created), 2)
        self.assertEqual([r['status'] for r in results], ['completed', 'completed'])
        self.assertEqual(wave['status'], 'PASS')
        self.assertGreaterEqual(wave['overlap_fraction'], .5)
        self.assertEqual(json.loads((self.root / 'waves' / 'wave-1' / 'wave.json').read_text())['status'], 'PASS')
        self.assertEqual({r['execution_context']['status'] for r in results}, {'PASS'})

    def test_second_member_preparation_waits_for_first_ready(self):
        marker = self.root / 'baseline-ready'
        self.members[0]['request'].update(role='baseline', ready_marker=str(marker))
        self.members[1]['request'].update(role='candidate', ready_marker=str(marker))
        with mock.patch('cppperflab.lab.run', side_effect=self.fake_run):
            results, wave = run_wave(self.members, self.store, self.root / 'waves' / 'wave-1', self.context,
                                     lambda *args: None)
        self.assertEqual(wave['status'], 'PASS')
        self.assertTrue(marker.is_file())

    def test_early_failed_run_keeps_category_and_does_not_prepare_peer(self):
        def failed_run(request, store, *, measurement_hook):
            run_id, folder = store.create()
            record = {'schema_version': 1, 'run_id': run_id, 'status': 'correctness_failed',
                      'samples': [], 'error': {'category': 'correctness_failed', 'message': 'oracle rejected'}}
            write_json(folder / 'result.json', record)
            _record_created.get()('run', run_id)
            return record

        created = []
        with mock.patch('cppperflab.lab.run', side_effect=failed_run):
            with self.assertRaises(LabError) as caught:
                run_wave(self.members, self.store, self.root / 'waves' / 'wave-1', self.context,
                         lambda *args: created.append(args))
        self.assertEqual(caught.exception.category, 'correctness_failed')
        self.assertEqual(len(created), 1)
        self.assertEqual(len(self.store.list()), 1)

    def test_low_overlap_is_incomparable_and_persisted(self):
        self.context['resource_conditions']['minimum_overlap_fraction'] = 1
        self.members[0]['request'].update(test_duration=.2, start_delay=0)
        self.members[1]['request'].update(test_duration=.35, start_delay=.15)
        with mock.patch('cppperflab.lab.run', side_effect=self.fake_run):
            with self.assertRaises(LabError) as caught:
                run_wave(self.members, self.store, self.root / 'waves' / 'wave-1', self.context, lambda *args: None)
        self.assertEqual(caught.exception.category, 'incomparable')
        wave = json.loads((self.root / 'waves' / 'wave-1' / 'wave.json').read_text())
        self.assertEqual(wave['status'], 'failed')
        self.assertLess(wave['overlap_fraction'], 1)


if __name__ == '__main__':
    unittest.main()
