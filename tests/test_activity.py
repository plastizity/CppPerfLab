import unittest

from cppperflab.activity import analyze
from cppperflab.errors import LabError


def event(event_id, track='t', start=0, end=100, parent=None, activity=None,
          correctness='passed', path=None):
    row = dict(id=event_id, track_id=track, start_ns=start, end_ns=end,
               parent_id=parent, correctness=correctness,
               path=path or [event_id])
    if activity is not None:
        row['activity'] = activity
    return row


class ActivityTests(unittest.TestCase):
    def test_unions_nested_annotations_and_keeps_tracks_separate(self):
        events = [
            event('root', start=0, end=100),
            event('work', start=10, end=70, parent='root',
                  activity={'kind': 'application_work'}),
            event('nested', start=20, end=50, parent='work',
                  activity={'kind': 'application_work'}),
            event('wait', start=70, end=90, parent='root',
                  activity={'kind': 'application_wait', 'reason': 'queue'}),
            event('other-root', track='u', start=0, end=100),
        ]
        result = analyze(events, 'solver', None, 'raw.phases.json')
        by_track = {row['track_id']: row for row in result['timeline']['tracks']}
        self.assertAlmostEqual(by_track['t']['observed_seconds'], 100e-9)
        self.assertAlmostEqual(by_track['t']['work_seconds'], 60e-9)
        self.assertAlmostEqual(by_track['t']['wait_seconds'], 20e-9)
        self.assertAlmostEqual(by_track['t']['unclassified_seconds'], 20e-9)
        self.assertEqual(by_track['t']['wait_reasons'], [dict(reason='queue', seconds=20e-9)])
        self.assertEqual(by_track['u']['work_seconds'], None)
        self.assertAlmostEqual(by_track['u']['unclassified_seconds'], 100e-9)
        self.assertEqual(result['timeline']['evidence_state'], 'Measured')
        self.assertEqual(result['timeline']['os_scheduler']['evidence_state'], 'Missing')
        self.assertEqual(sum(m['value'] for m in result['metrics']
                             if m['name'] == 'phase.application_observed_wall'), 200e-9)

    def test_incompatible_positive_overlap_is_rejected(self):
        events = [event('root'),
                  event('work', start=10, end=50, parent='root',
                        activity={'kind': 'application_work'}),
                  event('wait', start=40, end=70, parent='root',
                        activity={'kind': 'application_wait', 'reason': 'lock'})]
        with self.assertRaises(LabError) as caught:
            analyze(events, 'solver', None, 'raw.phases.json')
        self.assertEqual(caught.exception.category, 'parse_failed')

    def test_unannotated_legacy_events_are_missing_not_zero(self):
        result = analyze([event('root')], 'solver', None, 'legacy.phases.json')
        self.assertEqual(result['timeline']['evidence_state'], 'Missing')
        self.assertAlmostEqual(result['timeline']['tracks'][0]['unclassified_seconds'], 100e-9)
        self.assertIsNone(result['timeline']['tracks'][0]['work_seconds'])
        unavailable = {item['name']: item for item in result['unavailable_observations']}
        self.assertEqual(unavailable['phase.application_work_wall']['evidence_state'], 'Missing')
        self.assertEqual(unavailable['phase.application_wait_wall']['evidence_state'], 'Missing')
        self.assertTrue(any(item['name'] == 'phase.application_observed_wall'
                            for item in result['metrics']))

    def test_wait_ratio_zero_duration_and_metric_semantics(self):
        events = [event('zero-root', start=5, end=5),
                  event('zero-wait', start=5, end=5, parent='zero-root',
                        activity={'kind': 'application_wait', 'reason': 'queue'})]
        result = analyze(events, 'solver', None, 'raw.phases.json')
        self.assertIsNone(result['timeline']['tracks'][0]['wait_fraction'])
        ratio = next(item for item in result['unavailable_observations']
                     if item['name'] == 'phase.application_wait_fraction')
        self.assertEqual(ratio['evidence_state'], 'Missing')

        first = analyze([event('r', end=100), event('w', start=20, end=40, parent='r',
                            activity={'kind': 'application_wait', 'reason': 'queue'},
                            path=['solver', 'wait'])], 'solver', None, 'raw.phases.json')
        second = analyze([event('different-id', end=100), event('another-id', start=30, end=60,
                             parent='different-id', activity={'kind': 'application_wait', 'reason': 'queue'},
                             path=['solver', 'wait'])], 'solver', None, 'raw.phases.json')
        changed = analyze([event('r', end=100), event('w', start=20, end=40, parent='r',
                             activity={'kind': 'application_wait', 'reason': 'condition-variable'},
                             path=['solver', 'wait'])], 'solver', None, 'raw.phases.json')
        metric_for = lambda value: next(m for m in value['metrics']
                                        if m['name'] == 'phase.application_wait_wall')
        self.assertEqual(metric_for(first)['value'], 20e-9)
        self.assertAlmostEqual(metric_for(first)['value'] /
                               next(m for m in first['metrics']
                                    if m['name'] == 'phase.application_observed_wall')['value'], .2)
        self.assertEqual(metric_for(first)['counter_contract'], metric_for(second)['counter_contract'])
        self.assertNotEqual(metric_for(first)['counter_contract'], metric_for(changed)['counter_contract'])


if __name__ == '__main__':
    unittest.main()
