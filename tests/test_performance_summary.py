"""Independent metric conclusions, paired-counter provenance and saved reports."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from cppperflab.comparison import compare
from cppperflab.errors import LabError
from cppperflab.html_report import render_html
from cppperflab.lab import summarize
from cppperflab.performance_summary import derive_ipc, summarize_performance
from cppperflab.storage import write_json
from cppperflab.views import build_view, create_summary, query_view
import test_views as fixtures

BASELINE, CANDIDATE = fixtures.BASELINE, fixtures.CANDIDATE


class PerformanceSummaryTests(unittest.TestCase):
    def counter_run(self, source, instructions, cycles, wall):
        run = copy.deepcopy(source)
        run['samples'] = []
        for i, (inst, cyc, elapsed) in enumerate(zip(instructions, cycles, wall)):
            counters = []
            for name, value, unit, method in [('cpu.instructions',inst,'count','hardware-counter'),
                                             ('cpu.cycles',cyc,'count','hardware-counter'),
                                             ('time.wall',elapsed,'seconds','native-process-timing')]:
                counters.append(dict(name=name,value=value,unit=unit,method=method,
                                     scope='same target scope',evidence='raw.txt',running_percent=100))
            run['samples'].append({'index':i,'metrics':counters})
        run['summary'] = summarize(run['samples'])
        return run

    def test_instruction_reduction_is_independent_of_noisy_time_and_lower_ipc(self):
        with tempfile.TemporaryDirectory() as temp:
            fixture = fixtures.ViewTests()
            _, base = fixture.setup_run(temp)
            _, candidate = fixture.setup_run(temp,CANDIDATE)
            base = self.counter_run(base,[1000]*9,[500]*9,[1]*9)
            candidate = self.counter_run(candidate,[500]*9,[1000]*9,[.2,2,.4,3,.5,4,.6,5,.7])
            comparisons = [compare(base,candidate,name) for name in ['cpu.instructions','time.wall']]
            result = summarize_performance([base,candidate],comparisons)
            findings = {f['metric']:f for f in result['findings']}
            self.assertEqual(findings['cpu.instructions']['outcome'],'observed_work_reduction')
            self.assertEqual(findings['time.wall']['outcome'],'inconclusive')
            self.assertEqual([m['summary']['median'] for m in result['derived_metrics']],[2,.5])
            self.assertIsNone(result['overall_score'])
            self.assertFalse(result['optimization_accepted'])

    def test_ipc_requires_paired_full_coverage_same_scope_counters(self):
        with tempfile.TemporaryDirectory() as temp:
            _, raw = fixtures.ViewTests().setup_run(temp)
            run = self.counter_run(raw,[1000]*7,[500]*7,[1]*7)
            self.assertEqual(derive_ipc(run)['samples'][0]['sources'][1]['value'],500)
            for change in ('scope','running_percent','zero','unpaired'):
                candidate = copy.deepcopy(run)
                for sample in candidate['samples']:
                    if change == 'scope':sample['metrics'][1]['scope']='other region'
                    if change == 'running_percent':sample['metrics'][1]['running_percent']=20
                    if change == 'zero':sample['metrics'][1]['value']=0
                    if change == 'unpaired':sample['metrics'].pop(1)
                self.assertIsNone(derive_ipc(candidate),change)

    def test_missing_simd_and_numerical_metrics_are_not_inferred_from_oracle(self):
        with tempfile.TemporaryDirectory() as temp:
            _, run = fixtures.ViewTests().setup_run(temp)
            report = summarize_performance([run])
            dims = {d['id']:d for d in report['dimensions']}
            self.assertEqual(dims['instruction_quality']['coverage'],'not_collected')
            self.assertIn('simd.lane_utilization',dims['instruction_quality']['missing_metrics'])
            self.assertIn('numerical.relative_error',dims['correctness']['missing_metrics'])
            self.assertEqual(dims['correctness']['observations'][0]['correctness']['status'],'passed')

    def test_saved_summary_reuses_run_ids_and_html_has_identical_json(self):
        with tempfile.TemporaryDirectory() as temp:
            fixture = fixtures.ViewTests()
            store, base = fixture.setup_run(temp)
            _, candidate = fixture.setup_run(temp,CANDIDATE)
            result = compare(base,candidate)
            result['comparison_id']='20261014T120000-000000000003'
            path = store.record_path(result['comparison_id'],'comparison')
            path.parent.mkdir(parents=True);write_json(path,result)
            view = create_summary(store,[],[result['comparison_id']])
            record = store.read(view['record_id'],'summary')
            self.assertEqual(record['run_ids'],[BASELINE,CANDIDATE])
            self.assertNotIn('samples',record)
            loaded = build_view(store,view['record_id'],'summary')
            self.assertEqual(loaded,view)
            query = query_view(view,'summary')
            self.assertEqual(query['data'],view['summary'])
            document = render_html(view,store,Path(temp)/'summary.html')
            parser = fixtures.SnapshotParser();parser.feed(document)
            self.assertEqual(json.loads(parser.snapshot),view)
            self.assertIn('不计算总分',document)

    def test_failed_run_preserves_error_and_incomplete_run_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            store, run = fixtures.ViewTests().setup_run(temp)
            run.update(status='failed',samples=[],summary=[],
                       error={'category':'correctness_failed','message':'wrong answer','exit_code':5})
            run['correctness']['status']='failed'
            write_json(store.record_path(BASELINE),run)
            result=create_summary(store,[BASELINE],[])
            self.assertEqual(result['status'],'failed')
            self.assertEqual(result['error']['exit_code'],5)
            run.update(status='running',error=None)
            write_json(store.record_path(BASELINE),run)
            with self.assertRaises(LabError):create_summary(store,[BASELINE],[])
            with self.assertRaises(LabError):create_summary(store,[],[])
