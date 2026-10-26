#!/usr/bin/env python3.11
"""Generate standalone file-adapter inputs and requests. No build or execution."""
import argparse
import copy
import json
from pathlib import Path

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--collection',help='Existing samples capture collection ID; also emit the profiling Study')
args=parser.parse_args()
root=Path(__file__).resolve().parents[1]
folder=root/'workloads';folder.mkdir(exist_ok=True)
inputs=root/'.perflab/example-inputs';inputs.mkdir(parents=True,exist_ok=True)
binary=inputs/'case-bytes.bin';binary.write_bytes(bytes(range(256))*128)
text='x abc 0123456789 xx\n'*4096
json_value={'document':text,'items':[1,2,3]}
json_bytes=json.dumps(json_value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')
def payload(format,**source):return dict(format=format,format_version='raw-bytes-v1',**source)
def case(id,input,expected,result='exported'):
    return dict(id=id,input=input,expected=dict(format='text',format_version='count-v1',value=str(expected)),
                input_complete=True,result=result,correctness='not_checked',
                observation=dict(duration_seconds=None,evidence_state='Missing',reason='No producer timing; replay will measure it'))
samples=[case('text',payload('text',value=text),text.count('x')),
         case('document',payload('json',value=json_value),json_bytes.count(b'x')),
         case('bytes',payload('binary',path=str(binary)),binary.read_bytes().count(b'x')),
         case('mismatch',payload('text',value='x'),2,result='negative-control')]
manifest=dict(schema_version=1,name='synthetic-file-adapter-inputs',samples=samples,selection=dict(mode='all'),max_payload_bytes=1024*1024)
(folder/'case-samples.json').write_text(json.dumps(manifest,indent=2)+'\n')
exe=root/'build/profile/examples/cpp/file_case'
request=dict(schema_version=1,name='synthetic-file-adapter',workload=dict(id='synthetic-raw-document-count',parameters=dict(lifecycle='per_call',repeats=8),inputs=[]),
 target=dict(argv=[str(exe),'--case-v1','{case_input}','{case_expected_or_none}','{case_id}','{case_format}','{case_format_version}',
                   '{case_expected_format_or_none}','{case_expected_version_or_none}','{case_lifecycle}','{case_repeats}','{run_dir}/case.phases.json'],environment={},
             build=dict(revision='reviewed synthetic source',compiler='see compile_commands.json',flags=['-O2','-g','-DNDEBUG','-fno-fast-math','-ffp-contract=off'],mode='RelWithDebInfo',
               artifacts=[str(root/'examples/cpp/file_case.cpp'),str(root/'include/cppperflab/case.hpp'),str(root/'include/cppperflab/phase.hpp'),str(root/'build/profile/compile_commands.json')])),
 measurement=dict(tool='perf-stat',repetitions=9,warmups=1,timeout_seconds=30,options={'events':['duration_time']},phase_report='case.phases.json'),
 correctness=dict(argv=[str(exe),'--verify'],description='Synthetic fixture checks before run; every invocation independently checked against std::count and the optional captured expected scalar'))
(folder/'case-request.json').write_text(json.dumps(request,indent=2)+'\n')
reuse=copy.deepcopy(request);reuse['workload']['parameters']['lifecycle']='reuse_reset'
(folder/'case-reuse-request.json').write_text(json.dumps(reuse,indent=2)+'\n')
print(folder/'case-samples.json');print(folder/'case-request.json')

if args.collection:
    study=dict(schema_version=1,analysis='cases',name='synthetic-file-case-matrix',collection_id=args.collection,
               samples=['text','document','bytes'],requests={'target':'case-request.json'},comparisons=[],seed=71,budget=3,
               constraints='Synthetic input bytes and exact count oracle; per-call lifecycle; no aggregate speedup claim')
    (folder/'case-study.json').write_text(json.dumps(study,indent=2)+'\n')
    print(folder/'case-study.json')

    policy=dict(schema_version=1,min_samples=7,max_cv=.1,max_regression_fraction=.05)
    scope='case-lifecycle; phase=["operation","invoke"]'
    def comparison(id,role,metric,gate,scope=None):
        result=dict(id=id,role=role,metric=metric,gate=gate,policy=dict(policy))
        if scope is not None:result['metric_scope']=scope
        return result
    assessment=dict(schema_version=1,analysis='assessment',name='synthetic-joint-assessment',collection_id=args.collection,
        samples=['text'],requests=dict(baseline='case-request.json',candidate='case-request.json'),profiles=[
            dict(id='native',tool='perf-stat',options=dict(events=['duration_time']),warmups=1,
                 design=dict(kind='paired',blocks=7,order='random_ab_ba'),comparisons=[
                     comparison('process','end_to_end','time.wall','non_regression'),
                     comparison('invoke','stage','phase.inclusive_time','informational',scope),
                     comparison('work','work','phase.work','informational',scope+'; counter="input_bytes"')]),
            dict(id='heap',tool='massif',options={},warmups=0,design=dict(kind='unpaired',repetitions=7),
                 comparisons=[comparison('heap','resource','memory.heap_peak_observed','non_regression')])],
        seed=71,budget=16,constraints='Same synthetic binary control, exact count validation, no optimization claim',
        resource_conditions=dict(concurrent_runs=1,background_load='uncontrolled',notes='No resource isolation enforced'))
    (folder/'case-assessment.json').write_text(json.dumps(assessment,indent=2)+'\n')
    print(folder/'case-assessment.json')
