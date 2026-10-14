import copy
import json
from pathlib import Path
import tempfile
import unittest

from cppperflab.adapters import REGISTRY,parse_google_benchmark
from cppperflab.errors import LabError
from cppperflab.metrics import catalog
from cppperflab.valgrind_profiles import parse_cachegrind,parse_callgrind,parse_dhat


class ProfileTests(unittest.TestCase):
    def parse(self,parser,text,**kw):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)/'raw';p.write_text(text)
            return parser(p,**kw)

    def cache_text(self,summary='100 2 1 80 8 4 20 2 1 10 2 5 1'):
        return ('desc: I1 cache: 32768 B, 64 B, 8-way associative\n'
                'desc: D1 cache: 32768 B, 64 B, 8-way associative\n'
                'desc: LL cache: 8388608 B, 64 B, 16-way associative\n'
                'events: Ir I1mr ILmr Dr D1mr DLmr Dw D1mw DLmw Bc Bcm Bi Bim\nsummary: '+summary+'\n')

    def test_cache_model_units_and_ratios(self):
        r=self.parse(parse_cachegrind,self.cache_text())
        m={v['name']:v for v in r['metrics']}
        self.assertEqual(m['cachegrind.d1_miss_rate']['value'],.1)
        self.assertEqual(m['cachegrind.ll_data_mpki']['value'],50)
        self.assertTrue(all(v['evidence_state']=='Modeled' for v in m.values()))
        self.assertFalse(any(v['name'].startswith('cpu.') for v in m.values()))
        zero=self.parse(parse_cachegrind,self.cache_text('100 2 1 80 8 4 20 2 1 10 2 0 0'))
        self.assertEqual(zero['unavailable_observations'][0]['evidence_state'],'Missing')
        self.assertIsNone(zero['unavailable_observations'][0]['value'])

    def test_cache_rejects_incomplete_or_inconsistent_profiles(self):
        for text in [self.cache_text('1 2'),self.cache_text().replace('events: Ir','events: Ir Ir'),
                     self.cache_text('100 2 1 80 8 4 20 2 1 10 11 5 1'),self.cache_text()+'summary: 1\n']:
            with self.assertRaises(LabError):self.parse(parse_cachegrind,text)

    def dhat(self):
        return dict(dhatFileVersion=2,mode='heap',bklt=True,ftbl=['root','site'],pps=[
            dict(tb=100,tbk=2,gb=40,gbk=1,eb=10,ebk=1,mb=80,fs=[1]),
            dict(tb=200,tbk=4,gb=60,gbk=2,eb=20,ebk=1,mb=100,fs=[0,1])])

    def test_dhat_global_peak_is_simultaneous_not_sum_site_peaks(self):
        r=self.parse(parse_dhat,json.dumps(self.dhat()),operation_count=3)
        m={v['name']:v['value'] for v in r['metrics']}
        self.assertEqual(m['memory.heap_peak_live'],100)
        self.assertEqual(m['memory.allocated_bytes'],300)
        self.assertEqual(m['memory.allocations_per_operation'],2)
        self.assertEqual(r['allocation_sites'][1]['frames'],['root','site'])

    def test_dhat_rejects_unsupported_modes_and_invalid_stacks(self):
        for key,value in [('mode','copy'),('dhatFileVersion',3),('bklt',False)]:
            raw=self.dhat();raw[key]=value
            with self.assertRaises(LabError):self.parse(parse_dhat,json.dumps(raw))
        raw=self.dhat();raw['pps'][0]['fs']=[3]
        with self.assertRaises(LabError):self.parse(parse_dhat,json.dumps(raw))
        raw=self.dhat();raw['pps'][0]['gb']=101
        with self.assertRaises(LabError):self.parse(parse_dhat,json.dumps(raw))

    def call_text(self):
        return '# callgrind format\nversion: 1\npart: 1\nevents: Ir\nfn=(1) caller\ncfn=(2) callee\ncalls=3 0\n0 5\nfn=(2)\ncfn=(1)\ncalls=2 0\n0 3\ntotals: 20\n'

    def test_call_counts_resolve_compressed_functions(self):
        r=self.parse(parse_callgrind,self.call_text())
        self.assertEqual(r['metrics'][0]['value'],5)
        self.assertEqual(r['call_edges'][0]['callee'],'callee')
        self.assertEqual(r['call_edges'][1]['caller'],'callee')
        self.assertEqual(r['call_edges'][1]['calls'],2)
        self.assertEqual(r['metrics'][0]['evidence_state'],'Measured')

    def test_call_profile_rejects_unresolved_references_and_multiple_parts(self):
        for text in [self.call_text().replace('cfn=(2) callee','cfn=(2)'),self.call_text()+'part: 2\n',
                     self.call_text().replace('calls=3 0','calls=-3 0')]:
            with self.assertRaises(LabError):self.parse(parse_callgrind,text)

    def benchmark(self,**changes):
        row=dict(name='Dot/16',iterations=100,time_unit='ns',real_time=1000,cpu_time=1000,
                 flop_count=32,bytes_count=256,op_count=1)
        row.update(changes)
        return json.dumps({'benchmarks':[row]})

    def test_benchmark_useful_work_uses_same_iteration_scope(self):
        counters=dict(useful_flops='flop_count',logical_bytes='bytes_count',operations='op_count')
        r=self.parse(parse_google_benchmark,self.benchmark(),counters=counters)
        m={v['name']:v for v in r['samples'][0]}
        self.assertAlmostEqual(m['kernel.useful_flop_rate']['value'],32e6)
        self.assertAlmostEqual(m['workload.throughput']['value'],1e6)
        self.assertEqual(m['kernel.logical_arithmetic_intensity']['value'],.125)
        self.assertEqual(m['kernel.logical_arithmetic_intensity']['evidence_state'],'Modeled')
        self.assertEqual(m['kernel.useful_flops']['scope'],m['time.iteration.wall']['scope'])

    def test_benchmark_rejects_missing_or_invalid_declared_work(self):
        for text in [self.benchmark(flop_count=None),self.benchmark(flop_count=-1),self.benchmark(real_time=0)]:
            with self.assertRaises(LabError):self.parse(parse_google_benchmark,text,counters={'useful_flops':'flop_count'})

    def test_declared_capabilities_follow_options(self):
        self.assertNotIn('memory.allocations_per_operation',REGISTRY['dhat'].configured_metrics({}))
        self.assertIn('memory.allocations_per_operation',REGISTRY['dhat'].configured_metrics({'operation_count':2}))
        self.assertNotIn('kernel.useful_flop_rate',REGISTRY['google-benchmark'].configured_metrics({'filter':'Dot'}))
        self.assertEqual(catalog(REGISTRY,metric_id='control.call_count')['metrics'][0]['providers'],['callgrind'])
        self.assertEqual(catalog(REGISTRY,metric_id='kernel.useful_flop_rate')['metrics'][0]['providers'],['google-benchmark'])
        for tool,options in [('cachegrind',{'D1':[1000,8,64]}),('dhat',{'operation_count':0}),
                            ('google-benchmark',{'filter':'Dot','counters':{'useful_flops':'real_time'}})]:
            with self.assertRaises(LabError):REGISTRY[tool].validate(options)

    def test_growth_quality_is_structured_not_a_complexity_proof(self):
        from cppperflab.studies import fit_growth
        points=[dict(x=n,median=n*n,n=9,cv=0) for n in (1,2,4,8)]
        self.assertEqual(fit_growth(points)['quality']['status'],'PASS')
        points[0]['n']=1;points[0]['cv']=None
        self.assertEqual(fit_growth(points)['quality']['status'],'INCONCLUSIVE')

    def test_new_trace_sections_share_view_and_evidence(self):
        from test_views import ViewTests,BASELINE
        from cppperflab.storage import write_json
        from cppperflab.views import build_view,query_view
        with tempfile.TemporaryDirectory() as temp:
            store,record=ViewTests().setup_run(temp)
            record['call_edges']=[dict(caller='main',callee='dot',calls=3,evidence='raw.txt')]
            record['allocation_sites']=[dict(frames=['main','allocate'],allocations=4,evidence='raw.txt')]
            write_json(store.record_path(BASELINE),record)
            view=build_view(store,BASELINE)
            self.assertEqual(query_view(view,'call_edges',symbol='dot')['data'][0]['calls'],3)
            self.assertEqual(query_view(view,'allocation_sites',symbol='allocate')['data'][0]['allocations'],4)
            self.assertEqual(query_view(view,'call_edges')['data'][0]['evidence_id'],BASELINE+':raw.txt')

    def test_secondary_growth_reuses_runs_and_preserves_primary_results(self):
        import sys
        from unittest.mock import patch
        from test_core import request_template
        from cppperflab.studies import study
        from cppperflab.storage import Store
        from cppperflab.protocol import metric
        from cppperflab.lab import summarize
        from cppperflab.views import study_view
        with tempfile.TemporaryDirectory() as temp:
            template=request_template(temp)
            spec=dict(name='two metrics',axis=dict(parameter='n',values=[1,2,4,8],argument_index=2),
                      variants=[dict(id='base',executable=sys.executable,revision='r',artifacts=[])],budget=4,seed=1,
                      metric='time.wall',secondary_metrics=['memory.heap_peak_live'])
            def run(request,store):
                n=request['workload']['parameters']['n']
                samples=[{'index':i,'metrics':[metric('time.wall',n*n,'seconds','native-process-timing','scope','raw'),
                           metric('memory.heap_peak_live',n*8,'bytes','dhat-heap-instrumentation','scope','raw')]} for i in range(7)]
                return dict(run_id=str(n),status='completed',samples=samples,summary=summarize(samples))
            with patch('cppperflab.studies.load_study',return_value=(spec,template)),patch('cppperflab.studies.run',side_effect=run) as calls:
                report=study(Path(temp)/'unused',Store(Path(temp)/'store'))
            self.assertEqual(calls.call_count,4)
            self.assertEqual(report['status'],'completed')
            self.assertEqual(report['growth']['base']['doubling_ratios'][0]['value'],4)
            self.assertEqual(report['secondary_growth']['memory.heap_peak_live']['base']['doubling_ratios'][0]['value'],2)
            self.assertEqual(len(study_view(report)['growth']),2)

    def test_dhat_incomplete_native_output_is_a_parse_failure(self):
        for raw in ({},[],{**self.dhat(),'pps':[{'tb':1}]}):
            with self.assertRaises(LabError) as caught:self.parse(parse_dhat,json.dumps(raw))
            self.assertEqual(caught.exception.category,'parse_failed')

    def test_benchmark_counter_normalization_is_comparable(self):
        from test_core import comparison_result
        from cppperflab.comparison import compare
        a=comparison_result('a');b=comparison_result('b')
        row=self.parse(parse_google_benchmark,self.benchmark(),counters={'useful_flops':'flop_count'})['samples'][0]
        a['samples']=[{'index':i,'metrics':copy.deepcopy(row)} for i in range(7)]
        b['samples']=copy.deepcopy(a['samples'])
        self.assertTrue(compare(a,b,'kernel.useful_flop_rate')['comparable'])
        for sample in b['samples']:
            for m in sample['metrics']:
                if m['name']=='kernel.useful_flop_rate':m['evidence_state']='Modeled'
        with self.assertRaises(LabError):compare(a,b,'kernel.useful_flop_rate')
