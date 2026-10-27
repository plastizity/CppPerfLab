"""Side-effect-free successful-path execution counts, before any tool probe."""
from pathlib import Path
from .adapters import REGISTRY
from .errors import LabError


def execution_budget(request):
    selected=request['measurement']['tool']
    if selected!='auto' and selected not in REGISTRY:
        raise LabError('invalid_request','Unknown adapter '+selected)
    config=request['measurement']
    candidates=[]
    for tool in (list(REGISTRY) if selected=='auto' else [selected]):
        adapter=REGISTRY[tool]
        if 'work_report' in config and adapter.id != 'perf-stat':
            candidates.append({'tool':tool,'eligible':False,
                               'reason':{'category':'invalid_request','message':'Work report scope requires the non-batch perf-stat adapter'}})
            continue
        try:
            adapter.validate(config['options'])
        except LabError as error:
            if selected!='auto':raise
            candidates.append({'tool':tool,'eligible':False,'reason':error.as_dict()})
            continue
        validation='sample_validation' in request['correctness']
        if (validation or 'phase_report' in config) and adapter.batch:
            candidates.append({'tool':tool,'eligible':False,
                               'reason':{'category':'invalid_request','message':'Per-execution reports/validation require a nonbatch adapter'}})
            continue
        state=request.get('execution_state')
        if state is not None and state['scope']=='measurement_sample' and adapter.batch:
            candidates.append({'tool':tool,'eligible':False,
                               'reason':{'category':'unavailable','message':'State sample scope requires a nonbatch adapter'}})
            continue
        if tool=='google-benchmark' and config['warmups']:
            raise LabError('invalid_request','Google Benchmark uses options.warmup_seconds; set warmups=0')
        runtime=config.get('runtime_identity')
        if runtime and tool=='google-benchmark' and 'preflight' in runtime['stages']:
            candidates.append(dict(tool=tool,eligible=False,reason=dict(category='invalid_request',message='Integrated target has no native preflight to observe')))
            continue
        listing=int(tool=='google-benchmark')
        preflight=1-listing
        warmup_groups=0 if adapter.batch else config['warmups']
        measurement_groups=1 if adapter.batch else config['repetitions']
        validator_groups=config['repetitions'] if validation else 0
        groups=1+preflight+warmup_groups+measurement_groups+validator_groups
        from .state import state_budget
        state_actions=state_budget(request,measurement_groups)
        if state_actions is not None:
            groups+=state_actions['additional_commands']
        stages=[
            {'stage':'capability_probe','commands':None,'target_registration_listings':listing,
             'target_computation_runs':None if listing else 0,'meaning':'Integrated target main is executed for registration listing; its side effects are target-owned.' if listing else 'Tool versions and collection on small system workloads; not the configured target.'},
            {'stage':'correctness','commands':1,'target_computation_runs':None,
             'meaning':'Opaque trusted command; internal launches and runtime cannot be inferred.'},
            {'stage':'preflight','commands':preflight,'target_computation_runs':preflight},
            {'stage':'warmup','commands':warmup_groups,
             'target_computation_runs':None if listing else config['warmups'],
             'meaning':'Google Benchmark adaptive warmup; iterations unknown.' if listing else 'Hyperfine owns these internal launches.' if adapter.batch else 'One target invocation per warmup.'},
            {'stage':'measurement','commands':measurement_groups,
             'target_computation_runs':1 if listing else config['repetitions'],
             'observations_requested':config['repetitions'],
             'function_invocations':None,
             'meaning':'One process, adaptive timed iterations and repetition averages.' if listing else 'One target process per requested observation.'},
            {'stage':'per_sample_validation','commands':validator_groups,'target_computation_runs':None if validation else 0,
             'meaning':'External validator; any additional work inside it is opaque.'},
            {'stage':'postprocessing','commands':config['repetitions'] if tool=='perf-record' else None if tool=='massif' else 0,
             'maximum_commands':config['repetitions'] if tool in ('perf-record','massif') else 0,
             'target_computation_runs':0,
             'meaning':'perf report for each measurement; separate from target timing.' if tool=='perf-record' else 'Optional ms_print per measurement if discoverable; separate from target timing.' if tool=='massif' else 'No additional postprocessing process.'},
            {'stage':'binary_metadata','commands':None,'maximum_commands':1,'target_computation_runs':0,
             'meaning':'Optional readelf invocation on the target file, not execution of that file.'}]
        runtime_launches=(preflight if runtime and 'preflight' in runtime['stages'] else 0)+(measurement_groups if runtime and 'measurement' in runtime['stages'] else 0)
        runtime_budget=None if runtime is None else dict(stages=runtime['stages'],observed_launches=runtime_launches,additional_target_launches=0,
            maximum_snapshots=runtime['max_snapshots']*runtime_launches,maximum_process_handles_per_launch=runtime['max_processes'],interval_ms=runtime['interval_ms'],
            required_objects=len(runtime.get('required_objects',[])),file_hashing_bytes=None,hashing_scope='Observed executable/mapped files after each launch; bytes unknown before observation',
            measured_timing_affected='measurement' in runtime['stages'])
        trace=tool=='perf-record' and config['options'].get('sample_trace',False)
        precheck=bool(tool=='perf-record' and config['options'].get('precheck'))
        if precheck:stages[2]['meaning']='Configured profiling replaces this one preflight target launch; dynamic interpretability gate follows before formal samples'
        sampling_budget=None
        if trace:
            launches=measurement_groups+int(precheck)
            postprocessing_per_launch=3+int(config['options'].get('switch_events',False))
            sampling_budget=dict(profiled_launches=launches,additional_target_launches=0,known_perf_postprocessing_commands=postprocessing_per_launch*launches,
                switch_events=config['options'].get('switch_events',False),
                symbolization_commands=None if config['options'].get('symbolizer','perf')=='addr2line' else 0,
                target_preflight_profiled=precheck,static_target_inspection='ELF metadata only; target not executed')
            stages[6].update(commands=None,maximum_commands=None,known_perf_commands=postprocessing_per_launch*launches,
                meaning='perf report/script/evlist and optional switch-script per profile; explicit addr2line/readelf work depends on observed DSOs and addresses')
        phase_import_budget=None if 'phase_import' not in config else dict(additional_target_launches=0,source=config['phase_import']['source'],
            conversions=preflight+measurement_groups,mapping_sha256=config['phase_import']['mapping_sha256'],
            cost='Deterministic parsing/hashing after existing launches; log byte count unknown; excluded from target tool timing')
        candidates.append({'phase_import':phase_import_budget,'execution_state':state_actions,'progress':dict(enabled=config.get('progress',False),additional_target_launches=0,
            overhead='Opt-in process-start metadata writes can overlap target execution; slowdown is unquantified; no timing subtraction'),
            'sampling':sampling_budget,'runtime_identity':runtime_budget,'tool':tool,'eligible':True,'availability':'not_probed','eligibility_basis':'Adapter options and per-execution report compatibility only; goal and runtime availability still require plan.', 'stages':stages,
            'main_execution_groups':groups,'configured_timeout_budget_seconds':groups*config['timeout_seconds'],
            'known_target_computation_runs':None if listing else preflight+config['warmups']+config['repetitions'],
            'unknown_work':(['correctness command internal workload']
                + (['external validator internal workload'] if validation else [])
                + (['execution state actions internal workload'] if state_actions is not None and state_actions['additional_commands'] else [])),
            'adaptive_benchmark_warmup_seconds':config['options'].get('warmup_seconds',0) if listing else None,
            'adaptive_benchmark_min_time_seconds':config['options'].get('min_time_seconds',.1) if listing else None})
    return {'schema_version':1,'selected_tool':None if selected=='auto' else selected,
            'execution_performed':False,'candidates':candidates,'estimated_runtime_seconds':None,
            'pinned_input_count':len(request['workload'].get('expected_hashes',{})),
            'input_hashing_bytes_success_path':2*sum(Path(p).stat().st_size for p in request['workload']['inputs'])+sum(Path(p).stat().st_size for p in request['workload'].get('expected_hashes',{})),
            'limitations':['Successful-path counts; failures stop execution without implicit retries.',
                'Timeout totals are configured main-launch limits, not predicted runtime or a wall-clock upper bound.',
                'Probe/version/metadata/postprocessing commands, process cleanup and storage overhead are excluded from main-launch timeout totals.',
                'Oracle commands and adaptive benchmark iterations can perform additional unknown work.',
                'Input hashing is metadata I/O outside main launch timeouts and may warm file caches; cold-cache behavior is not assumed.',
                'No source edits or builds are executed.']}
