"""Run disposition metadata is append-only and never edits measurement records."""
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

from cppperflab.errors import LabError
from cppperflab.protocol import sha256
from cppperflab.run_notes import annotate, notes
from cppperflab.storage import Store, write_json


class RunNoteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'store')

    def tearDown(self):
        self.temp.cleanup()

    def run_record(self, started_at, *, status='completed', correctness='passed'):
        run_id, directory = self.store.create()
        write_json(directory / 'result.json', dict(schema_version=1, run_id=run_id, status=status,
            started_at=started_at, correctness=dict(status=correctness), samples=[], evidence=[]))
        return run_id

    def append(self, run_id, action, value, revision, reason='reviewed decision'):
        return annotate(self.store, run_id, action=action, value=value, reason=reason,
                        expected_revision=revision)

    def test_legacy_run_has_explicit_undecided_metadata(self):
        run_id = self.run_record('2026-10-26T00:00:00+00:00')
        self.assertEqual(notes(self.store, run_id), dict(schema_version=1, run_id=run_id, revision=0,
            current_sha256=None, candidate_state='undecided', superseded_by=None, events=[]))

    def test_candidate_lifecycle_and_clear_append_events_without_mutating_result(self):
        run_id = self.run_record('2026-10-26T00:00:00+00:00')
        replacement = self.run_record('2026-10-26T00:01:00+00:00')
        original = self.store.record_path(run_id)
        digest = sha256(original)
        first = self.append(run_id, 'candidate_state', 'retained', 0)
        self.assertEqual(first['candidate_state'], 'retained')
        second = self.append(run_id, 'superseded_by', replacement, 1)
        self.assertEqual(second['superseded_by'], replacement)
        third = self.append(run_id, 'candidate_state', 'withdrawn', 2)
        final = self.append(run_id, 'superseded_by', None, 3, 'Replacement link withdrawn')
        self.assertEqual(final['candidate_state'], 'withdrawn')
        self.assertIsNone(final['superseded_by'])
        self.assertEqual([e['revision'] for e in final['events']], [1, 2, 3, 4])
        self.assertEqual([e['previous_sha256'] for e in final['events']],
                         [None, *[e['sha256'] for e in final['events'][:-1]]])
        self.assertEqual(sha256(original), digest)
        self.assertEqual(self.store.read(run_id)['status'], 'completed')

    def test_revision_conflict_never_overwrites_the_concurrent_event(self):
        run_id = self.run_record('2026-10-26T00:00:00+00:00')
        destination = self.store.root / 'run-notes' / run_id / '00000001.json'
        incumbent = dict(schema_version=1, run_id=run_id, revision=1, previous_sha256=None,
                         action='candidate_state', value='withdrawn', reason='other writer',
                         recorded_at='2026-10-26T00:01:00+00:00')
        def concurrent_link(source, target):
            write_json(destination, incumbent)
            raise FileExistsError(17, 'revision already published', str(target))
        with mock.patch('cppperflab.run_notes.os.link', side_effect=concurrent_link):
            with self.assertRaises(LabError) as caught:
                self.append(run_id, 'candidate_state', 'retained', 0)
        self.assertEqual(caught.exception.category, 'incomparable')
        self.assertEqual(json.loads(destination.read_text()), incumbent)
        notes_result = notes(self.store, run_id)
        self.assertEqual(notes_result['candidate_state'], 'withdrawn')
        self.assertFalse(list(destination.parent.glob('.pending-*')))

    def test_two_writers_reading_same_revision_publish_one_event_and_conflict(self):
        run_id = self.run_record('2026-10-26T00:00:00+00:00')
        result_digest = sha256(self.store.record_path(run_id))
        barrier = threading.Barrier(2)
        real_link = os.link
        def synchronized_link(source, target):
            barrier.wait(timeout=5)
            return real_link(source, target)
        def write(state):
            return self.append(run_id, 'candidate_state', state, 0, state)
        with mock.patch('cppperflab.run_notes.os.link', side_effect=synchronized_link):
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(write, state) for state in ('retained', 'withdrawn')]
                outcomes = []
                for future in futures:
                    try:
                        outcomes.append(future.result(timeout=10)['candidate_state'])
                    except LabError as error:
                        outcomes.append(error.category)
        self.assertEqual(outcomes.count('incomparable'), 1)
        winner = next(outcome for outcome in outcomes if outcome != 'incomparable')
        self.assertIn(winner, ('retained', 'withdrawn'))
        current = notes(self.store, run_id)
        self.assertEqual(current['revision'], 1)
        self.assertEqual(current['candidate_state'], winner)
        self.assertEqual(sha256(self.store.record_path(run_id)), result_digest)

    def test_last_event_hash_is_not_an_external_tamper_anchor(self):
        run_id = self.run_record('2026-10-26T00:00:00+00:00')
        self.append(run_id, 'candidate_state', 'retained', 0)
        event = self.store.root / 'run-notes' / run_id / '00000001.json'
        altered = json.loads(event.read_text())
        altered['value'] = 'withdrawn'
        write_json(event, altered)
        # The chain detects altered events only once a later event references their hash.
        self.assertEqual(notes(self.store, run_id)['candidate_state'], 'withdrawn')

    def test_intermediate_event_tampering_breaks_hash_chain(self):
        run_id = self.run_record('2026-10-26T00:00:00+00:00')
        self.append(run_id, 'candidate_state', 'retained', 0)
        self.append(run_id, 'candidate_state', 'withdrawn', 1)
        event = self.store.root / 'run-notes' / run_id / '00000001.json'
        altered = json.loads(event.read_text())
        altered['reason'] = 'tampered'
        write_json(event, altered)
        with self.assertRaises(LabError) as caught:
            notes(self.store, run_id)
        self.assertEqual(caught.exception.category, 'storage_failed')

    def test_invalid_revision_reason_terminal_state_and_replacement_are_rejected(self):
        source = self.run_record('2026-10-26T00:00:00+00:00')
        earlier = self.run_record('2026-10-25T00:00:00+00:00')
        failed = self.run_record('2026-10-26T00:01:00+00:00', correctness='failed')
        running = self.run_record('2026-10-26T00:02:00+00:00', status='running')
        for kwargs in (
            dict(action='candidate_state', value='kept', reason='valid', expected_revision=0),
            dict(action='candidate_state', value='retained', reason=' ', expected_revision=0),
            dict(action='candidate_state', value='retained', reason='valid', expected_revision=True),
            dict(action='candidate_state', value='retained', reason='valid', expected_revision=1),
            dict(action='superseded_by', value=earlier, reason='valid', expected_revision=0),
            dict(action='superseded_by', value=failed, reason='valid', expected_revision=0),
            dict(action='superseded_by', value=source, reason='valid', expected_revision=0),
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(LabError):
                annotate(self.store, source, **kwargs)
        with self.assertRaises(LabError):
            self.append(running, 'candidate_state', 'retained', 0)
        self.assertEqual(notes(self.store, source)['revision'], 0)

    def test_supersession_cannot_create_a_cycle(self):
        first = self.run_record('2026-10-26T00:00:00+00:00')
        second = self.run_record('2026-10-26T00:01:00+00:00')
        self.append(first, 'superseded_by', second, 0)
        with self.assertRaises(LabError) as caught:
            self.append(second, 'superseded_by', first, 0)
        self.assertEqual(caught.exception.category, 'invalid_request')


if __name__ == '__main__':
    unittest.main()
