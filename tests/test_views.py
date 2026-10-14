"""Shared views, rejection semantics and evidence integrity at public boundaries."""
from html.parser import HTMLParser
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from cppperflab.cli import main
from cppperflab.comparison import compare
from cppperflab.errors import LabError
from cppperflab.html_report import render_html, export_html
from cppperflab.protocol import sha256
from cppperflab.storage import Store, write_json
from cppperflab.views import build_view, query_view, read_evidence, run_view, study_view
from test_core import comparison_result


BASELINE = "20261014T120000-000000000001"
CANDIDATE = "20261014T120000-000000000002"


def run_result(identifier, directory, values=None):
    result = comparison_result(identifier, values=values)
    result['schema_version'] = 1
    result['request']['name'] = 'standalone test'
    result['request']['workload']['inputs'] = []
    result['request']['correctness']['argv'] = ['/bin/true']
    result['started_at'] = '2026-10-14T12:00:00Z'
    result['ended_at'] = '2026-10-14T12:00:01Z'
    result['environment']['limitations'] = ['Test environment only']
    result['hotspots'] = []
    result['error'] = None
    for i, sample in enumerate(result['samples']):
        sample['index'] = i
        sample['metrics'][0]['evidence'] = 'raw.txt'
    values = [s['metrics'][0]['value'] for s in result['samples']]
    result['summary'] = [{'name': 'time.wall', 'unit': 'seconds', 'method': 'native-process-timing',
                          'scope': 'same target scope', 'n': len(values), 'median': values[0],
                          'min': min(values), 'max': max(values), 'cv': 0.0}]
    directory.mkdir(parents=True, exist_ok=True)
    (directory/'raw.txt').write_text('one\ntwo\nthree\n')
    result['evidence'] = [{'path': 'raw.txt', 'bytes': 14, 'sha256': sha256(directory/'raw.txt')}]
    return result


class SnapshotParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.active = False
        self.snapshot = ''
        self.scripts = 0

    def handle_starttag(self, tag, attrs):
        if tag == 'pre' and dict(attrs).get('id') == 'view-json':
            self.active = True
        if tag == 'script':
            self.scripts += 1

    def handle_endtag(self, tag):
        if tag == 'pre':
            self.active = False

    def handle_data(self, text):
        if self.active:
            self.snapshot += text


