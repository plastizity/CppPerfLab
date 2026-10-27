"""Claim/state/workflow conclusions remain visible in both shared reports."""
import json
from pathlib import Path
import tempfile
import unittest

from cppperflab.agent_report import build_agent_report
from cppperflab.comparison import compare
from cppperflab.html_report import render_html
from cppperflab.storage import Store, write_json
from cppperflab.views import SECTIONS, build_view, query_view
from test_dual_report_cli import AgentSnapshotParser
from test_views import BASELINE, CANDIDATE, run_result


def validity(status, *, reason='consumer state was not verified', kind='task_cost'):
    return {'schema_version':1,'status':status,'assessment_scope':'claim','claim_kind':kind,
            'claim':{'kind':kind,'hypothesis':'test same workload','metric':'time.wall'},
            'conditions':[{'id':'state','origin':'declared','source':'execution_state',
                'required':True,'status':status,'reason':reason,'evidence_refs':[BASELINE+':state/verify/stdout.txt']}],
            'limitations':['Verification covers the declared condition only.']}


class ValidityReportingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.store=Store(self.root/'store')
        self.a=run_result(BASELINE,self.store.record_path(BASELINE).parent,[1.0]*7)
        self.b=run_result(CANDIDATE,self.store.record_path(CANDIDATE).parent,[1.0]*7)

    def save_run(self,run):
        write_json(self.store.record_path(run['run_id']),run)

    def save_parent(self,context,status,workflow_status):
        path=self.store.record_path(context['study_id'],'study');path.parent.mkdir(parents=True,exist_ok=True)
        workflow={key:context[key] for key in ('study_id','phase','freeze_digest','freeze_monotonic_ns','clock')}
        workflow.update(schema_version=1,status=workflow_status,source_study_ids=[],selected_run_id=None,
            freeze_path='workflow-freeze.json',limitations=['Provenance is not a performance verdict.'],optimization_accepted=False)
        write_json(path,{'schema_version':1,'study_id':context['study_id'],'status':status,'workflow':workflow})

    def test_legacy_record_is_unconfigured_and_null_workflow_is_queryable(self):
        self.save_run(self.a)
        view=build_view(self.store,BASELINE)
        report=build_agent_report(view,self.store.root)
        self.assertEqual(view['validity']['status'],'NOT_CONFIGURED')
        self.assertEqual(report['validity']['status'],'NOT_CONFIGURED')
        self.assertIsNone(report['workflow'])
        self.assertIsNone(query_view(view,'workflow')['data'])
        self.assertIsNone(report['assessment'])
        self.assertFalse(report['optimization_accepted'])

    def test_query_schema_describes_advertised_sections_and_null_workflow(self):
        schema=json.loads((Path(__file__).parents[1]/'src/cppperflab/schemas/query.schema.json').read_text())
        self.assertEqual(schema['properties']['section']['enum'],list(SECTIONS))
        self.assertEqual(schema['properties']['available_sections']['items']['enum'],list(SECTIONS))
        self.assertIn('null',schema['properties']['data']['type'])

    def test_nonpassing_validity_is_not_hidden_by_passing_comparison_or_limit(self):
        self.a['validity']=validity('PASS',reason='state verifier passed')
        self.b['validity']=validity('INCONCLUSIVE')
        self.save_run(self.a);self.save_run(self.b)
        result=compare(self.a,self.b)
        result.update(comparison_id='20261014T120000-000000000003',
            validity=validity('INCONCLUSIVE'),claim_acceptance={'status':'INCONCLUSIVE','reason':'state missing'})
        directory=self.store.record_path(result['comparison_id'],'comparison');directory.parent.mkdir(parents=True)
        write_json(directory,result)
        view=build_view(self.store,result['comparison_id'],'comparison')
        report=build_agent_report(view,self.store.root,limit=1)
        self.assertEqual(report['validity']['status'],'INCONCLUSIVE')
        self.assertEqual(report['comparisons']['items'][0]['validity']['conditions'][0]['reason'],'consumer state was not verified')
        issues=report['attention']['source_validity_issues']
        self.assertEqual([item['run_id'] for item in issues],[CANDIDATE])
        self.assertIn('state/verify/stdout.txt',issues[0]['validity']['conditions'][0]['evidence_refs'][0])
        html=render_html(view,self.store,self.root/'report.html',agent_report=report)
        snapshot=AgentSnapshotParser();snapshot.feed(html)
        self.assertEqual(json.loads(snapshot.snapshot),report)
        self.assertIn('契约有效性',html)
        self.assertIn('consumer state was not verified',html)
        self.assertIn('工程采纳',html)

    def test_later_failed_source_and_state_evidence_survive_detail_limit(self):
        self.a['execution_state_evidence']=[
            {'schema_version':1,'measurement_group':i,'scope':'measurement_sample','status':'PASS',
             'verification':{'status':'PASS','description':'checked by project'},'actions':[],
             'evidence_refs':[f'execution-state-{i:03d}/verify/stdout.txt'],'limitations':[]}
            for i in range(4)]
        self.a['execution_state_evidence'].append({'schema_version':1,'measurement_group':4,
            'scope':'measurement_sample','status':'FAIL','verification':{'status':'FAIL','description':'wrong state'},
            'actions':[{'name':'verify','status':'PASS','evidence_refs':['execution-state-004/verify/stdout.txt']}],
            'evidence_refs':['execution-state-004/verify/stdout.txt'],'limitations':[]})
        self.a['validity']=validity('FAIL',reason='later state failed')
        self.save_run(self.a)
        view=build_view(self.store,BASELINE)
        report=build_agent_report(view,self.store.root,1)
        records=report['execution_state_evidence']
        self.assertEqual((records['total'],records['returned'],records['omitted']),(5,2,3))
        self.assertEqual(records['items'][0]['measurement_group'],4)
        self.assertEqual(records['items'][0]['verification']['status'],'FAIL')
        self.assertEqual(report['attention']['source_validity_issues'][0]['validity']['status'],'FAIL')

    def test_unpassed_claim_is_not_truncated_after_passing_statistics(self):
        self.save_run(self.a)
        view=build_view(self.store,BASELINE)
        normal=compare(self.a,self.b)
        self.assertEqual(normal['acceptance']['status'],'PASS')
        self.assertEqual(normal['decisions']['improvement']['status'],'NOT_ESTABLISHED')
        late={**normal,'comparison_id':'late','validity':validity('INCONCLUSIVE'),
              'claim_acceptance':{'status':'INCONCLUSIVE','reason':'missing state evidence'}}
        view['comparisons']=[{**normal,'comparison_id':'first'},
                             {**normal,'comparison_id':'second'},late]
        report=build_agent_report(view,self.store.root,1)
        selected={row['comparison_id'] for row in report['comparisons']['items']}
        self.assertEqual(selected,{'first','late'})
        self.assertEqual(report['comparisons']['omitted'],1)

    def test_optional_unverified_condition_is_visible_when_required_gates_pass(self):
        result=validity('PASS',reason='optional state not checked')
        result['conditions'][0].update(required=False,status='INCONCLUSIVE')
        self.a['validity']=result
        self.save_run(self.a)
        report=build_agent_report(build_view(self.store,BASELINE),self.store.root)
        self.assertEqual(report['validity']['status'],'PASS')
        conditions=report['attention']['source_validity_issues'][0]['validity']['conditions']
        self.assertEqual(conditions[0]['status'],'INCONCLUSIVE')
        self.assertFalse(conditions[0]['required'])

    def test_study_workflow_is_provenance_not_performance_acceptance(self):
        self.a['workflow_context']={'study_id':'20261014T120000-000000000004',
            'phase':'confirmation','freeze_digest':'abcdef','freeze_monotonic_ns':100,
            'run_started_monotonic_ns':200,'clock':'CLOCK_MONOTONIC'}
        self.save_parent(self.a['workflow_context'],'running','RUNNING')
        self.save_run(self.a)
        view=build_view(self.store,BASELINE)
        report=build_agent_report(view,self.store.root)
        self.assertEqual(report['workflow']['phase'],'confirmation')
        self.assertEqual(report['workflow']['status'],'RUNNING')
        self.assertEqual(report['workflow']['parent_study_status'],'running')
        self.assertIsNone(report['assessment'])
        self.assertFalse(report['optimization_accepted'])
        self.assertEqual(query_view(view,'workflow')['data']['source_context'],self.a['workflow_context'])

    def test_child_comparison_preserves_failed_parent_workflow(self):
        self.save_run(self.a);self.save_run(self.b)
        result=compare(self.a,self.b)
        self.assertEqual(result['acceptance']['status'],'PASS')
        context={'study_id':'20261014T120000-000000000004','phase':'confirmation',
                 'freeze_digest':'abcdef','freeze_monotonic_ns':100,'clock':'CLOCK_MONOTONIC'}
        self.save_parent(context,'failed','FAIL')
        result.update(comparison_id='20261014T120000-000000000003',workflow_context=context)
        path=self.store.record_path(result['comparison_id'],'comparison');path.parent.mkdir(parents=True)
        write_json(path,result)
        view=build_view(self.store,result['comparison_id'],'comparison')
        report=build_agent_report(view,self.store.root)
        self.assertEqual(report['comparisons']['items'][0]['acceptance']['status'],'PASS')
        self.assertEqual(report['workflow']['phase'],'confirmation')
        self.assertEqual(report['workflow']['status'],'FAIL')
        self.assertEqual(report['workflow']['parent_study_status'],'failed')
        self.assertIn('does not establish',report['workflow']['scope_note'])
        self.assertFalse(report['optimization_accepted'])
        html=render_html(view,self.store,self.root/'report.html',agent_report=report)
        self.assertIn('父 Study 执行状态',html)
        self.assertIn('阶段记录验证',html)
        snapshot=AgentSnapshotParser();snapshot.feed(html)
        self.assertEqual(json.loads(snapshot.snapshot)['workflow']['status'],'FAIL')

    def test_workflow_pass_does_not_hide_source_regressions_or_authorize_adoption(self):
        self.save_run(self.a)
        view=build_view(self.store,BASELINE)
        view['workflow']={'schema_version':1,'study_id':'20261014T120000-000000000004',
            'phase':'acceptance','status':'PASS','source_study_ids':['20261014T120000-000000000005'],
            'selected_run_id':CANDIDATE,'freeze_path':'workflow-freeze.json','freeze_digest':'abcdef',
            'source_confirmations':[{'study_id':'20261014T120000-000000000005','comparisons':[
                {'case_id':'one','acceptance':{'status':'PASS'},'improvement':{'status':'NOT_ESTABLISHED'}},
                {'case_id':'important','acceptance':{'status':'FAIL'},'improvement':{'status':'SUPPORTED_REGRESSION'}}]}],
            'limitations':['Workflow status covers provenance, not performance.'],'optimization_accepted':False}
        report=build_agent_report(view,self.store.root,1)
        self.assertEqual(report['workflow']['status'],'PASS')
        cases=report['workflow']['source_confirmations'][0]['comparisons']
        self.assertEqual(len(cases),2)
        self.assertEqual(cases[1]['improvement']['status'],'SUPPORTED_REGRESSION')
        self.assertIsNone(report['assessment'])
        self.assertFalse(report['optimization_accepted'])
        html=render_html(view,self.store,self.root/'report.html',agent_report=report)
        self.assertIn('阶段记录验证',html)
        self.assertIn('SUPPORTED_REGRESSION',html)


if __name__=='__main__':
    unittest.main()
