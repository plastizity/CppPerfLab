import copy
import json
from pathlib import Path
import tempfile
import unittest

from cppperflab.adapters import REGISTRY, parse_google_benchmark
from cppperflab.errors import LabError
from cppperflab.metrics import catalog
from cppperflab.studies import strong_scaling, load_study
from test_core import comparison_result, request_template


class ScienceTests(unittest.TestCase):
    def parse(self, seconds=.2):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'benchmark.json'
            p.write_text(json.dumps({'benchmarks':[dict(name='Pipeline',iterations=100,time_unit='s',real_time=1,cpu_time=1,phase=seconds,copies=4096)]}))
            return parse_google_benchmark(p,{'copied_bytes':'copies'},{'name':'convert','seconds_counter':'phase'})

    def test_stage_scope_units_evidence_and_counter(self):
        m={v['name']:v for v in self.parse()['samples'][0]}
        self.assertEqual(m['pipeline.stage_wall']['value'],.2)
        self.assertEqual(m['pipeline.stage_fraction']['value'],.2)
        self.assertEqual(m['pipeline.stage_wall']['unit'],'seconds')
        self.assertIn('stage=convert',m['pipeline.stage_wall']['scope'])
        self.assertEqual(m['pipeline.stage_wall']['evidence_state'],'Measured')
        self.assertEqual(m['memory.copied_bytes']['value'],4096)
        self.assertEqual(catalog(REGISTRY,metric_id='pipeline.stage_wall')['metrics'][0]['providers'],['google-benchmark'])
        self.assertEqual(catalog(REGISTRY,metric_id='parallel.speedup')['metrics'][0]['collection_method'],'study')

    def test_bad_stage_counters_rejected(self):
        for seconds in (-1,2,None,True):
            with self.subTest(seconds=seconds), self.assertRaises(LabError):self.parse(seconds)
        with self.assertRaises(LabError):
            REGISTRY['google-benchmark'].validate({'filter':'Pipeline','stage':{'name':'copy','seconds_counter':'shared'},'counters':{'copied_bytes':'shared'}})
        for stage in ({'name':'x'},{'name':'x','seconds_counter':'real_time'}, {'name':'bad/name','seconds_counter':'phase'}):
            with self.assertRaises(LabError):REGISTRY['google-benchmark'].validate({'filter':'Pipeline','stage':stage})

    def scaling(self):
        spec={'axis':{'parameter':'workers','argument_index':1,'values':[1,2]},'metric':'time.wall','constraints':'fixed n'}
        runs={}
        for p,t in ((1,1),(2,.6)):
            r=comparison_result(str(p),values=[t]*7)
            r['identity']['artifacts']={'/bin/x':'samehash'}
            r['request']['workload']['parameters']={'workers':p,'n':100}
            r['request']['target']['argv']=['/bin/x',str(p),'100']
            runs[('v',p)]=r
        return runs,spec

    def test_strong_scaling_reuses_comparison(self):
        runs,spec=self.scaling();r=strong_scaling(runs,'v',spec)
        self.assertEqual(r['points'][0]['speedup_interval_95_percent'],[1,1])
        row=r['points'][1]
        self.assertAlmostEqual(row['speedup'],1/.6)
        self.assertAlmostEqual(row['efficiency'],1/1.2)
        self.assertEqual(row['measurement_status'],'PASS')
        self.assertEqual(row['comparison']['treatment']['varying_parameter'],'workers')
        self.assertEqual(runs[('v',2)]['request']['workload']['parameters']['workers'],2)

    def test_reject_changed_problem_or_other_argument(self):
        for change in ('problem','argv','correctness','artifact'):
            runs,spec=self.scaling()
            if change=='artifact':runs[('v',2)]['identity']['artifacts']['/bin/x']='changed'
            if change=='problem':runs[('v',2)]['request']['workload']['parameters']['n']=200
            if change=='argv':runs[('v',2)]['request']['target']['argv'][2]='200'
            if change=='correctness':runs[('v',2)]['correctness']['status']='failed'
            with self.assertRaises(LabError):strong_scaling(runs,'v',spec)

    def test_noisy_points_stay_inconclusive(self):
        runs,spec=self.scaling();runs[('v',2)]=copy.deepcopy(runs[('v',2)])
        runs[('v',2)]['samples']=runs[('v',2)]['samples'][:2]
        self.assertEqual(strong_scaling(runs,'v',spec)['points'][1]['measurement_status'],'INCONCLUSIVE')

    def test_study_mode_boundary_and_legacy_default(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            (root/'template.json').write_text(json.dumps(request_template(root)))
            spec=dict(schema_version=1,name='sweep',template='template.json',axis=dict(parameter='workers',values=[1,2],argument_index=1),
                variants=[dict(id='v',executable='/bin/true',revision='r',artifacts=[])],budget=2,seed=1,metric='time.wall',constraints='fixed n')
            path=root/'study.json'
            def load():
                path.write_text(json.dumps(spec));return load_study(path)
            self.assertNotIn('analysis',load()[0])
            spec['analysis']='strong_scaling'
            self.assertEqual(load()[0]['analysis'],'strong_scaling')
            for axis,metric in (([2,4],'time.wall'),([1,2.5],'time.wall'),([1,2],'cpu.instructions')):
                spec['axis']['values']=axis;spec['metric']=metric
                with self.assertRaises(LabError):load()
