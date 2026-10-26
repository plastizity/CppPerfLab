#!/usr/bin/env python3.11
"""Generate a serial control study from existing synthetic phase configs; no builds."""
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
