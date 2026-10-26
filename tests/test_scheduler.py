"""Synthetic native-record parser controls; never performance success evidence."""
import json
from pathlib import Path
import tempfile
import unittest

from cppperflab.errors import LabError
from cppperflab.scheduler import normalize_switches


CLOCK = dict(name='CLOCK_MONOTONIC', boot_id='test-boot', pid_namespace='pid:[1]',
             time_namespace='time:[1]', monotonic_offset_ns=0,
             before_launch_monotonic_ns=1_000_000_000,
             after_capture_monotonic_ns=10_000_000_000)


def record(timestamp, body, pid=41, tid=41):
    whole, fraction = divmod(timestamp, 1_000_000_000)
    return f' {pid}/{tid} {whole}.{fraction:09d}: {body}\n'


def prepare(root, trace, *, attrs=None):
    launch = root / 'measurement-000'
    (launch / 'sample-attributes').mkdir(parents=True)
    (launch / 'switch-script').mkdir()
    (launch / 'sample-attributes' / 'stdout.txt').write_text(attrs or
        'use_clockid: 1, context_switch: 1, clockid: 1\n')
    (launch / 'sampling-profile.json').write_text(json.dumps({'clock': CLOCK}))
    (launch / 'switch-script' / 'stdout.txt').write_text(trace)
    return launch


