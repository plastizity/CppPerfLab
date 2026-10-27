"""Parser and report fixtures, without launching resource helpers or targets."""
import copy
import json
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from cppperflab.agent_report import build_agent_report, _comparison
from cppperflab.comparison import compare
from cppperflab.errors import LabError
from cppperflab.html_report import data_movement_section, comparisons_section
from cppperflab.lab import summarize
from cppperflab.native_resource import SCOPE, parse_resource
from cppperflab.performance_summary import dimension, data_movement_projection, summarize_performance
from cppperflab.protocol import metric
from cppperflab.views import run_view, base_view, build_view, query_view
from test_views import BASELINE, CANDIDATE, run_result


class NativeResourceReportingTests(unittest.TestCase):
    def test_paired_view_exposes_zero_resource_sample_with_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            run=run_result(BASELINE,root/'run',[1.0])
            run['samples'][0]['metrics'].append(metric('os.major_page_faults',0,'count',
                'linux-wait4-rusage',SCOPE,'resource.json'))
            run['summary']=summarize(run['samples'])
            study={'study_id':'study-fixture','status':'completed','error':None,
                   'spec':{'analysis':'paired','name':'fixture','metric':'time.wall',
                           'resource_conditions':{'concurrent_runs':1}},
                   'runs':[{'run_id':BASELINE,'variant':'baseline','block_id':'block-0000','slot':0}],
                   'blocks':[],'comparisons':[],'execution_budget':{'limitations':[]}}
            store=mock.Mock()
            store.read.side_effect=lambda identifier,kind='run':study if kind=='study' else run
            with mock.patch('cppperflab.study_status.study_status',return_value=None), \
                 mock.patch('cppperflab.run_notes.notes',return_value={}):
                view=build_view(store,'study-fixture','study')
            row=query_view(view,'metrics',metric='os.major_page_faults')['data'][0]
            self.assertEqual((row['run_id'],row['variant'],row['block_id'],row['slot']),
                             (BASELINE,'baseline','block-0000',0))
            self.assertEqual(row['samples'][0]['value'],0)
            self.assertEqual(row['samples'][0]['evidence_id'],BASELINE+':resource.json')
            report=build_agent_report(view,root,limit=200)
            observed=next(row for row in report['metrics']['items'] if row['name']=='os.major_page_faults')
            self.assertEqual((observed['median'],observed['evidence_state']),(0,'Measured'))
            self.assertTrue(any(link['section']=='metrics' for link in report['follow_up']))

    def test_compact_comparison_preserves_all_paired_sources_for_both_outcomes(self):
        baseline=['baseline-1','baseline-2','baseline-3']
        candidate=['candidate-1','candidate-2','candidate-3']
        for comparable in (True,False):
            row={'metric':'os.major_page_faults','baseline_id':baseline[0],'candidate_id':candidate[0],
                 'baseline_run_ids':baseline,'candidate_run_ids':candidate,'comparable':comparable,
                 'comparison_gate':'informational'}
            projected=_comparison(row)
            self.assertEqual(projected['baseline_run_ids'],baseline)
            self.assertEqual(projected['candidate_run_ids'],candidate)
            self.assertEqual(projected['comparable'],comparable)

    def test_informational_incomparable_retains_gate_error_and_null_score(self):
        error={'category':'incomparable','message':'Ratio comparison requires positive finite samples',
               'exit_code':7,'details':{'metric':'memory.rss_peak','reason':'zero'}}
        comparison={'metric':'memory.rss_peak','baseline_id':BASELINE,'candidate_id':CANDIDATE,
                    'comparable':False,'comparison_gate':'informational','error':error,
                    'unit':None,'baseline_median':None,'candidate_median':None,'speedup':None,
                    'acceptance':{'status':'INCONCLUSIVE','reason':'zero denominator'}}
        summary=summarize_performance([], [comparison])
        finding=summary['findings'][0]
        self.assertEqual(finding['comparison_gate'],'informational')
        self.assertIsNone(finding['normalized_cost']['value'])
        self.assertEqual(finding['decisions']['comparability']['status'],'INCONCLUSIVE')
        section=data_movement_projection([], [comparison])
        row=section['columns']['outcome']['comparisons'][0]
        self.assertEqual((row['comparison_gate'],row['observed_change']),('informational','inconclusive'))
        self.assertEqual(row['error'],error)
        self.assertIsNone(row['speedup'])
        view=base_view('study',BASELINE,'completed',None)
        view.update(summary=summary,comparisons=[comparison],data_movement=section)
        report=build_agent_report(view,Path('/tmp'))
        actual=report['comparisons']['items'][0]
        self.assertEqual(actual['comparison_gate'],'informational')
        self.assertEqual(actual['error']['details'],error['details'])
        self.assertIsNone(actual['normalized_cost']['value'])
        self.assertEqual(actual['acceptance']['status'],'INCONCLUSIVE')
        self.assertIn('信息项，不作为收益通过条件',data_movement_section(view))
        document=comparisons_section(view)
        self.assertIn('信息项，不作为收益通过条件',document)
        self.assertIn('Ratio comparison requires positive finite samples',document)

    def test_rusage_projection_preserves_values_scope_and_zero(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            raw={'schema_version':1,'collector':'linux-wait4','version':1,'child_pid':123,
                 'wait_status':0,'exited':True,'exit_code':0,'signal':None,'exec_errno':None,
                 'rusage':{'max_rss_kib':2048,'user_time':{'seconds':1,'microseconds':250000},
                           'system_time':{'seconds':0,'microseconds':200000},
                           'minor_faults':12,'major_faults':0,'voluntary_context_switches':2,
                           'involuntary_context_switches':1,'filesystem_inputs':0,'filesystem_outputs':0}}
            path=root/'resource.json'
            path.write_text(json.dumps(raw))
            parsed=parse_resource(path)
            run=run_result(BASELINE,root/'baseline',[1.0])
            run['samples'][0]['metrics'].extend(parsed['metrics'])
            run['samples'][0]['metrics'].append(metric('memory.heap_peak_observed',1024,'bytes',
                'instrumented-snapshot','Massif heap snapshots','massif.txt'))
            run['summary']=summarize(run['samples'])
            view=run_view(run)
            rows={r['name']:r for r in view['observations']}
            self.assertEqual((rows['memory.rss_peak']['value'],rows['memory.rss_peak']['unit']),
                             (2048*1024,'bytes'))
            self.assertEqual((rows['os.major_page_faults']['value'],rows['os.major_page_faults']['evidence_state']),
                             (0,'Measured'))
            self.assertEqual(rows['time.cpu.user']['value'],1.25)
            self.assertEqual(rows['memory.rss_peak']['method'],'linux-wait4-rusage')
            self.assertEqual(rows['memory.rss_peak']['scope'],SCOPE)
            self.assertNotEqual(rows['memory.rss_peak']['scope'],rows['memory.heap_peak_observed']['scope'])
            self.assertEqual(dimension('memory.rss_peak'),'resources')
            self.assertEqual(dimension('os.minor_page_faults'),'execution')
            self.assertEqual(dimension('time.cpu.system'),'timing')
            outcome=view['data_movement']['columns']['outcome']['observations']
            rss=next(row for row in outcome if row['name']=='memory.rss_peak')
            self.assertEqual(rss['evidence_id'],BASELINE+':resource.json')
            self.assertNotIn('os.major_page_faults',{row['name'] for row in view['data_movement']['columns']['data_movement']['observations']})
            self.assertTrue(any('not heap usage or simultaneous process-tree RSS' in s for s in view['data_movement']['limitations']))
            agent=build_agent_report(view,root,limit=200)
            self.assertIn('memory.rss_peak',{row['name'] for row in agent['data_movement']['columns']['outcome']['observations']['items']})
            candidate=copy.deepcopy(run);candidate['run_id']=CANDIDATE
            resource=next(m for m in candidate['samples'][0]['metrics'] if m['name']=='memory.rss_peak')
            resource.update(method='instrumented-snapshot',scope='Massif heap snapshots')
            with self.assertRaises(LabError):compare(run,candidate,'memory.rss_peak')
            with self.assertRaises(LabError):compare(run,copy.deepcopy(run),'os.major_page_faults')

if __name__ == '__main__':
    unittest.main()
