"""Scheduler timeline trust-boundary and pagination controls."""
import json
from pathlib import Path
import tempfile
import unittest

from cppperflab.cli import dispatch, parser
from cppperflab.errors import LabError
from cppperflab.protocol import sha256
from cppperflab.scheduler_queries import query_scheduler
from cppperflab.storage import Store, inventory, write_json


class SchedulerQueryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temporary.name) / 'store')
        self.run_id, self.directory = self.store.create()
        self.launch = self.directory / 'measurement-000'
        self.launch.mkdir()
        (self.launch/'switch-script').mkdir()
        self.track = dict(track_id='41:42:0', pid=41, tid=42, generation=0,
            switch_event_count=4, first_switch_ns=1_000, last_switch_ns=5_000,
            on_cpu_seconds=2e-6, off_cpu_seconds=2e-6, closed_interval_count=4,
            left_censored=True, right_censored=True, lifetime_complete=False)
        self.intervals = [
            dict(track_id='41:42:0', pid=41, tid=42, generation=0, state='on_cpu',
                start_ns=1_000, end_ns=2_000, duration_ns=1_000,
                start_line=2, end_line=3, evidence_state='Measured'),
            dict(track_id='41:42:0', pid=41, tid=42, generation=0, state='off_cpu',
                start_ns=2_000, end_ns=3_000, duration_ns=1_000,
                start_line=3, end_line=4, evidence_state='Measured'),
            dict(track_id='41:42:0', pid=41, tid=42, generation=0, state='on_cpu',
                start_ns=3_000, end_ns=4_000, duration_ns=1_000,
                start_line=4, end_line=5, evidence_state='Measured'),
            dict(track_id='41:42:0', pid=41, tid=42, generation=0, state='off_cpu',
                start_ns=4_000, end_ns=5_000, duration_ns=1_000,
                start_line=5, end_line=6, evidence_state='Measured'),
        ]
        self._write_record('Measured')

    def tearDown(self):
        self.temporary.cleanup()

    def _write_record(self, state, *, include_timeline=True):
        refs = dict(profile='measurement-000/scheduler-profile.json',
            native='measurement-000/switch-script/stdout.txt', events='measurement-000/events.jsonl',
            intervals='measurement-000/intervals.jsonl')
        local = dict(native='switch-script/stdout.txt', events='events.jsonl', intervals='intervals.jsonl')
        (self.launch/'switch-script/stdout.txt').write_text('native evidence\n')
        (self.launch/'events.jsonl').write_text('{}\n')
        (self.launch / Path(local['intervals']).name).write_text(''.join(
            json.dumps(row, sort_keys=True) + '\n' for row in
            (self.intervals if state == 'Measured' else [])))
        stored_tracks=[dict(self.track)] if state != 'Missing' else []
        if state == 'Invalid':
            stored_tracks[0].update(on_cpu_seconds=None,off_cpu_seconds=None,closed_interval_count=0)
        profile = dict(schema_version=1, source='perf-record-switch', evidence_state=state,
            clock=dict(name='CLOCK_MONOTONIC', boot_id='test', pid_namespace='pid:[1]',
                time_namespace='time:[1]', monotonic_offset_ns=0,
                before_launch_monotonic_ns=1, after_capture_monotonic_ns=9_000),
            native_evidence=local['native'], events_evidence=local['events'],
            intervals_evidence=local['intervals'], switch_event_count=4 if state != 'Missing' else 0,
            loss_record_count=1 if state == 'Invalid' else 0,
            issues=[dict(code='lost_events', line=3, reason='lost')] if state == 'Invalid' else [],
            tracks=stored_tracks,
            limitations=['No wakeup or runqueue causality.'])
        write_json(self.launch / Path(refs['profile']).name, profile)
        record = dict(schema_version=1, run_id=self.run_id, status='completed', error=None,
            correctness=dict(status='passed'), request=dict(measurement={}),
            evidence=inventory(self.directory),
            scheduler_timelines=([dict(profile, execution_stage='measurement', measurement_group=0,
                native_evidence=refs['native'],events_evidence=refs['events'],intervals_evidence=refs['intervals'],
                evidence=refs['profile'])] if include_timeline else []),
            samples=[], hotspots=[], summary=[], phases=[], thread_timelines=[])
        write_json(self.directory / 'result.json', record)
        self.refs = refs

    def test_query_pages_verified_intervals_and_cli_defaults_to_application(self):
        result = query_scheduler(self.store, self.run_id, 'measurement', 0,
                                 track='41:42:0', offset=1, limit=2)
        self.assertEqual(result['evidence_state'], 'Measured')
        self.assertEqual(result['selection_status'], 'PASS')
        self.assertEqual(result['interval_page'],
            dict(offset=1, limit=2, total=4, returned=2, truncated=True))
        self.assertEqual([row['state'] for row in result['intervals']], ['off_cpu', 'on_cpu'])
        args = parser().parse_args(['timeline', self.run_id, '--store', str(self.store.root)])
        self.assertEqual(args.source, 'application')
        scheduler_args=parser().parse_args(['timeline',self.run_id,'--measurement','0','--source','scheduler',
            '--store',str(self.store.root),'--limit','1'])
        cli_result=dispatch(scheduler_args)
        self.assertEqual(cli_result['evidence_state'],'Measured')
        self.assertEqual(cli_result['interval_page']['returned'],1)

    def test_legacy_missing_profile_is_missing_not_zero_or_success(self):
        self._write_record('Measured', include_timeline=False)
        result = query_scheduler(self.store, self.run_id, 'measurement', 0)
        self.assertEqual(result['evidence_state'], 'Missing')
        self.assertEqual(result['selection_status'], 'INCONCLUSIVE')
        self.assertIsNone(result['profile'])
        self.assertEqual(result['tracks'], [])
        self.assertIn('not zero', result['limitations'][0])

    def test_invalid_capture_keeps_state_invalid_and_no_intervals(self):
        self._write_record('Invalid')
        result = query_scheduler(self.store, self.run_id, 'measurement', 0)
        self.assertEqual(result['evidence_state'], 'Invalid')
        self.assertEqual(result['selection_status'], 'INCONCLUSIVE')
        self.assertEqual(result['interval_page']['total'], 0)
        self.assertIsNone(result['tracks'][0]['on_cpu_seconds'])

    def test_modified_interval_evidence_is_rejected(self):
        path = self.launch / Path(self.refs['intervals']).relative_to('measurement-000')
        path.write_text('tampered\n')
        with self.assertRaises(LabError) as caught:
            query_scheduler(self.store, self.run_id, 'measurement', 0)
        self.assertEqual(caught.exception.category, 'storage_failed')

    def test_missing_clock_and_invalid_page_controls_are_rejected(self):
        with self.assertRaises(LabError):
            query_scheduler(self.store, self.run_id, 'preflight', None, offset=-1)
        with self.assertRaises(LabError):
            query_scheduler(self.store, self.run_id, 'measurement', True)


if __name__ == '__main__':
    unittest.main()
