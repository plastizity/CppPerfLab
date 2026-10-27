"""Evidence projection fixtures; no performance measurements are performed."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from cppperflab.agent_report import build_agent_report
from cppperflab.comparison import compare
from cppperflab.html_report import data_movement_section
from cppperflab.lab import summarize
from cppperflab.performance_summary import data_movement_projection
from cppperflab.protocol import metric
from cppperflab.views import query_view, run_view
from test_views import BASELINE, CANDIDATE, run_result


class DataMovementReportingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.run = run_result(BASELINE, self.root / 'baseline', [1.0] * 7)
        for sample in self.run['samples']:
            sample['metrics'].extend([
                metric('work.items', 100, 'item', 'target-reported-invocation-work',
                       'whole invocation', 'work.json', work_definition_id='mesh-cells',
                       evidence_state='Measured'),
                metric('cpu.cache_misses', 20, 'count', 'hardware-counter',
                       'whole invocation', 'perf.csv', evidence_state='Measured'),
                metric('kernel.logical_input_bytes', 200, 'bytes', 'fixture',
                       'per iteration', 'model.txt', evidence_state='Modeled')])
        self.run['summary'] = summarize(self.run['samples'])

    def test_per_sample_provenance_and_missing_are_not_conflated(self):
        projection = data_movement_projection([self.run], members=[
            {'run_id':BASELINE,'case_id':'case-a','profile_id':'native','variant':'baseline','block_id':'block-1'}])
        work = projection['columns']['work']['observations']
        self.assertEqual(len(work), 7)
        self.assertEqual(work[0]['value'],100)
        self.assertEqual(work[0]['work_definition_id'],'mesh-cells')
        self.assertEqual(work[0]['membership']['block_id'],'block-1')
        self.assertEqual(work[0]['evidence_id'],BASELINE+':work.json')
        movement = projection['columns']['data_movement']['observations']
        self.assertIn(('kernel.logical_input_bytes','Modeled'), {(r['name'],r['evidence_state']) for r in movement})
        self.assertIn(('cpu.cache_misses','Measured'), {(r['name'],r['evidence_state']) for r in movement})
        empty=data_movement_projection([])
        self.assertEqual({i['evidence_state'] for i in empty['unknowns']},{'Missing'})
        self.assertEqual(empty['possible_explanations'],[])

    def test_comparison_uses_existing_result_and_retains_member_reference(self):
        candidate=run_result(CANDIDATE,self.root/'candidate',[.5]*7)
        comparison=compare(self.run,candidate)
        comparison.update(comparison_id='saved',block_pairs=[{'block_id':'block-1',
                          'members':{'baseline':[{'run_id':BASELINE,'sample_index':0}],
                                     'candidate':[{'run_id':CANDIDATE,'sample_index':0}]}}])
        row=data_movement_projection([self.run,candidate],[comparison])['columns']['outcome']['comparisons'][0]
        self.assertEqual(row['observed_change'],'observed_reduction')
        self.assertEqual(row['block_pairs'][0]['members']['candidate'][0]['run_id'],CANDIDATE)
        comparison['classification']='no_clear_change'
        row=data_movement_projection([self.run,candidate],[comparison])['columns']['outcome']['comparisons'][0]
        self.assertEqual(row['observed_change'],'inconclusive')

    def test_agent_human_and_query_share_projection(self):
        view=run_view(self.run)
        section=query_view(view,'data_movement')['data']
        agent=build_agent_report(view,self.root,limit=2)
        self.assertEqual(agent['data_movement']['columns']['work']['observations']['items'],
                         section['columns']['work']['observations'][:2])
        self.assertEqual(agent['data_movement']['columns']['work']['observations']['total'],7)
        html=data_movement_section(view)
        self.assertIn('工作量',html)
        self.assertIn('cpu.cache_misses',html)
        self.assertIn(BASELINE+':work.json',html)
        self.assertTrue(any(x['section']=='data_movement' for x in agent['follow_up']))

    def test_grouped_ratio_retains_estimated_state_and_actual_operands(self):
        from test_dme_metrics import sample
        self.run['samples']=[sample()]
        result=data_movement_projection([self.run])
        work=result['columns']['work']['observations']
        ratio=next(row for row in work if row['name']=='cpu.instructions_per_work')
        self.assertEqual((ratio['value'],ratio['evidence_state']),(100,'Estimated'))
        self.assertEqual(ratio['sources'][0]['value'],1000)
        self.assertEqual((ratio['denominator']['value'],ratio['denominator']['work_definition_id']),(10,'mesh.cells'))
        self.assertEqual(ratio['denominator']['evidence_id'],BASELINE+':measurement-000/work.json')

    def test_benchmark_throughput_dictionary_sources_remain_intact(self):
        from cppperflab.adapters import parse_google_benchmark
        path=self.root/'benchmark.json'
        path.write_text(json.dumps({'benchmarks':[{'name':'Dot/16','iterations':100,
            'time_unit':'ns','real_time':1000,'cpu_time':1000,'op_count':1}]}))
        parsed=parse_google_benchmark(path,counters={'operations':'op_count'})
        self.run['samples']=[{'index':0,'metrics':parsed['samples'][0]}]
        result=data_movement_projection([self.run])
        row=next(item for item in result['columns']['outcome']['observations']
                 if item['name']=='workload.throughput')
        original=next(item for item in parsed['samples'][0] if item['name']=='workload.throughput')
        self.assertEqual(row['sources'],original['sources'])
        self.assertAlmostEqual(row['sources']['wall_seconds'],1e-6)
        self.assertEqual(row['denominator']['scope'],'per iteration of Dot/16')

    def test_human_table_uses_short_provenance_and_existing_comparison(self):
        from test_dme_metrics import sample
        self.run['samples']=[sample()]
        self.run['tool']={'id':'perf-stat','version':'perf 1','mode':'counter','extra':'large metadata'}
        comparison={'metric':'cpu.instructions_per_work','comparable':False,'unit':'count/cell',
                    'baseline_median':100,'candidate_median':90,'observed_change':'observed_reduction'}
        result=data_movement_projection([self.run], members=[{'run_id':BASELINE,'variant':'baseline',
                                        'block_id':'block-2','slot':0}])
        result['columns']['work']['comparisons']=[comparison]
        rendered=data_movement_section({'data_movement':result})
        self.assertIn('<td>perf-stat · perf 1</td>',rendered)
        self.assertIn('work.items · 10 · cell · Measured · mesh.cells',rendered)
        self.assertIn('baseline / block-2 / 0',rendered)
        self.assertIn('<td>100</td><td>90</td><td>count/cell</td><td>inconclusive</td>',rendered)
        self.assertIn('完整观测元数据',rendered)
        self.assertNotIn('large metadata',rendered)

    def test_specific_cache_and_tlb_raw_events_keep_evidence_and_states(self):
        from cppperflab.performance_summary import dimension
        names=['cpu.l1d_loads','cpu.llc_load_misses','cpu.dtlb_loads',
               'cpu.l1d_load_misses','cpu.llc_loads','cpu.dtlb_load_misses']
        descriptor={'event_descriptor_id':'linux-cache-event', 'event_descriptor_version':1,
                    'event_semantics_key':'cpu-family/example/event',
                    'event_descriptor_evidence':'measurement-000/event-descriptors.json'}
        for name in names[:3]:
            self.run['samples'][0]['metrics'].append(metric(name,11,'count','hardware-counter',
                'target invocation','perf.csv',event=name,**descriptor))
        self.run['unavailable_observations']=[dict(name=name, value=None, evidence_state=state,
            evidence_version=1, reason='fixture unavailable', unit='count',method='hardware-counter',
            scope='target invocation', evidence='perf.csv', **descriptor)
            for name,state in zip(names[3:],('Unsupported','Missing','Invalid'))]
        self.run['summary']=summarize(self.run['samples'])
        view=run_view(self.run)
        column=view['data_movement']['columns']['data_movement']
        rows={row['name']:row for row in column['observations'] if row['name'] in names}
        unavailable={row['name']:row for row in column['unavailable'] if row['name'] in names}
        self.assertEqual(set(rows),set(names[:3]))
        self.assertEqual({name:row['evidence_state'] for name,row in unavailable.items()},
                         dict(zip(names[3:],('Unsupported','Missing','Invalid'))))
        for name,row in {**rows,**unavailable}.items():
            self.assertEqual(dimension(name),'execution')
            self.assertEqual(row['event_semantics_key'],descriptor['event_semantics_key'])
            self.assertEqual(row['event_descriptor_evidence'],descriptor['event_descriptor_evidence'])
            self.assertEqual(row['evidence_id'],BASELINE+':perf.csv')
        self.assertIn('cpu.cache_misses',{row['name'] for row in column['observations']})
        agent=build_agent_report(view,self.root,limit=200)
        actual={row['name']:row for row in agent['data_movement']['columns']['data_movement']['observations']['items']}
        self.assertEqual(actual['cpu.l1d_loads']['event_descriptor_id'],'linux-cache-event')
        html=data_movement_section(view)
        self.assertIn('cpu.llc_load_misses',html)
        self.assertIn('event-descriptors.json',html)

if __name__ == '__main__':
    unittest.main()
