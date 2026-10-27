"""Claim and state validity projects existing evidence; fixtures are not measurements."""
import copy
import unittest

from cppperflab.protocol import metric
from cppperflab.validity import (
    apply_validity, evaluate_comparison_validity, evaluate_paired_validity,
    evaluate_run_validity,
)
from test_core import comparison_result


def claim(kind='task_cost', conditions=()):
    return {'schema_version':1, 'hypothesis':'less total time', 'kind':kind,
            'applies_to':'fixed representative task', 'allowed_changes':['implementation'],
            'metric':'time.wall', 'conditions':list(conditions)}


def condition(source, *, required=True, id=None, **extra):
    return {'id':id or source, 'description':'consumer defined', 'source':source,
            'required':required, **extra}


def runs(*, kind='task_cost', conditions=()):
    a=comparison_result('a');b=comparison_result('b')
    for run in (a,b):
        run['request']['claim']=claim(kind,conditions)
        for i,sample in enumerate(run['samples']):
            sample['index']=i
            sample['metrics'][0]['evidence']=f'measurement-{i:03d}/timing.txt'
        run['correctness']['evidence']='correctness/execution.json'
    return a,b


def set_work(run, values, *, state='Measured', scope='same target scope', unit='count'):
    for sample,value in zip(run['samples'],values):
        sample['metrics'].append(metric('algorithm.operations',value,unit,'software-counter',scope,
                                        'work.txt',evidence_state=state))


def state_contract(*, scope='measurement_sample', required=True):
    return {'schema_version':1,'scope':scope,'process':'fresh_process',
            'object_state':'consumer-defined','cache':'uncontrolled','external_state':'prepared',
            'required':required,'verify':{'argv':['/bin/verify']}}


def state_entries(count, *, verifier='PASS', action_status='PASS'):
    return [{'schema_version':1, 'scope':'measurement_sample', 'measurement_group':i,
             'status':action_status, 'verification':None if verifier is None else
                {'schema_version':1,'status':verifier,'description':'explicit observed fixture'},
             'actions':[{'name':'verify','status':'PASS'}], 'evidence_refs':[f'execution-state-{i:03d}/verify/stdout.txt'],
             'limitations':[]} for i in range(count)]


