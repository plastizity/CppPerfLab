"""Parser/query control fixtures are synthetic, never benchmark success evidence."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from cppperflab.adapters import REGISTRY
from cppperflab.errors import LabError
from cppperflab.profile_queries import query_profile
from cppperflab.protocol import sha256
from cppperflab.sampling import ElfResolver,clock_context,evaluate_precheck,known_symbol,native_samples,normalize_trace,static_readiness
from cppperflab.storage import inventory,write_json
from cppperflab.views import build_view
import test_views as fixtures


CONTEXT=dict(name='CLOCK_MONOTONIC',boot_id='test-boot',pid_namespace='test-pidns',time_namespace='test-timens',monotonic_offset_ns=0)
POLICY=dict(min_samples=2,min_resolved_self_fraction=.8,min_resolved_stack_fraction=.5,require_phase_clock=True)


def phase(id,parent,start,end,path,complete=True):
    return dict(id=id,parent_id=parent,absolute_start_ns=start,absolute_end_ns=end,path=path,children_complete=complete,
                call_id=id,input_id='input',pid=17,tid=18,execution_stage='measurement',sample=0)


class SamplingTests(unittest.TestCase):
    def test_native_parser_has_exact_nanoseconds_periods_and_recorded_build_identity(self):
        text=''' 17/18 123.000000001: PERF_RECORD_MMAP2 17/18: [0x400000(0x3000) @ 0 <abcdef>]: r-xp /tmp/a library.so
 17/18 123.000000002: 10 cpu-clock:u:
        401020 [unknown] (/tmp/a library.so+0x1020)
        401030 caller (/tmp/a library.so+0x1030)

 17/18 123.000000003: PERF_RECORD_LOST lost 4
 17/18 123.000000004: 20 cpu-clock:u: 401040 add (/tmp/a library.so+0x1040)
'''
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'native.txt';p.write_text(text);metadata=dict(mapping_records=0,lost_events=0)
            rows=list(native_samples(p,metadata))
        self.assertEqual([r['time_ns'] for r in rows],[123000000002,123000000004])
        self.assertEqual(rows[0]['frames'][0]['file_offset'],0x1020)
        self.assertEqual(rows[0]['frames'][0]['recorded_build_id'],'abcdef')
        self.assertEqual(metadata,dict(mapping_records=1,lost_events=4))
        self.assertTrue(known_symbol('add'));self.assertTrue(known_symbol('unknown'))
        self.assertFalse(known_symbol('[unknown]'));self.assertFalse(known_symbol('0x123'))
        for bad in ('orphan frame','17/18 1.000000000: 0 cpu-clock:u: 4010 add (/tmp/a+0x10)','17/18 1.000000000: strange event'):
            with tempfile.TemporaryDirectory() as d:
                p=Path(d)/'bad';p.write_text(bad)
                with self.assertRaises(LabError):list(native_samples(p,dict(mapping_records=0,lost_events=0)))

    def test_elf_file_offsets_translate_through_load_segments_not_raw_virtual_guessing(self):
        for base in (0,0x400000):
            with tempfile.TemporaryDirectory() as d:
                root=Path(d);image=root/'image';image.write_bytes(b'\x7fELFcontrolled test data')
                frames=[dict(dso=str(image),file_offset=0x1020,recorded_build_id='abcdef',native_symbol='[unknown]')]
                def postprocess(argv,folder):
                    if argv[0]=='readelf':return ' LOAD 0x1000 '+hex(base+0x1000)+' 0x0 0x100 0x100 R E 0x1000\n Build ID: abcdef\n'
                    self.assertEqual(argv[-1],hex(base+0x1020));return hex(base+0x1020)+'\nadd\nfixture.cpp:7\n'
                resolver=ElfResolver(root/'symbolizer',dict(readelf='readelf',addr2line='addr2line'),{str(image):sha256(image)})
                with mock.patch('cppperflab.sampling.command',side_effect=postprocess):resolver.resolve(frames)
                self.assertTrue(frames[0]['resolved']);self.assertEqual(frames[0]['symbol'],'add')
                self.assertEqual(frames[0]['elf_address'],base+0x1020)
                self.assertEqual(frames[0]['attribution_state'],'Estimated')

    def test_symbol_identity_mismatch_does_not_fall_back_to_native_name(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);image=root/'image';image.write_bytes(b'\x7fELFfixture')
            resolver=ElfResolver(root/'symbols',dict(readelf='readelf',addr2line='addr2line'),{})
            f=dict(dso=str(image),file_offset=32,recorded_build_id='bad',native_symbol='old_name')
            with mock.patch('cppperflab.sampling.command',return_value='LOAD 0x0 0x0 0x0 0x100 0x100 R E\nBuild ID: abcdef\n'):
                resolver.resolve([f])
            self.assertFalse(f['resolved']);self.assertEqual(f['attribution_state'],'Invalid');self.assertEqual(f['symbol'],'[unknown]')

    def test_precheck_distinguishes_lack_of_samples_unresolved_symbols_and_clock_support(self):
        p=dict(quality=dict(sample_count=10,lost_events=0,resolved_self_fraction=1,resolved_two_frame_fraction=.8),clock=dict(CONTEXT))
        self.assertEqual(evaluate_precheck(p,POLICY,CONTEXT)['status'],'PASS')
        p['quality']['sample_count']=1;self.assertEqual(evaluate_precheck(p,POLICY,CONTEXT)['status'],'INCONCLUSIVE')
        p['quality']['sample_count']=10;p['quality']['resolved_self_fraction']=.2
        self.assertEqual(evaluate_precheck(p,POLICY,CONTEXT)['status'],'FAIL')
        self.assertEqual(evaluate_precheck(p,POLICY,None)['status'],'FAIL')
        p['quality']['resolved_self_fraction']=1
        self.assertEqual(evaluate_precheck(p,POLICY,None)['status'],'UNSUPPORTED')
        with tempfile.TemporaryDirectory() as d:
            result=static_readiness(None,Path(d)/'readiness',dict(precheck=POLICY,call_graph='dwarf'),
                dict(dwarf_unwind_available=False,symbolization_available=False),dict(readelf=None,addr2line=None))
            self.assertEqual(result['status'],'UNSUPPORTED');self.assertFalse(result['target_execution_performed'])

    def make_run(self,root):
        store,run=fixtures.ViewTests().setup_run(root)
        directory=store.record_path(run['run_id']).parent;folder=directory/'measurement-000';folder.mkdir()
        data=[]
        for i,(t,period,thread,known) in enumerate([(10,10,18,True),(20,20,18,True),(30,30,18,False),(40,40,18,True),(20,50,19,True)]):
            data.append(dict(index=i,pid=17,tid=thread,time_ns=t,period=period,event='cpu-clock:u',evidence_state='Measured',frames=[
                dict(symbol='leaf' if known else '[unknown]',dso='fixture',resolved=known),
                dict(symbol='caller',dso='fixture',resolved=True),dict(symbol='caller',dso='fixture',resolved=True)]))
        source=folder/'cpu-samples.jsonl';source.write_text(''.join(json.dumps(r)+'\n' for r in data))
        run['sampling_profiles']=[dict(evidence='measurement-000/profile.json',samples_evidence='measurement-000/cpu-samples.jsonl',
            execution_stage='measurement',measurement_group=0,quality=dict(sample_count=5,total_period=150),clock=dict(CONTEXT),event='cpu-clock:u')]
        run['phase_clocks']=[dict(sample=0,execution_stage='measurement',clock=dict(CONTEXT))]
        run['phases']=[phase('parent',None,10,40,['model']),phase('child','parent',20,30,['model','kernel'])]
        run['evidence']=inventory(directory);write_json(store.record_path(run['run_id']),run)
        return store,run,source

    def test_phase_half_open_windows_thread_scope_and_recursive_inclusive_dedup(self):
        with tempfile.TemporaryDirectory() as d:
            store,run,_=self.make_run(Path(d))
            r=query_profile(store,run['run_id'],'measurement',0,phase_id='parent')
            self.assertEqual(r['denominator']['selected_sample_count'],3)
            self.assertEqual(r['denominator']['selected_recorded_period'],60)
            self.assertEqual(r['denominator']['unresolved_selected_self_fraction'],.5)
            caller=next(h for h in r['hotspots'] if h['symbol']=='caller')
            self.assertEqual(caller['inclusive_percent'],100);self.assertEqual(caller['self_percent'],0)
            child=query_profile(store,run['run_id'],'measurement',0,phase_path=['model','kernel'])
            self.assertEqual(child['denominator']['selected_sample_count'],1)
            own=query_profile(store,run['run_id'],'measurement',0,phase_id='parent',phase_mode='self')
            self.assertEqual(own['denominator']['selected_recorded_period'],40)
            whole=query_profile(store,run['run_id'],'measurement',0)
            self.assertEqual(whole['denominator']['selected_recorded_period'],150)

    def test_missing_clocks_or_incomplete_self_coverage_never_degrade_silently(self):
        with tempfile.TemporaryDirectory() as d:
            store,run,_=self.make_run(Path(d))
            run['phases'][0]['children_complete']=False;write_json(store.record_path(run['run_id']),run)
            self.assertEqual(query_profile(store,run['run_id'],'measurement',0,phase_id='parent',phase_mode='self')['selection_status'],'UNSUPPORTED')
            run['phase_clocks'][0]['clock']['time_namespace']='other';write_json(store.record_path(run['run_id']),run)
            r=query_profile(store,run['run_id'],'measurement',0,call_id='parent')
            self.assertEqual(r['evidence_state'],'Unsupported');self.assertIsNone(r['denominator'])
            with self.assertRaises(LabError):query_profile(store,run['run_id'],'measurement',None)

    def test_tampered_cpu_trace_cannot_return_a_normal_profile(self):
        with tempfile.TemporaryDirectory() as d:
            store,run,source=self.make_run(Path(d))
            value=source.read_text().replace('"period": 10','"period": 11');source.write_text(value)
            with self.assertRaises(LabError) as caught:query_profile(store,run['run_id'],'measurement',0)
            self.assertEqual(caught.exception.category,'storage_failed')

    def test_options_validate_explicit_modes_and_thresholds(self):
        adapter=REGISTRY['perf-record']
        adapter.validate(dict(frequency=99,sample_trace=True,call_graph='fp',symbolizer='addr2line',precheck=POLICY))
        for options in (dict(symbolizer='addr2line'),dict(call_graph='automatic'),dict(sample_trace=True,precheck=dict(POLICY,min_samples=0))):
            with self.assertRaises(LabError):adapter.validate(options)

    def test_sampling_schema_names_match_emitted_protocol_and_keep_provenance_states(self):
        schema=json.loads((Path(__file__).parents[1]/'src/cppperflab/schemas/sampling.schema.json').read_text())
        definitions=schema['$defs']
        self.assertTrue({'sample','frame','profile','precheckResult','profileQuery','options'}<=set(definitions))
        self.assertEqual(set(definitions['sample']['properties']) & {'index','pid','tid','time_ns','period','event','evidence_state','frames'},
                         {'index','pid','tid','time_ns','period','event','evidence_state','frames'})
        self.assertEqual(definitions['sample']['properties']['evidence_state']['const'],'Measured')
        self.assertEqual(set(definitions['evidenceState']['enum']),{'Measured','Estimated','Modeled','Unsupported','Missing','Invalid'})
        self.assertEqual(definitions['quality']['properties']['complete_callchain_proven']['const'],False)
        self.assertIn('monotonic_offset_ns',definitions['samplingClock']['required'])
        self.assertEqual(definitions['samplingClock']['properties']['monotonic_offset_ns']['const'],0)
        self.assertIn('after_capture_monotonic_ns',definitions['samplingClock']['required'])
        self.assertEqual(definitions['hotspot']['properties']['sample']['type'],['integer','null'])
        self.assertEqual(len(definitions['options']['allOf']),2)

    def test_clock_context_rejects_nonzero_monotonic_namespace_offset(self):
        from pathlib import Path
        original=Path.read_text
        def read_text(path,*args,**kwargs):
            if str(path)=='/proc/self/timens_offsets':return 'monotonic 1 0\nboottime 0 0\n'
            return original(path,*args,**kwargs)
        with mock.patch.object(Path,'read_text',read_text):
            with self.assertRaises(LabError) as caught:clock_context()
        self.assertEqual(caught.exception.category,'unavailable')

    def test_unavailable_clock_metadata_is_unsupported_before_target_execution(self):
        with tempfile.TemporaryDirectory() as d,mock.patch.object(Path,'read_text',side_effect=PermissionError('clock metadata denied')):
            result=static_readiness(None,Path(d)/'readiness',dict(call_graph='fp'),{},dict(readelf=None,addr2line=None))
        self.assertEqual(result['status'],'UNSUPPORTED')
        self.assertFalse(result['target_execution_performed'])
        self.assertIn('clock correlation metadata',result['issues'][0])

    def test_normalizer_rejects_samples_outside_monotonic_capture_window(self):
        for stamp in ('0.000000050','0.000000201'):
            with self.subTest(stamp=stamp),tempfile.TemporaryDirectory() as d:
                root=Path(d);(root/'sample-attributes').mkdir();(root/'script').mkdir()
                context=dict(name='CLOCK_MONOTONIC',boot_id='test-boot',pid_namespace='pid',time_namespace='time',
                    monotonic_offset_ns=0,before_launch_monotonic_ns=100,after_capture_monotonic_ns=200)
                write_json(root/'sampling-context.json',context)
                (root/'sample-attributes/stdout.txt').write_text('use_clockid: 1\nclockid: 1\n')
                (root/'script/stdout.txt').write_text(f'17/18 {stamp}: 10 cpu-clock:u:\n 401020 add (/tmp/a.so+0x1020)\n')
                with mock.patch('cppperflab.sampling.time.monotonic_ns',return_value=200):
                    with self.assertRaises(LabError) as caught:normalize_trace(root,dict(symbolizer='perf'),{})
                self.assertEqual(caught.exception.category,'parse_failed')
                self.assertIn('outside',str(caught.exception))

    def test_phase_query_requires_explicit_zero_monotonic_namespace_offset(self):
        with tempfile.TemporaryDirectory() as d:
            store,run,_=self.make_run(Path(d))
            clock=run['sampling_profiles'][0]['clock']
            clock.update(monotonic_offset_ns=0,before_launch_monotonic_ns=0,after_capture_monotonic_ns=100,verified_attributes='attributes.txt')
            write_json(store.record_path(run['run_id']),run)
            self.assertEqual(query_profile(store,run['run_id'],'measurement',0,phase_id='parent')['selection_status'],'PASS')
            for value in (None,1):
                if value is None:clock.pop('monotonic_offset_ns')
                else:clock['monotonic_offset_ns']=value
                write_json(store.record_path(run['run_id']),run)
                result=query_profile(store,run['run_id'],'measurement',0,phase_id='parent')
                self.assertEqual(result['selection_status'],'UNSUPPORTED')
                self.assertIsNone(result['denominator'])

    def test_cli_query_and_html_share_the_selected_denominator(self):
        from cppperflab.cli import parser,dispatch
        from cppperflab.profile_queries import profile_view
        from cppperflab.html_report import render_html
        with tempfile.TemporaryDirectory() as d:
            store,run,_=self.make_run(Path(d))
            args=parser().parse_args(['profile',run['run_id'],'--measurement','0','--phase-id','child','--store',str(store.root)])
            selected=dispatch(args)
            self.assertEqual(selected['denominator']['selected_sample_count'],1)
            view=profile_view(store,selected)
            document=render_html(view,store,Path(d)/'profile.html')
            snapshot=fixtures.SnapshotParser();snapshot.feed(document)
            self.assertEqual(json.loads(snapshot.snapshot),view)
            self.assertIn('CPU 样本筛选与分母',document)
            self.assertIn('observed-chain inclusive',document)

    def test_profile_precheck_reuses_one_preflight_and_stops_before_formal_on_failure(self):
        from cppperflab.lab import run
        from cppperflab.storage import Store
        from test_core import request_template
        class Adapter:
            batch=False
            def describe(self):return dict(id='perf-record',mode='controlled orchestration fixture')
            def prepare(self,command,folder,options,**kwargs):
                preparations.append(Path(folder).name);return command
            def parse(self,folder,options,record):
                profile=dict(schema_version=1,quality=dict(sample_count=10,total_period=100,lost_events=0,
                    resolved_self_fraction=0 if fail else 1,resolved_two_frame_fraction=1),clock=dict(CONTEXT,verified_attributes='attributes.txt'),
                    samples_evidence='controlled.jsonl',native_evidence='controlled-native.txt',helpers={},images=[])
                write_json(folder/'sampling-profile.json',profile)
                return dict(metrics=[],hotspots=[],sampling_profile=profile)
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);log=root/'target-calls';r=request_template(root)
            r['target']['argv']=[__import__('sys').executable,'-c','from pathlib import Path; p=Path('+repr(str(log))+'); p.open("a").write("call\\n")']
            r['measurement'].update(tool='perf-record',repetitions=2,warmups=0,options=dict(sample_trace=True,precheck=dict(POLICY,require_phase_clock=False)))
            plan=dict(selected='perf-record',probes=dict(tools={'perf-record':dict(version='controlled test fixture')}))
            for fail in (True,False):
                if log.exists():log.unlink()
                preparations=[]
                with mock.patch('cppperflab.lab.plan',return_value=plan),mock.patch.dict('cppperflab.lab.REGISTRY',{'perf-record':Adapter()}):
                    result=run(r,Store(root/'store'))
                self.assertEqual(result['sampling_precheck']['status'],'FAIL' if fail else 'PASS')
                self.assertEqual(result['status'],'failed' if fail else 'completed',result['error'])
                self.assertEqual(len(log.read_text().splitlines()),1 if fail else 3)
                self.assertEqual(preparations,['preflight'] if fail else ['preflight','measurement-000','measurement-001'])
                self.assertEqual(len(result['samples']),0 if fail else 2)
                self.assertEqual(result['sampling_profiles'][0]['execution_stage'],'preflight')
