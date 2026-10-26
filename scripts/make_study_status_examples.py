#!/usr/bin/env python3.11
"""Prepare standalone live Study examples; no target execution or compilation."""
import argparse
import json
from pathlib import Path

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--collection',help='Captured study-status-samples collection ID in the selected Store')
args=parser.parse_args()
root=Path(__file__).resolve().parents[1];out=root/'workloads';out.mkdir(exist_ok=True)
def write(name,value):
    path=out/name;path.write_text(json.dumps(value,indent=2)+'\n');print(path)

template=json.loads((out/'status-progress.json').read_text())
template['measurement'].update(repetitions=1,warmups=0)
write('study-status-scale.request.json',template)
write('study-status-scale.json',dict(schema_version=1,name='synthetic-linked-scale-study',template='study-status-scale.request.json',
    axis=dict(parameter='n',values=[1000000,2000000],argument_index=1),
    variants=[dict(id='reference',executable=template['target']['argv'][0],revision=template['target']['build']['revision'],
        artifacts=template['target']['build']['artifacts'])],budget=2,seed=17,metric='time.wall',
    constraints='Exact cyclic integer sums; scope is lifecycle/lineage verification, not a complexity or speedup claim'))

source=root/'.perflab/example-inputs/study-status.bin';source.parent.mkdir(parents=True,exist_ok=True)
data=bytes(range(256))*16384;source.write_bytes(data)
write('study-status-samples.json',dict(schema_version=1,name='synthetic-linked-study-input',
    samples=[dict(id='bytes',input=dict(format='binary',format_version='raw-bytes-v1',path=str(source)),
        expected=dict(format='text',format_version='count-v1',value=str(data.count(b'x'))),input_complete=True,
        result='synthetic',correctness='not_checked',observation=dict(duration_seconds=None,evidence_state='Missing',reason='No producer timing'))],
    selection=dict(mode='all'),max_payload_bytes=8*1024*1024))
case=json.loads((out/'case-request.json').read_text());case['name']='synthetic-linked-file-case'
case['measurement'].update(progress=True,repetitions=1,warmups=0)
case['goal']=dict(metrics=['time.wall'],max_overhead='high',allow_instrumentation=False)
write('study-status-case.request.json',case)
if args.collection:
    policy=dict(schema_version=1,min_samples=2,max_cv=1,max_regression_fraction=.5)
    spec=dict(schema_version=1,analysis='assessment',name='synthetic-linked-assessment',collection_id=args.collection,
        samples=['bytes'],requests=dict(baseline='study-status-case.request.json',candidate='study-status-case.request.json'),
        profiles=[dict(id='process',tool='perf-stat',options=dict(events=['duration_time']),warmups=0,
            design=dict(kind='paired',blocks=2,order='random_ab_ba'),
            comparisons=[dict(id='time',role='end_to_end',metric='time.wall',gate='non_regression',policy=policy)]),
            dict(id='work',tool='perf-stat',options=dict(events=['duration_time']),warmups=0,
                design=dict(kind='unpaired',repetitions=2),comparisons=[dict(id='work',role='work',metric='phase.work',
                    metric_scope='case-lifecycle; phase=["operation","invoke"]; counter="input_bytes"',gate='informational',policy=policy)])],
        seed=19,budget=6,constraints='Same reviewed binary and captured byte payload; exact count oracle; no optimization claim',
        resource_conditions=dict(concurrent_runs=1,background_load='uncontrolled',notes='Serial diagnostic lifecycle observation; metadata interference allowed'))
    write('study-status-assessment.json',spec)
