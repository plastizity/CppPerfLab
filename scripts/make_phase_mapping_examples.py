#!/usr/bin/env python3.11
"""Generate two deterministic mappings for different synthetic program logs."""
import copy
import json
from pathlib import Path

root=Path(__file__).resolve().parents[1];out=root/'workloads';out.mkdir(exist_ok=True)
def write(name,value):
    path=out/name;path.write_text(json.dumps(value,indent=2)+'\n');print(path)
maps={}
for style in ('format','keywords'):
    rules=[]
    for id,parent in [('model',None),('assemble','model')]:
        row=dict(id=id,name=id,parent_id=parent,aggregation='single',required=True,children_coverage='unknown')
        if style=='format':
            row.update(keyword=id.upper()+' ',format=id.upper()+' total={total} own={own} calls={calls} items={items} status={status}',
                inclusive=dict(group='total',unit='ms' if id=='model' else 'us',kind='total'),
                self=dict(group='own',unit='ms' if id=='model' else 'us'),calls=dict(group='calls'),
                correctness=dict(group='status',values={'OK':'passed','FAIL':'failed'}),
                work=dict(items=dict(group='items',unit='count',evidence_state='Measured')))
        else:
            row.update(keyword='[profile] '+id+' ',inclusive=dict(keyword='total_ms',unit='ms',kind='total'),
                self=dict(keyword='self_ms',unit='ms'),calls=dict(keyword='count'),
                correctness=dict(keyword='check',values={'OK':'passed','FAIL':'failed'}),
                work=dict(items=dict(keyword='items',unit='count',evidence_state='Measured')))
        rules.append(row)
    mapping=dict(schema_version=1,scope='mapped-synthetic-model',unmatched_lines='error',rules=rules)
    maps[style]=mapping;write('phase-map-'+style+'.json',mapping)
binary=root/'build/profile/examples/cpp/logged_stages'
for style in ('format','keywords','bad'):
    request=dict(schema_version=1,name='synthetic-mapped-logs-'+style,
        workload=dict(id='mapped-cyclic-sum',parameters=dict(n=2000000),inputs=[]),
        target=dict(argv=[str(binary),'2000000',style],environment={},build=dict(revision='reviewed synthetic counted model',
            compiler='see compile_commands.json',flags=[],mode='RelWithDebInfo',artifacts=[str(root/'examples/cpp/logged_stages.cpp'),str(root/'build/profile/compile_commands.json')])),
        correctness=dict(argv=[str(binary),'--verify'],description='Independent closed-form boundary oracle; every model run independently validated'),
        measurement=dict(tool='perf-stat',repetitions=3,warmups=0,timeout_seconds=30,options=dict(events=['duration_time']),
            phase_report='mapped.phases.json',phase_import=dict(source='stdout.txt',mapping='phase-map-'+('keywords' if style=='bad' else style)+'.json')),
        goal=dict(metrics=['time.wall','phase.inclusive_time','phase.work'],max_overhead='low',allow_instrumentation=False))
    write('mapped-logs-'+style+'.json',request)