class ValidityTests(unittest.TestCase):
    def test_legacy_is_not_retroactively_declared_or_changed(self):
        a=comparison_result('a');b=comparison_result('b');original=copy.deepcopy(a)
        result=evaluate_comparison_validity(a,b,'time.wall')
        self.assertEqual(result['status'],'NOT_CONFIGURED')
        self.assertEqual(result['conditions'],[])
        self.assertEqual(a,original)

    def test_total_cost_accepts_changed_internal_work_when_not_a_required_condition(self):
        a,b=runs();set_work(a,[100]*7);set_work(b,[40]*7)
        result=evaluate_comparison_validity(a,b,'time.wall')
        self.assertEqual(result['status'],'PASS')
        self.assertEqual(result['claim_kind'],'task_cost')
        self.assertTrue(any('not proof' in s for s in result['limitations']))
        self.assertEqual(evaluate_run_validity(a)['status'],'PASS')

    def test_required_declaration_and_metric_mismatch_are_inconclusive(self):
        a,b=runs(conditions=[condition('declaration')])
        result=evaluate_comparison_validity(a,b,'time.wall')
        self.assertEqual(result['status'],'INCONCLUSIVE')
        item=next(row for row in result['conditions'] if row['origin']=='declared')
        self.assertEqual(item['status'],'INCONCLUSIVE')
        a,b=runs()
        for run in (a,b):
            for sample in run['samples']:
                sample['metrics'].append(metric('time.cpu',1,'seconds','software-counter','cpu scope','cpu.txt'))
        result=evaluate_comparison_validity(a,b,'time.cpu')
        self.assertEqual(result['status'],'PASS')
        self.assertFalse(result['claim_applicable'])
        mismatch=next(c for c in result['conditions'] if c['id']=='comparison_metric')
        self.assertEqual(mismatch['status'],'INCONCLUSIVE')
        self.assertFalse(mismatch['required'])

    def test_secondary_work_comparison_has_valid_evidence_without_claim_support(self):
        a,b=runs();set_work(a,[100]*7);set_work(b,[80]*7)
        v=evaluate_comparison_validity(a,b,'algorithm.operations')
        self.assertEqual(v['status'],'PASS')
        self.assertFalse(v['claim_applicable'])
        result={'acceptance':{'status':'PASS','reason':'informational metric'},
                'classification':'observed_improvement','normalized_cost':{'value':.8}}
        apply_validity(result,v)
        self.assertEqual(result['claim_acceptance']['status'],'INCONCLUSIVE')
        self.assertEqual(result['acceptance']['status'],'PASS')
        self.assertEqual(result['normalized_cost']['value'],.8)

    def test_secondary_evidence_does_not_masquerade_as_primary(self):
        a,b=runs();set_work(a,[100]*7);set_work(b,[80]*7)
        for run in (a,b):
            for sample in run['samples']:
                sample['metrics']=[m for m in sample['metrics'] if m['name']!='time.wall']
        result=evaluate_comparison_validity(a,b,'algorithm.operations')
        self.assertEqual(result['status'],'PASS')
        self.assertFalse(result['claim_applicable'])
        self.assertIn('auxiliary_metric',{row['id'] for row in result['conditions']})
        self.assertNotIn('primary_metric',{row['id'] for row in result['conditions']})

    def test_secondary_metric_does_not_hide_required_state_failure(self):
        a,b=runs(conditions=[condition('execution_state')]);set_work(a,[100]*7);set_work(b,[80]*7)
        for run in (a,b):
            run['request']['measurement']['tool']='perf-stat';run['tool']['id']='perf-stat'
            run['request']['execution_state']=state_contract()
            run['execution_state_evidence']=state_entries(7)
        b['execution_state_evidence'][-1]['verification']['status']='FAIL'
        v=evaluate_comparison_validity(a,b,'algorithm.operations')
        self.assertFalse(v['claim_applicable'])
        self.assertEqual(v['status'],'FAIL')
        result=apply_validity({'acceptance':{'status':'PASS'}},v)
        self.assertEqual(result['claim_acceptance']['status'],'FAIL')

    def test_unit_efficiency_requires_equal_measured_work_and_single_run_cannot_prove_equality(self):
        c=condition('work_metric',metric='algorithm.operations')
        a,b=runs(kind='unit_efficiency',conditions=[c]);set_work(a,[10]*7);set_work(b,[10]*7)
        self.assertEqual(evaluate_run_validity(a)['status'],'INCONCLUSIVE')
        result=evaluate_comparison_validity(a,b,'time.wall')
        self.assertEqual(result['status'],'PASS')
        self.assertEqual(next(c for c in result['conditions'] if c['id']=='work_metric')['status'],'PASS')
        set_work_change=copy.deepcopy(b)
        for sample in set_work_change['samples']:sample['metrics'][-1]['value']=9
        self.assertEqual(evaluate_comparison_validity(a,set_work_change,'time.wall')['status'],'FAIL')
        varied=copy.deepcopy(b);varied['samples'][0]['metrics'][-1]['value']=9
        self.assertEqual(evaluate_comparison_validity(a,varied,'time.wall')['status'],'INCONCLUSIVE')

    def test_unit_efficiency_rejects_missing_model_and_scope_mismatch(self):
        c=condition('work_metric',metric='algorithm.operations')
        a,b=runs(kind='unit_efficiency',conditions=[c]);set_work(a,[10]*7)
        self.assertEqual(evaluate_comparison_validity(a,b,'time.wall')['status'],'INCONCLUSIVE')
        set_work(b,[10]*7,state='Modeled')
        self.assertEqual(evaluate_comparison_validity(a,b,'time.wall')['status'],'INCONCLUSIVE')
        for sample in b['samples']:
            sample['metrics'][-1]['evidence_state']='Measured'
            sample['metrics'][-1]['scope']='different scope'
        self.assertEqual(evaluate_comparison_validity(a,b,'time.wall')['status'],'INCONCLUSIVE')

    def test_unit_efficiency_rejects_primary_and_work_scope_mismatch(self):
        c=condition('work_metric',metric='algorithm.operations')
        a,b=runs(kind='unit_efficiency',conditions=[c]);set_work(a,[10]*7,scope='different window');set_work(b,[10]*7,scope='different window')
        result=evaluate_comparison_validity(a,b,'time.wall')
        self.assertEqual(result['status'],'INCONCLUSIVE')
        self.assertIn('sample scope',next(c for c in result['conditions'] if c['id']=='work_metric')['reason'])

    def test_time_ratio_and_unknown_units_are_not_work_counts(self):
        c=condition('work_metric',metric='algorithm.operations')
        for unit in ('seconds','ns','ratio','FLOP/s','items'):
            with self.subTest(unit=unit):
                a,b=runs(kind='unit_efficiency',conditions=[c])
                set_work(a,[10]*7,unit=unit);set_work(b,[10]*7,unit=unit)
                result=evaluate_comparison_validity(a,b,'time.wall')
                self.assertEqual(result['status'],'INCONCLUSIVE')
                self.assertIn('work count',next(c for c in result['conditions'] if c['id']=='work_metric')['reason'])

    def test_phase_unit_work_uses_same_phase_and_report(self):
        phase='fixture; phase=["operation","invoke"]'
        c=condition('work_metric',metric='phase.work',metric_scope=phase+'; counter="items"')
        a,b=runs(kind='unit_efficiency',conditions=[c])
        for run in (a,b):
            run['request']['claim']['metric']='phase.inclusive_time'
            run['request']['claim']['metric_scope']=phase
            for sample in run['samples']:
                sample['metrics'].extend([
                    metric('phase.inclusive_time',1,'seconds','target-phase-events',phase,'phase.json'),
                    metric('phase.work',10,'count','target-phase-events',phase+'; counter="items"','phase.json'),
                ])
        self.assertEqual(evaluate_comparison_validity(a,b,'phase.inclusive_time',phase)['status'],'PASS')
        b['samples'][-1]['metrics'][-1]['evidence']='different-report.json'
        self.assertEqual(evaluate_comparison_validity(a,b,'phase.inclusive_time',phase)['status'],'INCONCLUSIVE')

    def test_zero_work_is_valid_for_task_cost_but_not_unit_efficiency(self):
        c=condition('work_metric',metric='algorithm.operations')
        a,b=runs(conditions=[c]);set_work(a,[0]*7);set_work(b,[0]*7)
        self.assertEqual(evaluate_comparison_validity(a,b,'time.wall')['status'],'PASS')
        for run in (a,b):run['request']['claim']['kind']='unit_efficiency'
        self.assertEqual(evaluate_comparison_validity(a,b,'time.wall')['status'],'FAIL')
        self.assertEqual(evaluate_run_validity(a)['status'],'FAIL')

    def test_missing_required_counter_can_be_unsupported_without_fabricated_zero(self):
        c=condition('work_metric',metric='algorithm.operations')
        a,b=runs(kind='unit_efficiency',conditions=[c]);set_work(a,[10]*7)
        b['unavailable_observations']=[{'name':'algorithm.operations','evidence_state':'Unsupported',
                                       'value':None,'reason':'collector denied'}]
        result=evaluate_comparison_validity(a,b,'time.wall')
        self.assertEqual(result['status'],'UNSUPPORTED')
        self.assertTrue(all('value' not in row for row in result['conditions']))

    def test_state_requires_per_boundary_verifier_and_records_evidence(self):
        c=condition('execution_state');a,b=runs(conditions=[c])
        for run in (a,b):
            run['request']['measurement']['tool']='perf-stat'
            run['tool']['id']='perf-stat'
            run['request']['execution_state']=state_contract()
        self.assertEqual(evaluate_comparison_validity(a,b,'time.wall')['status'],'INCONCLUSIVE')
        for run in (a,b):run['execution_state_evidence']=state_entries(7,verifier=None)
        self.assertEqual(evaluate_comparison_validity(a,b,'time.wall')['status'],'INCONCLUSIVE')
        for run in (a,b):run['execution_state_evidence']=state_entries(7)
        report=evaluate_comparison_validity(a,b,'time.wall')
        self.assertEqual(report['status'],'PASS')
        item=next(x for x in report['conditions'] if x['id']=='execution_state' and x['origin']=='declared')
        self.assertIn('a:execution-state-000/verify/stdout.txt',item['evidence_refs'])
        b['execution_state_evidence'][-1]['verification']['status']='FAIL'
        self.assertEqual(evaluate_comparison_validity(a,b,'time.wall')['status'],'FAIL')

    def test_required_state_without_claim_is_still_a_gate(self):
        a=comparison_result('a');b=comparison_result('b')
        for run in (a,b):
            run['request']['measurement']['tool']='perf-stat'
            run['tool']['id']='perf-stat'
            run['request']['execution_state']=state_contract()
            for i,sample in enumerate(run['samples']):sample['index']=i
        report=evaluate_comparison_validity(a,b,'time.wall')
        self.assertEqual(report['assessment_scope'],'execution_state')
        self.assertEqual(report['status'],'INCONCLUSIVE')
        self.assertIsNone(report['claim_kind'])
        for run in (a,b):run['execution_state_evidence']=state_entries(7)
        report=evaluate_comparison_validity(a,b,'time.wall')
        self.assertEqual(report['status'],'PASS')
        self.assertEqual(apply_validity({'acceptance':{'status':'PASS'}},report)['claim_acceptance']['status'],'INCONCLUSIVE')

    def test_unavailable_batch_state_without_samples_is_unsupported(self):
        run=comparison_result('a')
        run['status']='failed';run['samples']=[]
        run['error']={'category':'unavailable'}
        run['correctness']['status']='not_run'
        run['request']['measurement']['tool']='hyperfine'
        run['request']['execution_state']=state_contract(scope='measurement_sample')
        result=evaluate_run_validity(run)
        self.assertEqual(result['assessment_scope'],'execution_state')
        self.assertEqual(result['status'],'UNSUPPORTED')
        self.assertEqual(next(c for c in result['conditions'] if c['id']=='execution')['status'],'UNSUPPORTED')

    def test_explicit_unsupported_primary_without_samples_is_unsupported(self):
        run,_=runs()
        run['status']='failed';run['samples']=[]
        run['error']={'category':'unavailable'}
        run['unavailable_observations']=[{'name':'time.wall','evidence_state':'Unsupported',
                                         'value':None,'reason':'event denied'}]
        result=evaluate_run_validity(run)
        self.assertEqual(result['status'],'UNSUPPORTED')
        self.assertEqual(next(c for c in result['conditions'] if c['id']=='primary_metric')['status'],'UNSUPPORTED')
        run['unavailable_observations']=[]
        self.assertEqual(evaluate_run_validity(run)['status'],'INCONCLUSIVE')

    def test_missing_correctness_is_not_masked_by_another_unsupported_run(self):
        a,b=runs()
        a['status']='failed';a['correctness']['status']='not_run';a['error']={'category':'target_failed'}
        b['status']='failed';b['correctness']['status']='not_run';b['error']={'category':'unavailable'}
        result=evaluate_comparison_validity(a,b,'time.wall')
        check=next(c for c in result['conditions'] if c['id']=='correctness' and c['origin']=='platform')
        self.assertEqual(check['status'],'INCONCLUSIVE')
        self.assertEqual(result['status'],'INCONCLUSIVE')

    def test_batch_invocation_scope_does_not_claim_per_sample_control(self):
        a,b=runs(conditions=[condition('execution_state')])
        for run in (a,b):
            run['request']['measurement']['tool']='hyperfine'
            run['request']['execution_state']=state_contract(scope='measurement_invocation')
            run['execution_state_evidence']=state_entries(1)
            run['execution_state_evidence'][0]['scope']='measurement_invocation'
        item=next(c for c in evaluate_comparison_validity(a,b,'time.wall')['conditions'] if c['origin']=='declared')
        self.assertEqual(item['status'],'PASS')
        self.assertIn('not each internal sample',item['reason'])
        for run in (a,b):run['request']['execution_state']['scope']='measurement_sample'
        self.assertEqual(evaluate_comparison_validity(a,b,'time.wall')['status'],'UNSUPPORTED')

    def test_state_scope_and_verifier_evidence_must_match_declaration(self):
        a,b=runs(conditions=[condition('execution_state')])
        for run in (a,b):
            run['request']['measurement']['tool']='perf-stat';run['tool']['id']='perf-stat'
            run['request']['execution_state']=state_contract()
            run['execution_state_evidence']=state_entries(7)
        b['execution_state_evidence'][0]['scope']='measurement_invocation'
        self.assertEqual(evaluate_comparison_validity(a,b,'time.wall')['status'],'INCONCLUSIVE')
        b['execution_state_evidence'][0]['scope']='measurement_sample'
        b['execution_state_evidence'][0]['actions']=[]
        self.assertEqual(evaluate_comparison_validity(a,b,'time.wall')['status'],'INCONCLUSIVE')

    def test_paired_validity_checks_every_later_member(self):
        c=condition('work_metric',metric='algorithm.operations')
        a,b=runs(kind='unit_efficiency',conditions=[c])
        a['samples']=a['samples'][:1];b['samples']=b['samples'][:1];set_work(a,[20]);set_work(b,[20])
        late=copy.deepcopy(b);late['run_id']='late';late['samples'][0]['metrics'][-1]['value']=19
        blocks=[{'members':[{'run':a},{'run':b}]},{'members':[{'run':a},{'run':late}]}]
        self.assertEqual(evaluate_paired_validity(blocks,'time.wall')['status'],'FAIL')

    def test_failed_condition_does_not_change_original_statistical_result(self):
        a,b=runs(conditions=[condition('declaration')]);v=evaluate_comparison_validity(a,b,'time.wall')
        result={'acceptance':{'status':'PASS','reason':'regression gate passed'},
                'classification':'observed_improvement','baseline_samples':[1], 'candidate_samples':[.5]}
        apply_validity(result,v)
        self.assertEqual(result['acceptance']['status'],'PASS')
        self.assertEqual(result['claim_acceptance']['status'],'INCONCLUSIVE')
        self.assertEqual(result['classification'],'observed_improvement')
        self.assertEqual(result['baseline_samples'],[1])

    def test_mechanism_is_hypothesis_even_if_measured_gates_pass(self):
        a,b=runs(kind='mechanism_hypothesis')
        v=evaluate_comparison_validity(a,b,'time.wall')
        self.assertEqual(v['status'],'PASS')
        result=apply_validity({'acceptance':{'status':'PASS'}},v)
        self.assertEqual(result['claim_acceptance']['status'],'INCONCLUSIVE')
        self.assertIn('hypothesis',result['claim_acceptance']['reason'])

    def test_optional_declaration_is_visible_without_failing_required_checks(self):
        a,b=runs(conditions=[condition('declaration',required=False)])
        result=evaluate_comparison_validity(a,b,'time.wall')
        self.assertEqual(result['status'],'PASS')
        self.assertEqual(next(c for c in result['conditions'] if c['origin']=='declared')['status'],'INCONCLUSIVE')

    def test_optional_state_gap_remains_visible(self):
        a,b=runs()
        for run in (a,b):run['request']['execution_state']=state_contract(required=False)
        result=evaluate_comparison_validity(a,b,'time.wall')
        state=next(c for c in result['conditions'] if c['id']=='execution_state')
        self.assertEqual(result['status'],'PASS')
        self.assertFalse(state['required'])
        self.assertEqual(state['status'],'INCONCLUSIVE')

    def test_declared_per_sample_validation_must_cover_samples(self):
        a,b=runs()
        for run in (a,b):run['request']['correctness']['sample_validation']={'argv':['/bin/verifier']}
        self.assertEqual(evaluate_comparison_validity(a,b,'time.wall')['status'],'INCONCLUSIVE')
        for run in (a,b):
            run['correctness']['sample_validation_status']='passed'
            run['solver_samples']=[{'status':'PASS'} for _ in run['samples']]
        self.assertEqual(evaluate_comparison_validity(a,b,'time.wall')['status'],'PASS')
        b['solver_samples'][-1]['status']='FAIL'
        self.assertEqual(evaluate_comparison_validity(a,b,'time.wall')['status'],'FAIL')


if __name__=='__main__':unittest.main()
