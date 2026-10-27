"""Work-report protocol tests use synthetic workload outputs, not benchmarks."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from cppperflab.errors import LabError
from cppperflab.budget import execution_budget
from cppperflab.adapters import REGISTRY
from cppperflab.lab import plan, run
from cppperflab.protocol import load_request
from cppperflab.storage import Store, write_json
from cppperflab.work import normalize, validate_config
from test_core import request_template


def config():
    return dict(schema_version=1, path='items.work.json', definition_id='mesh-cells-v1')


def report():
    return dict(schema_version=1, definition_id='mesh-cells-v1', unit='cells', count=12,
                scope='target-invocation', count_kind='observed', completed=True)


class WorkReportTests(unittest.TestCase):
    def test_config_and_report_validation(self):
        validate_config(config())
        for key, value in [('path', '../items.work.json'), ('path', 'work.json'), ('definition_id', ''), ('schema_version', True)]:
            bad = config(); bad[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(LabError): validate_config(bad)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'items.work.json'
            for key, value in [('definition_id','different'), ('unit',' '), ('count',-1), ('count',True),
                               ('count',1.2), ('scope','phase'), ('count_kind','estimated'), ('completed',False),
                               ('schema_version',True)]:
                bad = report(); bad[key] = value; write_json(path, bad)
                with self.subTest(key=key, value=value), self.assertRaises(LabError) as caught:
                    normalize(path, config())
                self.assertEqual(caught.exception.category, 'parse_failed')
            value = report(); value['count'] = 0; write_json(path, value)
            metric = normalize(path, config())
            self.assertEqual((metric['name'], metric['value'], metric['unit'], metric['evidence_state']), ('work.items', 0, 'cells', 'Measured'))
            self.assertEqual((metric['count_kind'],metric['completed']),('observed',True))
            first = metric['counter_contract']; value['count'] = 99; write_json(path, value)
            self.assertEqual(normalize(path, config())['counter_contract'], first)
            value['unit'] = 'faces'; write_json(path, value)
            self.assertNotEqual(normalize(path, config())['counter_contract'], first)

    def test_incompatible_batch_rejected_before_probe(self):
        with tempfile.TemporaryDirectory() as temp:
            request = request_template(temp); request['measurement']['work_report'] = config()
            with mock.patch('cppperflab.lab.probe_tools', side_effect=AssertionError('probe ran')):
                with self.assertRaises(LabError) as caught: plan(request, Store(Path(temp) / 'store'))
            self.assertEqual(caught.exception.category, 'invalid_request')

    def test_plan_selects_report_capability_only_for_perf_stat(self):
        with tempfile.TemporaryDirectory() as temp:
            request = request_template(temp)
            request['measurement'].update(tool='auto', work_report=config())
            request['goal'] = dict(metrics=['work.items'],max_overhead='high',allow_instrumentation=True)
            def probes(store, ids, options, target):
                self.assertEqual(ids, ['perf-stat','native-resource'])
                return {'tools': {tool:{'available':True} for tool in ids}}
            with mock.patch('cppperflab.lab.probe_tools', side_effect=probes):
                result = plan(request, Store(Path(temp) / 'store'))
            self.assertEqual(result['selected'], 'perf-stat')
            self.assertTrue(all(not row['feasible'] for row in result['candidates'] if row['tool'] not in ('perf-stat','native-resource')))

    def test_grouped_goal_exposes_same_execution_diagnostics(self):
        with tempfile.TemporaryDirectory() as temp:
            request=request_template(temp)
            request['measurement'].update(tool='auto',work_report=config(),options={
                'events':['cache-misses','instructions'],
                'event_groups':[['cache-misses','instructions']]})
            request['goal']=dict(metrics=['cpu.cache_mpki','cpu.instructions_per_work'],
                                 max_overhead='high',allow_instrumentation=True)
            with mock.patch('cppperflab.lab.probe_tools',return_value={'tools':{'perf-stat':{'available':True}}}):
                result=plan(request,Store(Path(temp)/'store'))
            self.assertEqual(result['selected'],'perf-stat')

    def test_missing_group_does_not_claim_dme_capability(self):
        from cppperflab.lab import configured_metric_ids, _missing_claim_metrics
        with tempfile.TemporaryDirectory() as temp:
            request=request_template(temp)
            request['measurement'].update(tool='perf-stat',work_report=config(),options={
                'events':['cache-misses','instructions']})
            request['goal']=dict(metrics=['cpu.cache_mpki','cpu.instructions_per_work'],
                                 max_overhead='high',allow_instrumentation=True)
            supplied=configured_metric_ids(request,REGISTRY['perf-stat'])
            self.assertNotIn('cpu.cache_mpki',supplied)
            self.assertNotIn('cpu.instructions_per_work',supplied)
            with mock.patch('cppperflab.lab.probe_tools',return_value={'tools':{'perf-stat':{'available':True}}}):
                result=plan(request,Store(Path(temp)/'store'))
            self.assertIsNone(result['selected'])
            self.assertTrue(any('missing_metrics' in reason for reason in result['candidates'][0]['reasons']))
            request['claim']=dict(metric='cpu.cache_mpki',conditions=[])
            self.assertEqual(_missing_claim_metrics(request,REGISTRY['perf-stat']),{'cpu.cache_mpki'})
            # With a claim, the incompatible candidate is excluded even before probing.
            with mock.patch('cppperflab.lab.probe_tools',side_effect=AssertionError('probe ran')):
                result=plan(request,Store(Path(temp)/'store'))
            self.assertIsNone(result['selected'])
            self.assertIn('claim_metrics_unavailable',result['candidates'][0]['reasons'])

    def test_work_report_budget_rejects_other_adapter_scopes(self):
        with tempfile.TemporaryDirectory() as temp:
            request=request_template(temp);request['measurement'].update(tool='auto',work_report=config())
            budget=execution_budget(request)
            rows={row['tool']:row for row in budget['candidates']}
            self.assertTrue(rows['perf-stat']['eligible'])
            self.assertTrue(rows['native-resource']['eligible'])
            for name in ('hyperfine','google-benchmark','massif'):
                self.assertFalse(rows[name]['eligible'])
                self.assertIn('Work report scope',rows[name]['reason']['message'])

    def test_sample_work_evidence_is_tied_to_each_fresh_measurement(self):
        class Adapter:
            id='perf-stat'; batch=False
            def describe(self): return {'id':'perf-stat','mode':'fixture'}
            def prepare(self, command, *args, **kwargs): return command
            def parse(self, folder, options, record):
                return {'metrics':[dict(name='time.wall',value=1.0,unit='seconds',method='native-process-timing',
                    scope='test',evidence='stdout.txt',collection_evidence='events.json',evidence_state='Measured')], 'hotspots':[]}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); script = root / 'producer.py'
            script.write_text('import json,sys\nfrom pathlib import Path\nr='+repr(report())+'\nPath(sys.argv[1]).write_text(json.dumps(r))\n')
            request = request_template(temp)
            request['measurement'].update(tool='perf-stat',repetitions=2,warmups=0,work_report=config())
            request['target']['argv']=[sys.executable,str(script),'{run_dir}/items.work.json']
            path=root/'request.json';write_json(path,request);request=load_request(path)
            selected = {'selected':'perf-stat','probes':{'tools':{'perf-stat':{'version':'fixture'}}}}
            with mock.patch('cppperflab.lab.plan',return_value=selected), mock.patch.dict('cppperflab.lab.REGISTRY',{'perf-stat':Adapter()}):
                result = run(request,Store(root/'store'))
            self.assertEqual(result['status'],'completed',result['error'])
            for index,sample in enumerate(result['samples']):
                work = next(m for m in sample['metrics'] if m['name']=='work.items')
                self.assertEqual(work['value'],12)
                self.assertEqual(work['evidence'],f'measurement-{index:03d}/items.work.json')
                self.assertTrue((Path(result['evidence_directory'])/work['evidence']).is_file())
                self.assertEqual(sample['metrics'][0]['collection_evidence'],f'measurement-{index:03d}/events.json')
            script.write_text(script.read_text().replace('mesh-cells-v1','wrong-definition'))
            with mock.patch('cppperflab.lab.plan',return_value=selected), mock.patch.dict('cppperflab.lab.REGISTRY',{'perf-stat':Adapter()}):
                result = run(request,Store(root/'store'))
            self.assertEqual(result['status'],'failed')
            self.assertEqual(result['error']['category'],'parse_failed')
            self.assertTrue((Path(result['evidence_directory'])/'measurement-000/items.work.json').is_file())
