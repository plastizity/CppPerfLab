"""Public versioned claim/state/workflow input contracts; no measurements."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from cppperflab.contracts import validate_claim, validate_execution_state, validate_workflow
from cppperflab.errors import LabError


def claim():
    return {'schema_version': 1, 'hypothesis': 'Reuse reduces task cost',
            'kind': 'task_cost', 'applies_to': 'declared input and runtime',
            'allowed_changes': ['implementation'], 'metric': 'time.wall',
            'conditions': [{'id': 'correct', 'description': 'Declared oracle passes',
                            'source': 'correctness', 'required': True}]}


def state():
    return {'schema_version': 1, 'scope': 'measurement_sample', 'process': 'fresh_process',
            'object_state': 'Consumer initial state', 'cache': 'uncontrolled',
            'external_state': 'Consumer-controlled state file', 'required': True}


def workflow():
    return {'schema_version': 1, 'phase': 'exploration', 'source_study_ids': [],
            'selection_reason': 'Run all selected candidates'}


class ContractValidationTests(unittest.TestCase):
    def test_claim_preserves_scope_and_only_declared_changes(self):
        data=claim();data.update(metric_scope='whole task',allowed_changes=['implementation','reuse'])
        data['conditions'].append({'id':'work', 'description':'same logical entities',
            'source':'work_metric', 'required':False, 'metric':'algorithm.elements',
            'metric_scope':'declared region'})
        self.assertEqual(validate_claim(data),data)
        for kind in ('task_cost','mechanism_hypothesis'):
            data['kind']=kind
            self.assertEqual(validate_claim(data)['kind'],kind)

    def test_unit_efficiency_requires_a_required_work_metric(self):
        data=claim();data['kind']='unit_efficiency'
        with self.assertRaises(LabError):validate_claim(data)
        item={'id':'work','description':'units of work','source':'work_metric',
              'required':False,'metric':'algorithm.operations'}
        data['conditions'].append(item)
        with self.assertRaises(LabError):validate_claim(data)
        item['required']=True
        self.assertEqual(validate_claim(data)['conditions'][-1]['metric'],'algorithm.operations')
        del item['metric']
        with self.assertRaises(LabError):validate_claim(data)

    def test_claim_rejects_ambiguous_conditions_and_unknown_fields(self):
        original=claim()
        cases=[]
        changed=copy.deepcopy(original);changed['unknown']='field';cases.append(changed)
        changed=copy.deepcopy(original);changed['allowed_changes']=['implementation','implementation'];cases.append(changed)
        changed=copy.deepcopy(original);changed['conditions'].append(copy.deepcopy(changed['conditions'][0]));cases.append(changed)
        changed=copy.deepcopy(original);changed['conditions'][0]['required']=1;cases.append(changed)
        changed=copy.deepcopy(original);changed['conditions'][0]['metric']='time.wall';cases.append(changed)
        changed=copy.deepcopy(original);changed['conditions'][0]['source']='imagined';cases.append(changed)
        changed=copy.deepcopy(original);changed['metric_scope']='';cases.append(changed)
        for data in cases:
            with self.subTest(data=data),self.assertRaises(LabError):validate_claim(data)

    def test_state_actions_are_resolved_without_execution(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);script=root/'state-control'
            script.write_text('#!/bin/sh\nexit 0\n');script.chmod(0o755)
            data=state();data['setup']={'argv':['./state-control','$(literal)']}
            data['verify']={'argv':['./state-control','--verify']}
            data['cleanup']={'argv':['./state-control','--cleanup']}
            with mock.patch('subprocess.Popen', side_effect=AssertionError('Executed command')):
                result=validate_execution_state(data,root)
            self.assertEqual(result['setup']['argv'],[str(script),'$(literal)'])
            self.assertEqual(result['verify']['argv'][0],str(script))
            self.assertEqual(result['cleanup']['argv'][0],str(script))

    def test_state_declaration_does_not_require_a_verifier(self):
        data=state();data.update(scope='measurement_invocation',cache='cold')
        result=validate_execution_state(data,Path('.'))
        self.assertTrue(result['required'])
        self.assertNotIn('verify',result)
        self.assertNotIn('status',result)

    def test_state_rejects_invalid_action_scope_and_types(self):
        original=state();cases=[]
        changed=copy.deepcopy(original);changed['scope']='per_process';cases.append(changed)
        changed=copy.deepcopy(original);changed['required']='yes';cases.append(changed)
        changed=copy.deepcopy(original);changed['cache']='unknown';cases.append(changed)
        changed=copy.deepcopy(original);changed['reset']={'argv':[]};cases.append(changed)
        changed=copy.deepcopy(original);changed['verify']={'argv':['/bin/true','a\x00b']};cases.append(changed)
        changed=copy.deepcopy(original);changed['cleanup']={'argv':['/bin/true'],'extra':0};cases.append(changed)
        changed=copy.deepcopy(original);changed['setup']={'argv':['./absent-executable']};cases.append(changed)
        for data in cases:
            with self.subTest(data=data),self.assertRaises(LabError):validate_execution_state(data,Path('/tmp'))

    def test_workflow_requires_explicit_distinct_sources_for_later_phases(self):
        data=workflow()
        self.assertEqual(validate_workflow(data)['phase'],'exploration')
        data['selected_run_id']='20261027T120000-000000000001'
        with self.assertRaises(LabError):validate_workflow(data)
        del data['selected_run_id']
        data['phase']='calibration'
        self.assertEqual(validate_workflow(data)['source_study_ids'],[])
        data['selected_run_id']='20261027T120000-000000000001'
        with self.assertRaises(LabError):validate_workflow(data)
        del data['selected_run_id']
        for phase in ('confirmation','acceptance'):
            data['phase']=phase
            with self.subTest(phase=phase),self.assertRaises(LabError):validate_workflow(data)
            data['source_study_ids']=['20261027T120000-000000000001']
            with self.subTest(phase=phase),self.assertRaises(LabError):validate_workflow(data)
            data['selected_run_id']='not-a-run-id'
            with self.subTest(phase=phase),self.assertRaises(LabError):validate_workflow(data)
            data['selected_run_id']='20261027T120000-000000000002'
            self.assertEqual(validate_workflow(data)['phase'],phase)
            data['source_study_ids']=[]
            del data['selected_run_id']
        data['source_study_ids']=['a','a']
        with self.assertRaises(LabError):validate_workflow(data)

    def test_contracts_reject_unsupported_versions_including_boolean(self):
        for validator, factory in ((validate_claim,claim),
                                   (lambda value:validate_execution_state(value,Path('.')),state),
                                   (validate_workflow,workflow)):
            for version in (2,True,'1'):
                data=factory();data['schema_version']=version
                with self.subTest(validator=validator,version=version),self.assertRaises(LabError) as caught:
                    validator(data)
                self.assertEqual(caught.exception.category,'unsupported_schema')

    def test_schema_resources_expose_matching_public_fields(self):
        root=Path(__file__).resolve().parents[1]/'src/cppperflab/schemas'
        for name,data in [('claim',claim()),('execution-state',state()),('workflow',workflow())]:
            schema=json.loads((root/(name+'.schema.json')).read_text())
            self.assertEqual(schema['$id'],'urn:cppperflab:'+name+':1')
            self.assertEqual(set(schema['required']),set(data))
            self.assertFalse(schema['additionalProperties'])


if __name__=='__main__':
    unittest.main()
