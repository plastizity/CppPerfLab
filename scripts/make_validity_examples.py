#!/usr/bin/env python3.11
"""Generate standalone validity/decision-study configurations; no target execution or build."""
import argparse
import copy
import json
from pathlib import Path
import sys

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--blocks',type=int,default=3,help='Paired blocks; interface example defaults to 3, not a stability guarantee')
parser.add_argument('--size',type=int,default=2000000,help='Positive synthetic input size')
parser.add_argument('--rounds',type=int,default=4,help='Positive synthetic work repetitions')
parser.add_argument('--exploration',help='Existing completed exploration Study ID for confirmation')
parser.add_argument('--selected-run',help='Selected candidate Run ID in the exploration Study')
parser.add_argument('--calibration',help='Optional existing completed A/A calibration Study ID')
parser.add_argument('--confirmation',help='Existing completed confirmation Study ID for acceptance')
parser.add_argument('--confirmed-run',help='Candidate Run ID used by the confirmation comparison')
args=parser.parse_args()
if args.blocks<2 or args.size<1 or args.rounds<1:
    parser.error('blocks must be at least 2 and size/rounds must be positive')
if bool(args.exploration)!=bool(args.selected_run):
    parser.error('--exploration and --selected-run must be supplied together')
if bool(args.confirmation)!=bool(args.confirmed_run):
    parser.error('--confirmation and --confirmed-run must be supplied together')
if args.calibration and not (args.exploration or args.confirmation):
    parser.error('--calibration is a source of a confirmation or acceptance, not an implicit run')

root=Path(__file__).resolve().parents[1]
folder=root/'workloads';folder.mkdir(exist_ok=True)
state_folder=root/'.perflab/validity-example';state_folder.mkdir(parents=True,exist_ok=True)
state=state_folder/'state.txt'
binary=root/'build/profile/examples/cpp/validity_workload'
oracle=root/'examples/cpp/oracle_validity.py'
python=str(Path(sys.executable).resolve())
artifacts=[root/'examples/cpp/validity_workload.cpp',oracle,root/'include/cppperflab/phase.hpp']
compile_commands=root/'build/profile/compile_commands.json'
if compile_commands.is_file():artifacts.append(compile_commands)

# The mutable state file is an execution resource, not an immutable workload input.
# Each run's oracle establishes preflight state; its reset/verify execute after
# preflight and before each formal invocation. CPU cache state is not controlled.
state_contract=dict(schema_version=1,scope='measurement_sample',process='fresh_process',
    object_state='new synthetic target process for each measured invocation',cache='uncontrolled',
    external_state='synthetic file must contain ready before each target invocation',required=True,
    reset=dict(argv=[python,str(oracle),'establish',str(state),'synthetic-ready']),
    verify=dict(argv=[python,str(oracle),'verify',str(state),'synthetic-ready']))
claim=dict(schema_version=1,kind='task_cost',metric='time.wall',
    hypothesis='Reusing a repeated synthetic calculation may reduce process elapsed time.',
    applies_to='This declared synthetic size, rounds, state protocol, tool and execution environment.',
    allowed_changes=['target.argv[4] strategy selects repeated computation or reuse'],
    conditions=[dict(id='state',description='Consumer checks the synthetic state file before each invocation.',
                     source='execution_state',required=True),
                dict(id='visits',description='Recorded logical element visits; fewer visits are permitted.',
                     source='work_metric',required=False,metric='phase.work',
                     metric_scope='synthetic-validity-workload; phase=["compute"]; counter="visits"')])
request=dict(schema_version=1,name='synthetic-validity-baseline',
    workload=dict(id='synthetic-validity-task',parameters=dict(size=args.size,rounds=args.rounds),inputs=[]),
    target=dict(argv=[str(binary),str(state),str(args.size),str(args.rounds),'baseline','{run_dir}/validity.phases.json'],
                environment={},build=dict(revision='reviewed synthetic example',compiler='see build metadata',
                flags=[],mode='RelWithDebInfo',artifacts=[str(path) for path in artifacts])),
    measurement=dict(tool='perf-stat',repetitions=1,warmups=0,timeout_seconds=30,
                     options=dict(events=['duration_time']),phase_report='validity.phases.json'),
    correctness=dict(argv=[python,str(oracle),'check',str(binary),str(state)],
        description='Independent integer reference checks both synthetic strategies and restores preflight state.'),
    claim=claim,execution_state=state_contract)

def save(name,value):
    path=folder/name
    path.write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print(path)

for strategy in ('baseline','candidate'):
    item=copy.deepcopy(request);item['name']='synthetic-validity-'+strategy
    item['target']['argv'][4]=strategy
    save('validity-'+strategy+'.request.json',item)
aa_request=copy.deepcopy(request)
aa_request['name']='synthetic-validity-aa'
aa_request.pop('claim')
save('validity-aa.request.json',aa_request)
multiple=copy.deepcopy(request);multiple['name']='synthetic-validity-multiple-samples'
multiple['measurement']['repetitions']=3
save('validity-multiple.request.json',multiple)

# Deliberately permissive thresholds are example-only and preregistered. The
# default three-block result tests the interface, not statistical stability.
policy=dict(schema_version=1,min_samples=args.blocks,max_cv=0.5,max_regression_fraction=0.5)
study=dict(schema_version=1,analysis='paired',name='synthetic-validity-exploration',
    baseline='validity-baseline.request.json',candidate='validity-candidate.request.json',
    blocks=args.blocks,order='random_ab_ba',seed=20261027,budget=2*args.blocks,
    metric='time.wall',policy=policy,allow_command_change=True,
    constraints='Same synthetic task and integer oracle; strategy argument is the declared treatment.',
    resource_conditions=dict(concurrent_runs=1,background_load='uncontrolled',
        notes='Serial synthetic control; external state is reset and verified per measured invocation.'),
    workflow=dict(schema_version=1,phase='exploration',source_study_ids=[],
        selection_reason='Synthetic exploration; select a candidate with recorded evidence before confirmation.'))
save('validity-exploration.study.json',study)
calibration=copy.deepcopy(study)
calibration.update(name='synthetic-validity-aa',baseline='validity-aa.request.json',candidate='validity-aa.request.json',allow_command_change=False,
    constraints='A/A uses identical synthetic programs, inputs, state actions and declared measurement controls.')
calibration['workflow']=dict(schema_version=1,phase='calibration',source_study_ids=[],
    selection_reason='Synthetic same-program control; inspect its calibration result and limitations.')
save('validity-aa.study.json',calibration)
if args.exploration:
    confirmation=copy.deepcopy(study)
    confirmation['name']='synthetic-validity-confirmation'
    confirmation['workflow']=dict(schema_version=1,phase='confirmation',
        source_study_ids=[args.exploration]+([args.calibration] if args.calibration else []),
        selected_run_id=args.selected_run,
        selection_reason='Synthetic selected candidate; provide the actual selection rationale with a real workload.')
    save('validity-confirmation.study.json',confirmation)
if args.confirmation:
    acceptance=copy.deepcopy(study)
    acceptance['name']='synthetic-validity-acceptance'
    acceptance['workflow']=dict(schema_version=1,phase='acceptance',
        source_study_ids=[args.confirmation]+([args.calibration] if args.calibration else []),
        selected_run_id=args.confirmed_run,
        selection_reason='Synthetic engineering check following the recorded confirmation.')
    save('validity-acceptance.study.json',acceptance)