class SchedulerTests(unittest.TestCase):
    def test_switch_intervals_are_paired_per_task_and_exit_does_not_close(self):
        trace = ''.join([
            record(0, 'PERF_RECORD_COMM: perf-exec:0/0', 0, 0),
            record(1_100_000_000, 'PERF_RECORD_COMM exec: runner:41/41'),
            record(2_000_000_000, 'PERF_RECORD_SWITCH IN'),
            record(2_500_000_000, 'PERF_RECORD_FORK(41:42):(41:41)'),
            record(2_600_000_000, 'PERF_RECORD_SWITCH IN', 41, 42),
            record(2_700_000_000, 'PERF_RECORD_SWITCH OUT', 41, 42),
            record(3_000_000_000, 'PERF_RECORD_SWITCH OUT preempt'),
            record(4_700_000_000, 'PERF_RECORD_SWITCH IN', 41, 42),
            record(4_800_000_000, 'PERF_RECORD_EXIT(41:42):(41:41)', 41, 42),
            record(5_000_000_000, 'PERF_RECORD_SWITCH IN'),
            record(5_200_000_000, 'PERF_RECORD_EXIT(41:41):(40:40)'),
        ])
        with tempfile.TemporaryDirectory() as tmp:
            launch = prepare(Path(tmp), trace)
            profile = normalize_switches(launch)
            intervals = [json.loads(line) for line in (launch/'scheduler-intervals.jsonl').read_text().splitlines()]
            events = [json.loads(line) for line in (launch/'scheduler-events.jsonl').read_text().splitlines()]
        self.assertEqual(profile['evidence_state'], 'Measured')
        self.assertEqual(profile['switch_event_count'], 6)
        self.assertEqual(len(events), 9)
        self.assertEqual(events[4]['flags'], ['preempted'])
        self.assertEqual([(v['track_id'], v['state'], v['duration_ns']) for v in intervals], [
            ('41:42:0', 'on_cpu', 100_000_000),
            ('41:41:0', 'on_cpu', 1_000_000_000),
            ('41:42:0', 'off_cpu', 2_000_000_000),
            ('41:41:0', 'off_cpu', 2_000_000_000),
        ])
        root_track = next(t for t in profile['tracks'] if t['track_id']=='41:41:0')
        self.assertEqual(root_track['on_cpu_seconds'], 1)
        self.assertEqual(root_track['off_cpu_seconds'], 2)
        self.assertTrue(root_track['right_censored'])
        self.assertFalse(root_track['lifetime_complete'])

    def test_repeated_direction_invalidates_all_derived_intervals(self):
        trace = ''.join([
            record(2_000_000_000, 'PERF_RECORD_SWITCH IN'),
            record(3_000_000_000, 'PERF_RECORD_SWITCH OUT'),
            record(4_000_000_000, 'PERF_RECORD_SWITCH OUT'),
            record(5_000_000_000, 'PERF_RECORD_SWITCH IN'),
        ])
        with tempfile.TemporaryDirectory() as tmp:
            launch = prepare(Path(tmp), trace)
            profile = normalize_switches(launch)
            interval_text = (launch/'scheduler-intervals.jsonl').read_text()
        self.assertEqual(profile['evidence_state'], 'Invalid')
        self.assertEqual(profile['issues'][0]['code'], 'repeated_direction')
        self.assertEqual(interval_text, '')
        self.assertEqual(profile['tracks'][0]['closed_interval_count'], 0)
        self.assertIsNone(profile['tracks'][0]['on_cpu_seconds'])
        self.assertIsNone(profile['tracks'][0]['off_cpu_seconds'])

    def test_lost_records_invalidate_profile_and_preserve_raw_event(self):
        trace = ''.join([
            record(2_000_000_000, 'PERF_RECORD_SWITCH OUT'),
            record(2_500_000_000, 'PERF_RECORD_LOST lost 3'),
            record(3_000_000_000, 'PERF_RECORD_SWITCH IN'),
        ])
        with tempfile.TemporaryDirectory() as tmp:
            launch = prepare(Path(tmp), trace)
            profile = normalize_switches(launch)
            events = (launch/'scheduler-events.jsonl').read_text()
            interval_text = (launch/'scheduler-intervals.jsonl').read_text()
        self.assertEqual(profile['evidence_state'], 'Invalid')
        self.assertEqual(profile['loss_record_count'], 1)
        self.assertIn('"lost_count": 3', events)
        self.assertEqual(interval_text, '')

    def test_no_switch_events_are_missing_and_not_measured_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            launch = prepare(Path(tmp), record(2_000_000_000, 'PERF_RECORD_SWITCH IN'))
            profile = normalize_switches(launch)
        self.assertEqual(profile['evidence_state'], 'Missing')
        self.assertEqual(profile['switch_event_count'], 1)
        self.assertEqual(profile['tracks'][0]['on_cpu_seconds'], None)
        self.assertIn('No closed switch intervals', profile['reason'])


    def test_root_exit_advances_generation_without_a_prior_fork(self):
        trace = ''.join([
            record(2_000_000_000, 'PERF_RECORD_SWITCH OUT'),
            record(2_500_000_000, 'PERF_RECORD_EXIT(41:41):(40:40)'),
            record(3_000_000_000, 'PERF_RECORD_SWITCH IN'),
            record(4_000_000_000, 'PERF_RECORD_SWITCH OUT'),
            record(4_500_000_000, 'PERF_RECORD_EXIT(41:41):(40:40)'),
        ])
        with tempfile.TemporaryDirectory() as tmp:
            launch=prepare(Path(tmp),trace)
            profile=normalize_switches(launch)
            intervals=[json.loads(line) for line in (launch/'scheduler-intervals.jsonl').read_text().splitlines()]
        self.assertEqual([track['track_id'] for track in profile['tracks']],['41:41:0','41:41:1'])
        self.assertEqual(profile['evidence_state'],'Measured')
        self.assertEqual([(item['track_id'],item['state'],item['duration_ns']) for item in intervals],
                         [('41:41:1','on_cpu',1_000_000_000)])
        self.assertIsNone(profile['tracks'][0]['off_cpu_seconds'])
        self.assertTrue(all('_pending_switch' not in track for track in profile['tracks']))

    def test_preempt_flag_is_retained_and_in_only_flag_is_rejected(self):
        trace=''.join([record(2_000_000_000,'PERF_RECORD_SWITCH IN'),
                       record(3_000_000_000,'PERF_RECORD_SWITCH OUT preempt'),
                       record(4_000_000_000,'PERF_RECORD_SWITCH IN')])
        with tempfile.TemporaryDirectory() as tmp:
            launch=prepare(Path(tmp),trace)
            profile=normalize_switches(launch)
            events=[json.loads(line) for line in (launch/'scheduler-events.jsonl').read_text().splitlines()]
        self.assertEqual(profile['evidence_state'],'Measured')
        self.assertEqual(events[1]['flags'],['preempted'])
        self.assertIn('runqueue delay', ' '.join(profile['limitations']))
        with tempfile.TemporaryDirectory() as tmp:
            launch=prepare(Path(tmp),record(2_000_000_000,'PERF_RECORD_SWITCH IN preempt'))
            with self.assertRaises(LabError) as caught:normalize_switches(launch)
        self.assertEqual(caught.exception.category,'parse_failed')

    def test_unverified_context_switch_clock_and_out_of_capture_events_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            launch=prepare(Path(tmp),record(2_000_000_000,'PERF_RECORD_SWITCH IN'),
                           attrs='use_clockid: 1, clockid: 1\n')
            with self.assertRaises(LabError) as caught:
                normalize_switches(launch)
            self.assertEqual(caught.exception.category,'parse_failed')
        with tempfile.TemporaryDirectory() as tmp:
            launch=prepare(Path(tmp),record(11_000_000_000,'PERF_RECORD_SWITCH IN'))
            with self.assertRaises(LabError) as caught:
                normalize_switches(launch)
            self.assertEqual(caught.exception.category,'parse_failed')

    def test_unrecognized_switch_record_is_not_silently_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            launch=prepare(Path(tmp),record(2_000_000_000,'PERF_RECORD_SWITCH_CPU_WIDE OUT'))
            with self.assertRaises(LabError) as caught:
                normalize_switches(launch)
        self.assertEqual(caught.exception.category,'unavailable')


if __name__ == '__main__':
    unittest.main()