class ViewTests(unittest.TestCase):
    def setup_run(self, temp, rid=BASELINE, values=None):
        store = Store(Path(temp)/'store')
        directory = store.record_path(rid).parent
        result = run_result(rid, directory, values)
        write_json(directory/'result.json', result)
        return store, result

    def invoke(self, argv):
        output = io.StringIO()
        with mock.patch('sys.argv', ['perflab'] + argv), mock.patch('sys.stdout', output):
            code = main()
        return code, json.loads(output.getvalue())

    def test_run_retains_scope_samples_and_per_repetition_hotspots(self):
        with tempfile.TemporaryDirectory() as temp:
            store, result = self.setup_run(temp)
            result['hotspots'] = [dict(sample=i, self_percent=60, symbol='kernel', dso='app',
                                      evidence='raw.txt') for i in (0, 1)]
            view = run_view(result)
            self.assertEqual(len(view['hotspots']), 2)
            self.assertEqual([h['self_percent'] for h in view['hotspots']], [60, 60])
            self.assertEqual(view['metrics'][0]['summary']['scope'], 'same target scope')
            self.assertEqual(len(view['metrics'][0]['samples']), 7)
            query = query_view(view, 'hotspots', symbol='KERNEL', limit=1)
            self.assertEqual(query['pagination']['total'], 2)
            self.assertEqual(query['pagination']['next_offset'], 1)
            self.assertEqual(query['data'][0]['evidence_id'], BASELINE+':raw.txt')

    def test_empty_filter_and_invalid_pagination_have_distinct_meanings(self):
        with tempfile.TemporaryDirectory() as temp:
            store, _ = self.setup_run(temp)
            view = build_view(store, BASELINE)
            result = query_view(view, 'metrics', metric='missing')
            self.assertEqual(result['data'], [])
            self.assertEqual(result['pagination']['total'], 0)
            for args in [dict(limit=0), dict(offset=-1), dict(symbol='x')]:
                with self.assertRaises(LabError):
                    query_view(view, **args)

    def test_failed_run_and_noisy_run_remain_unaccepted(self):
        with tempfile.TemporaryDirectory() as temp:
            store, result = self.setup_run(temp)
            result['summary'][0]['cv'] = .3
            result['summary'][0]['n'] = 3
            codes = {q['code'] for q in run_view(result)['quality']}
            self.assertTrue({'high_variability', 'insufficient_repetitions'} <= codes)
            result.update(status='failed', samples=[], summary=[],
                          error={'category':'correctness_failed', 'message':'wrong result', 'exit_code':5})
            result['correctness']['status'] = 'failed'
            view = run_view(result)
            self.assertFalse(view['optimization_accepted'])
            self.assertEqual(view['metrics'], [])
            self.assertEqual(view['error']['exit_code'], 5)

    def test_evidence_query_verifies_hash_and_pages_lines(self):
        with tempfile.TemporaryDirectory() as temp:
            store, _ = self.setup_run(temp)
            result = read_evidence(store, BASELINE, 'raw.txt', offset=1, limit=1)
            self.assertEqual(result['lines'], [{'line':2, 'text':'two'}])
            self.assertEqual(result['pagination']['next_offset'], 2)
            self.assertEqual(result['integrity'], 'verified')
            store.record_path(BASELINE).parent.joinpath('raw.txt').write_text('changed')
            with self.assertRaises(LabError) as caught:
                read_evidence(store, BASELINE, 'raw.txt')
            self.assertEqual(caught.exception.category, 'storage_failed')

    def test_evidence_rejects_unlisted_paths_and_manifest_escape(self):
        with tempfile.TemporaryDirectory() as temp:
            store, result = self.setup_run(temp)
            with self.assertRaises(LabError) as caught:
                read_evidence(store, BASELINE, '../../secret')
            self.assertEqual(caught.exception.category, 'not_found')
            result['evidence'] = [{'path':'../../../secret', 'bytes':1, 'sha256':'a'*64}]
            write_json(store.record_path(BASELINE), result)
            with self.assertRaises(LabError) as caught:
                read_evidence(store, BASELINE, '../../../secret')
            self.assertEqual(caught.exception.category, 'invalid_request')

    def test_html_snapshot_is_exact_shared_view_and_escapes_tool_text(self):
        with tempfile.TemporaryDirectory() as temp:
            store, result = self.setup_run(temp)
            result['request']['name'] = '</pre><script>alert(1)</script><pre>'
            write_json(store.record_path(BASELINE), result)
            view = build_view(store, BASELINE)
            document = render_html(view, store, Path(temp)/'report.html')
            parser = SnapshotParser(); parser.feed(document)
            self.assertEqual(json.loads(parser.snapshot), view)
            self.assertEqual(parser.scripts, 1)
            self.assertNotIn('<script>alert(1)</script>', document)
            self.assertIn('<svg', document)
            self.assertIn('same target scope', document)
            with self.assertRaises(LabError):
                export_html(view, store, store.record_path(BASELINE))

    def test_incomparable_html_is_saved_and_cli_keeps_exit_seven(self):
        with tempfile.TemporaryDirectory() as temp:
            store, _ = self.setup_run(temp)
            _, candidate = self.setup_run(temp, CANDIDATE)
            candidate['tool']['mode'] = 'instrumented'
            write_json(store.record_path(CANDIDATE), candidate)
            output = Path(temp)/'incomparable.html'
            code, envelope = self.invoke(['compare', BASELINE, CANDIDATE, '--store', str(store.root),
                                          '--format','html','--output',str(output),'--json'])
            self.assertEqual(code, 7)
            self.assertFalse(envelope['ok'])
            self.assertTrue(output.is_file())
            cid = envelope['result']['record_id']
            comparison = store.read(cid, 'comparison')
            self.assertFalse(comparison['comparable'])
            self.assertNotIn('speedup', comparison)
            self.assertIn('tool.mode', output.read_text())

    def test_comparison_view_preserves_core_classification_and_sample_arrays(self):
        with tempfile.TemporaryDirectory() as temp:
            store, baseline = self.setup_run(temp)
            _, candidate = self.setup_run(temp, CANDIDATE, values=[2.0]*7)
            result = compare(baseline, candidate)
            result['comparison_id'] = '20261014T120000-000000000003'
            path = store.record_path(result['comparison_id'], 'comparison')
            path.parent.mkdir(parents=True)
            write_json(path, result)
            view = build_view(store, result['comparison_id'], 'comparison')
            self.assertEqual(view['overview']['classification'], 'observed_regression')
            self.assertEqual(view['comparisons'][0]['candidate_samples'], [2.0]*7)
            self.assertEqual(len(view['evidence']), 2)

    def test_growth_preserves_empirical_limit_and_separates_metric_meanings(self):
        point = dict(x=100, median=.1, n=9, cv=.02, name='time.wall', unit='seconds',
                     method='native-process-timing', scope='process', run_id=BASELINE)
        raw = dict(schema_version=1,study_id='20261014T120000-000000000004',status='completed',
                   error=None,started_at='now',runs=[],comparisons=[],
                   spec=dict(name='test',axis={'parameter':'n','values':[100]},metric='time.wall',
                             variants=[{'id':'A'},{'id':'B'}]),
                   growth={'A':{'points':[point], 'limitations':['Not a Big-O proof']},
                           'B':{'points':[{**point,'unit':'ns'}], 'limitations':[]}})
        view = study_view(raw)
        self.assertIn('mixed_growth_semantics', {q['code'] for q in view['quality']})
        self.assertEqual(view['growth'][0]['points'][0]['median'], .1)
        self.assertFalse(view['optimization_accepted'])
