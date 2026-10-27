"""Public dual-report export and existing report compatibility contracts."""
import io
import json
from html.parser import HTMLParser
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from cppperflab.cli import main
from cppperflab.comparison import compare
from cppperflab.errors import LabError
from cppperflab.storage import Store, write_json
from test_views import BASELINE, CANDIDATE, SnapshotParser, run_result


class AgentSnapshotParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.active = False
        self.snapshot = ''

    def handle_starttag(self, tag, attrs):
        if tag == 'pre' and dict(attrs).get('id') == 'agent-report-json':
            self.active = True

    def handle_endtag(self, tag):
        if tag == 'pre':
            self.active = False

    def handle_data(self, data):
        if self.active:
            self.snapshot += data


class DualReportCliTests(unittest.TestCase):
    def invoke(self, args):
        output = io.StringIO()
        with mock.patch('sys.argv', ['perflab', *args]), mock.patch('sys.stdout', output):
            code = main()
        return code, json.loads(output.getvalue())

    def fixture(self, temp):
        store = Store(Path(temp) / 'store')
        directory = store.record_path(BASELINE).parent
        result = run_result(BASELINE, directory)
        write_json(directory / 'result.json', result)
        return store, result

    def view(self, error=None):
        return {'record_id': BASELINE, 'view_kind': 'run',
                'status': 'failed' if error else 'completed', 'error': error,
                'test_median': 12.5}

    def test_both_exports_one_shared_view_and_one_projection(self):
        with tempfile.TemporaryDirectory() as temp:
            store, _ = self.fixture(temp)
            view = self.view()
            digest = {'schema_version': 1, 'report_type': 'agent', 'record_id': BASELINE,
                      'status': 'completed', 'error': None, 'median': view['test_median']}
            output = Path(temp) / 'output'
            def html(shared, actual_store, path, *, agent_report):
                self.assertIs(shared, view)
                self.assertIs(actual_store, store_arg)
                self.assertIs(agent_report, digest)
                return json.dumps({'view_median': shared['test_median'], 'agent_median': agent_report['median']})
            # CLI creates its own Store, so compare its root while preserving object identity across calls.
            store_arg = Store(store.root)
            with mock.patch('cppperflab.cli.Store', return_value=store_arg), \
                 mock.patch('cppperflab.cli.build_view', return_value=view) as build, \
                 mock.patch('cppperflab.reporting.build_agent_report', return_value=digest) as project, \
                 mock.patch('cppperflab.reporting.render_html', side_effect=html) as render:
                code, envelope = self.invoke(['report', BASELINE, '--store', str(store.root),
                                               '--format', 'both', '--output', str(output), '--limit', '2', '--json'])
            self.assertEqual(code, 0)
            self.assertTrue(envelope['ok'])
            build.assert_called_once_with(store_arg, BASELINE, 'run')
            project.assert_called_once_with(view, store.root, limit=2)
            self.assertEqual(render.call_count, 1)
            paths = envelope['result']['paths']
            agent = json.loads(Path(paths['agent']).read_text())
            html_values = json.loads(Path(paths['html']).read_text())
            self.assertEqual(agent['record_id'], BASELINE)
            self.assertEqual(agent['median'], html_values['view_median'])
            self.assertEqual(agent['median'], html_values['agent_median'])

    def test_real_both_exports_match_saved_metrics(self):
        with tempfile.TemporaryDirectory() as temp:
            store, _ = self.fixture(temp)
            output = Path(temp) / 'output'
            code, envelope = self.invoke(['report', BASELINE, '--store', str(store.root),
                                           '--format', 'both', '--output', str(output), '--json'])
            self.assertEqual(code, 0, envelope)
            document = (output / 'report.html').read_text()
            snapshot = SnapshotParser()
            snapshot.feed(document)
            agent_snapshot = AgentSnapshotParser()
            agent_snapshot.feed(document)
            html_view = json.loads(snapshot.snapshot)
            agent = json.loads((output / 'report.agent.json').read_text())
            self.assertEqual(json.loads(agent_snapshot.snapshot), agent)
            self.assertEqual(html_view['record_id'], agent['record_id'])
            self.assertEqual(html_view['status'], agent['status'])
            self.assertEqual(html_view['metrics'][0]['summary']['median'], agent['metrics']['items'][0]['median'])
            self.assertEqual(html_view['metrics'][0]['summary']['scope'], agent['metrics']['items'][0]['scope'])

    def test_comparison_bundle_keeps_pass_separate_from_improvement(self):
        with tempfile.TemporaryDirectory() as temp:
            store, baseline = self.fixture(temp)
            candidate_dir = store.record_path(CANDIDATE).parent
            candidate = run_result(CANDIDATE, candidate_dir)
            write_json(candidate_dir / 'result.json', candidate)
            result = compare(baseline, candidate)
            identifier = '20261014T120000-000000000003'
            result['comparison_id'] = identifier
            result.pop('decisions')  # Historical records can omit the derived projection.
            path = store.record_path(identifier, 'comparison')
            path.parent.mkdir(parents=True)
            write_json(path, result)
            output = Path(temp) / 'output'
            code, envelope = self.invoke(['report', identifier, '--kind', 'comparison',
                                           '--store', str(store.root), '--format', 'both',
                                           '--output', str(output), '--json'])
            self.assertEqual(code, 0, envelope)
            agent = json.loads((output / 'report.agent.json').read_text())
            decision = agent['comparisons']['items'][0]['decisions']
            self.assertEqual(decision['non_regression']['status'], 'PASS')
            self.assertEqual(decision['improvement']['status'], 'NOT_ESTABLISHED')
            snapshot = AgentSnapshotParser()
            snapshot.feed((output / 'report.html').read_text())
            self.assertEqual(json.loads(snapshot.snapshot), agent)

    def test_existing_json_report_remains_original_record(self):
        with tempfile.TemporaryDirectory() as temp:
            store, result = self.fixture(temp)
            with mock.patch('cppperflab.cli.build_view') as build:
                code, envelope = self.invoke(['report', BASELINE, '--store', str(store.root), '--format', 'json'])
            self.assertEqual(code, 0)
            self.assertEqual(envelope['result'], result)
            build.assert_not_called()

    def test_comparison_retains_unavailable_source_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            store, baseline = self.fixture(temp)
            candidate_dir = store.record_path(CANDIDATE).parent
            candidate = run_result(CANDIDATE, candidate_dir)
            for run in (baseline,candidate):
                run['unavailable_observations']=[{'name':'memory.rss_peak','value':None,
                    'evidence_state':'Unsupported','reason':'collector unavailable for this target'}]
                write_json(store.record_path(run['run_id']),run)
            result=compare(baseline,candidate)
            identifier='20261014T120000-000000000003'
            result['comparison_id']=identifier
            path=store.record_path(identifier,'comparison');path.parent.mkdir(parents=True)
            write_json(path,result)
            code,envelope=self.invoke(['report',identifier,'--kind','comparison','--format','agent',
                '--store',str(store.root),'--limit','1','--json'])
            self.assertEqual(code,0,envelope)
            missing=envelope['result']['attention']['unavailable_observations']
            rss=next(row for row in missing if row['metric']=='memory.rss_peak')
            self.assertEqual(rss['evidence_state'],'Unsupported')
            self.assertEqual(rss['reason'],'collector unavailable for this target')
            self.assertEqual(rss['count'],2)
            self.assertIn('observations',{item['section'] for item in envelope['result']['follow_up']})

    def test_single_run_retains_concurrent_conditions(self):
        with tempfile.TemporaryDirectory() as temp:
            store,result=self.fixture(temp)
            result['execution_context']={'kind':'concurrent_ab','concurrent_runs':2,
                'resource_conditions':{'minimum_overlap_fraction':0.5},'status':'PASS',
                'overlap_fraction':0.9,'overlap':{'evidence_state':'Measured'}}
            write_json(store.record_path(BASELINE),result)
            code,envelope=self.invoke(['report',BASELINE,'--format','agent','--store',str(store.root),'--json'])
            self.assertEqual(code,0,envelope)
            context=envelope['result']['subject']['execution_context']
            self.assertEqual(context['kind'],'concurrent_ab')
            self.assertEqual(context['overlap_fraction'],0.9)

    def test_agent_stdout_and_file_are_compact_projections(self):
        with tempfile.TemporaryDirectory() as temp:
            store, _ = self.fixture(temp)
            args = ['report', BASELINE, '--store', str(store.root), '--format', 'agent']
            # Agent output is a JSON envelope even when --json is omitted.
            code, envelope = self.invoke(args)
            self.assertEqual(code, 0)
            report = envelope['result']
            self.assertEqual(report['report_type'], 'agent')
            self.assertEqual(report['record_id'], BASELINE)
            self.assertEqual(report['metrics']['items'][0]['median'], 1.0)
            self.assertNotIn('samples', report['metrics']['items'][0])
            path = Path(temp) / 'agent.json'
            code, exported = self.invoke([*args, '--output', str(path)])
            self.assertEqual(code, 0)
            self.assertEqual(exported['result']['paths']['agent'], str(path))
            self.assertEqual(json.loads(path.read_text()), report)

    def test_incomparable_agent_report_preserves_exit_seven(self):
        error = {'category': 'incomparable', 'message': 'scope mismatch', 'details': {}, 'exit_code': 7}
        view = {**self.view(error), 'view_kind': 'comparison', 'status': 'incomparable'}
        digest = {'schema_version': 1, 'report_type': 'agent', 'record_id': BASELINE,
                  'status': 'incomparable', 'error': error}
        with tempfile.TemporaryDirectory() as temp:
            store, _ = self.fixture(temp)
            with mock.patch('cppperflab.cli.build_view', return_value=view), \
                 mock.patch('cppperflab.agent_report.build_agent_report', return_value=digest):
                code, envelope = self.invoke(['report', BASELINE, '--store', str(store.root),
                                               '--kind', 'comparison', '--format', 'agent'])
            self.assertEqual(code, 7)
            self.assertFalse(envelope['ok'])
            self.assertEqual(envelope['error']['category'], 'incomparable')

    def test_failed_result_exports_and_keeps_exit_status(self):
        with tempfile.TemporaryDirectory() as temp:
            store, _ = self.fixture(temp)
            error = {'category': 'correctness_failed', 'message': 'bad result', 'details': {}, 'exit_code': 5}
            view = self.view(error)
            digest = {'schema_version': 1, 'report_type': 'agent', 'record_id': BASELINE,
                      'status': 'failed', 'error': error}
            output = Path(temp) / 'out'
            with mock.patch('cppperflab.cli.build_view', return_value=view), \
                 mock.patch('cppperflab.reporting.build_agent_report', return_value=digest), \
                 mock.patch('cppperflab.reporting.render_html', return_value='failure report'):
                code, envelope = self.invoke(['report', BASELINE, '--store', str(store.root),
                                               '--format', 'both', '--output', str(output), '--json'])
            self.assertEqual(code, 5)
            self.assertFalse(envelope['ok'])
            self.assertEqual(envelope['error']['category'], 'correctness_failed')
            self.assertEqual(json.loads((output / 'report.agent.json').read_text())['error']['exit_code'], 5)
            self.assertTrue((output / 'report.html').is_file())

    def test_rejects_invalid_format_controls_before_building(self):
        with tempfile.TemporaryDirectory() as temp:
            store, _ = self.fixture(temp)
            for args in (['--format', 'both'], ['--format', 'agent', '--limit', '0'],
                         ['--format', 'both', '--output', temp, '--limit', '201'],
                         ['--format', 'html', '--output', str(Path(temp) / 'x'), '--limit', '1'],
                         ['--format', 'json', '--output', str(Path(temp) / 'x')]):
                with self.subTest(args=args), mock.patch('cppperflab.cli.build_view') as build:
                    code, envelope = self.invoke(['report', BASELINE, '--store', str(store.root), *args, '--json'])
                    self.assertEqual(code, 2)
                    self.assertEqual(envelope['error']['category'], 'invalid_request')
                    build.assert_not_called()

    def test_export_rejects_store_and_symlink_into_store(self):
        from cppperflab.reporting import export_agent, export_both
        with tempfile.TemporaryDirectory() as temp:
            store, _ = self.fixture(temp)
            view = self.view()
            original = store.record_path(BASELINE).read_text()
            with self.assertRaises(LabError):
                export_agent(view, store, store.record_path(BASELINE))
            with self.assertRaises(LabError):
                export_both(view, store, store.root / 'bundle')
            directory = Path(temp) / 'bundle'
            directory.mkdir()
            (directory / 'report.agent.json').symlink_to(store.record_path(BASELINE))
            with mock.patch('cppperflab.reporting.build_agent_report') as project, \
                 mock.patch('cppperflab.reporting.render_html') as render, self.assertRaises(LabError):
                export_both(view, store, directory)
            project.assert_not_called()
            render.assert_not_called()
            self.assertEqual(store.record_path(BASELINE).read_text(), original)
            self.assertFalse((directory / 'report.html').exists())


if __name__ == '__main__':
    unittest.main()
