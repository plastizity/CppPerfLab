#!/usr/bin/env python3.11
"""Generate serial and concurrent control studies from existing configs; no builds."""
import copy
import json
from pathlib import Path

root=Path(__file__).resolve().parents[1]
folder=root/'workloads'
request=json.loads((folder/'phase-text.json').read_text())
request['measurement'].update(repetitions=1,warmups=0)
for variant in ('baseline','candidate'):
    local=copy.deepcopy(request);local['name']='synthetic-paired-'+variant
    (folder/('paired-'+variant+'.json')).write_text(json.dumps(local,indent=2)+'\n')
policy=dict(schema_version=1,min_samples=7,max_cv=.1,max_regression_fraction=.05)
spec=dict(schema_version=1,analysis='paired',name='synthetic-serial-paired-control',baseline='paired-baseline.json',candidate='paired-candidate.json',
          blocks=7,order='abba',seed=20261026,budget=28,metric='phase.inclusive_time',metric_scope='text-function; phase=["call","invoke"]',policy=policy,
          constraints='Same reviewed executable, text bytes and 32 calls per run; a repeatability control, not an optimization.',
          resource_conditions=dict(concurrent_runs=1,background_load='uncontrolled',notes='Serial platform launches, inherited CPU affinity; no resource isolation enforced.'),
          secondary_metrics=[dict(metric='time.wall',policy=policy),dict(metric='phase.work',metric_scope='text-function; phase=["call","invoke"]; counter="input_bytes"',policy=policy)])
path=folder/'paired-text.study.json';path.write_text(json.dumps(spec,indent=2)+'\n');print(path)

# This interface example starts two identical, already-built synthetic programs.
# It demonstrates measured competition; it is not a candidate speedup claim.
thread_request=json.loads((folder/'thread-activity.json').read_text())
thread_request['measurement'].update(repetitions=1,warmups=0,progress=True,
    runtime_identity=dict(stages=['measurement'],interval_ms=20,max_snapshots=500,max_processes=16,required_objects=[]))
thread_request['goal'].update(max_overhead='high',allow_instrumentation=True)
for variant in ('baseline','candidate'):
    local=copy.deepcopy(thread_request)
    local['name']='synthetic-concurrent-paired-'+variant
    (folder/('concurrent-paired-'+variant+'.json')).write_text(json.dumps(local,indent=2)+'\n')
concurrent_policy=dict(schema_version=1,min_samples=2,max_cv=.5,max_regression_fraction=.5)
concurrent_spec=dict(schema_version=1,analysis='paired',name='synthetic-competing-pair-interface-control',
    baseline='concurrent-paired-baseline.json',candidate='concurrent-paired-candidate.json',
    blocks=2,order='random_ab_ba',seed=20261026,budget=4,metric='time.wall',policy=concurrent_policy,
    constraints='Same reviewed two-thread synthetic workload on both sides; interface control only, no speedup claim.',
    resource_conditions=dict(concurrent_runs=2,background_load='uncontrolled',
        notes='Both synthetic processes compete concurrently; inherited CPU affinity and background load are not controlled.',
        synchronization_timeout_seconds=60,minimum_overlap_fraction=.5,
        threads_per_variant=dict(baseline=2,candidate=2)))
path=folder/'paired-thread-activity.concurrent.study.json'
path.write_text(json.dumps(concurrent_spec,indent=2)+'\n');print(path)
