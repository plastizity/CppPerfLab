import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from cppperflab.errors import LabError
from cppperflab.samples import capture,capture_budget,query_samples,verify_sample
from cppperflab.replay import bind_sample
from cppperflab.storage import Store,write_json
from test_core import request_template


def payload(value='hello'):
    return dict(format='text',format_version='test-v1',value=value)

def sample(id='a',seconds=1,result='uncertified'):
    return dict(id=id,input=payload(),expected=payload('ok'),input_complete=True,result=result,correctness='passed',
                observation=dict(duration_seconds=seconds,evidence_state='Measured'))

def manifest(samples=None):
    return dict(schema_version=1,name='test samples',samples=samples or [sample()],selection=dict(mode='all'),max_payload_bytes=10000)


class SampleTests(unittest.TestCase):
    def setup_manifest(self,d,spec):
        p=Path(d)/'samples.json';write_json(p,spec);return p,Store(Path(d)/'store')

    def test_capture_preserves_bytes_and_replay_after_original_removed(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);data=root/'raw';data.write_bytes(b'x\x00\xff')
            attachment=root/'mesh';attachment.write_bytes(b'opaque')
            spec=manifest();spec['samples'][0]['input']=dict(format='binary',format_version='opaque-v1',path=str(data),attachments=[dict(name='nested/mesh.bin',path=str(attachment))])
            path,store=self.setup_manifest(d,spec);r=capture(path,store)
            self.assertEqual(r['status'],'completed',r['error'])
            data.unlink();attachment.unlink()
            record,s=verify_sample(store,r['collection_id'],'a')
            folder=store.record_path(r['collection_id'],'collection').parent
            self.assertEqual((folder/s['input']['entry']).read_bytes(),b'x\x00\xff')
            self.assertEqual((folder/s['input']['files'][1]['path']).read_bytes(),b'opaque')
            self.assertEqual(s['result'],'uncertified');self.assertEqual(s['correctness'],'passed')

    def test_selection_is_declared_reproducible_and_keeps_population(self):
        items=[sample(str(i),i,'success' if i<4 else 'uncertified') for i in range(8)]
        with tempfile.TemporaryDirectory() as d:
            spec=manifest(items);spec['selection']=dict(mode='longest_per_result',seed=7,limit_per_result=1)
            path,store=self.setup_manifest(d,spec)
            a=capture_budget(path,store);b=capture_budget(path,store)
            self.assertEqual(a['selected_ids'],['3','7']);self.assertEqual(a,b)
            self.assertFalse(store.root.exists())
            r=capture(path,store)
            self.assertEqual(len(r['samples']),8)
            self.assertEqual(sum(s['captured'] for s in r['samples']),2)
            with self.assertRaises(LabError):verify_sample(store,r['collection_id'],'0')
            q=query_samples(store,r['collection_id'],result='uncertified',sort='elapsed_desc')
            self.assertEqual(q['data'][0]['id'],'7')

    def test_bad_descriptors_and_insufficient_evidence_rejected(self):
        for issue in ('partial','nan','binary_inline','path_escape','budget','missing_time','unknown','nul_path'):
            with tempfile.TemporaryDirectory() as d:
                spec=manifest()
                if issue=='partial':spec['samples'][0]['input_complete']=False
                if issue=='nan':spec['samples'][0]['observation']['duration_seconds']=float('nan')
                if issue=='binary_inline':spec['samples'][0]['input']['format']='binary'
                if issue=='path_escape':spec['samples'][0]['input']['attachments']=[dict(name='../escape',path='/bin/true')]
                if issue=='budget':spec['max_payload_bytes']=1
                if issue=='missing_time':
                    spec['samples'][0]['observation']=dict(duration_seconds=None,evidence_state='Missing',reason='not exported')
                    spec['selection']=dict(mode='longest_per_result',seed=1,limit_per_result=1)
                if issue=='unknown':spec['surprise']=0
                if issue=='nul_path':spec['samples'][0]['input']=dict(format='text',format_version='v1',path='bad\x00file')
                path=Path(d)/'samples.json';path.write_text(json.dumps(spec));store=Store(Path(d)/'store')
                with self.subTest(issue=issue),self.assertRaises(LabError):capture_budget(path,store)
                self.assertFalse(store.root.exists())

    def test_no_hidden_execution_and_json_validation(self):
        with tempfile.TemporaryDirectory() as d:
            spec=manifest();spec['samples'][0]['input']=dict(format='json',format_version='v1',value={'array':[1,2],'str':'x'})
            path,store=self.setup_manifest(d,spec)
            with mock.patch('cppperflab.execution.execute',side_effect=AssertionError('exec')):
                r=capture(path,store)
            _,s=verify_sample(store,r['collection_id'],'a')
            folder=store.record_path(r['collection_id'],'collection').parent
            self.assertEqual(json.loads((folder/s['input']['entry']).read_text()),{'array':[1,2],'str':'x'})

    def test_tampering_fails_without_repair(self):
        with tempfile.TemporaryDirectory() as d:
            path,store=self.setup_manifest(d,manifest());r=capture(path,store)
            p=store.record_path(r['collection_id'],'collection').parent/r['samples'][0]['input']['entry']
            p.write_text('changed')
            with self.assertRaises(LabError):verify_sample(store,r['collection_id'],'a')
            self.assertEqual(p.read_text(),'changed')

    def test_request_binding_and_no_expected_are_explicit(self):
        with tempfile.TemporaryDirectory() as d:
            spec=manifest();del spec['samples'][0]['expected']
            path,store=self.setup_manifest(d,spec);r=capture(path,store)
            template=request_template(d)
            template['target']['argv']+=['{case_input}','{case_expected_or_none}','{case_id}']
            bound=bind_sample(store,r['collection_id'],'a',template)
            self.assertEqual(bound['target']['argv'][-2:],['-','a'])
            self.assertEqual(bound['workload']['parameters']['case.sample_id'],'a')
            self.assertEqual(len(bound['workload']['inputs']),1)
            self.assertIn('{case_input}',template['target']['argv'])
            template['target']['argv'][-2]='{case_expected}'
            with self.assertRaises(LabError):bind_sample(store,r['collection_id'],'a',template)

    def test_selected_file_format_is_validated_without_repair(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);source=root/'invalid.json';source.write_text('{broken')
            spec=manifest();spec['samples'][0]['input']=dict(format='json',format_version='v1',path=str(source))
            path,store=self.setup_manifest(d,spec)
            preview=capture_budget(path,store)
            self.assertFalse(preview['files_written'])
            r=capture(path,store)
            self.assertEqual(r['status'],'failed')
            self.assertFalse(r['samples'][0]['captured'])
            self.assertEqual(source.read_text(),'{broken')
            with self.assertRaises(LabError):verify_sample(store,r['collection_id'],'a')

    def test_source_link_uses_hashed_raw_phase_and_archives_it(self):
        from cppperflab.phases import normalize
        from cppperflab.storage import inventory
        from test_phases import report
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);store=Store(root/'store');run_id,folder=store.create()
            measurement=folder/'measurement-000';measurement.mkdir()
            path=measurement/'raw.phases.json';write_json(path,report())
            phases=[{**p,'sample':0,'evidence':'measurement-000/raw.phases.json'} for p in normalize(path)['phases']]
            r={'schema_version':1,'run_id':run_id,'phases':phases,'evidence':inventory(folder)};write_json(folder/'result.json',r)
            spec=manifest([sample('input-1')]);item=spec['samples'][0]
            item['source']=dict(run_id=run_id,sample_index=0,phase_id='p')
            del item['observation']
            manifest_path=root/'samples.json';write_json(manifest_path,spec)
            captured=capture(manifest_path,store)
            self.assertEqual(captured['status'],'completed',captured['error'])
            path.unlink()
            _,s=verify_sample(store,captured['collection_id'],'input-1')
            self.assertEqual(s['observation']['evidence_state'],'Measured')
            self.assertAlmostEqual(s['observation']['duration_seconds'],80e-9)

    def test_distinct_call_samples_can_share_a_producer_input_id(self):
        from cppperflab.phases import normalize
        from cppperflab.storage import inventory
        from test_phases import report
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);store=Store(root/'store');run_id,folder=store.create()
            measurement=folder/'measurement-000';measurement.mkdir()
            raw=measurement/'raw.phases.json';write_json(raw,report())
            phases=[{**p,'sample':0,'evidence':'measurement-000/raw.phases.json'} for p in normalize(raw)['phases']]
            write_json(folder/'result.json',{'schema_version':1,'run_id':run_id,'phases':phases,'evidence':inventory(folder)})
            a,b=sample('call-one'),sample('call-two')
            for item,phase in ((a,'p'),(b,'a')):
                item.update(input_id='input-1',source=dict(run_id=run_id,sample_index=0,phase_id=phase))
                del item['observation']
            spec=manifest([a,b]);spec['selection']=dict(mode='longest_per_result',limit_per_result=1,seed=7)
            path=root/'samples.json';write_json(path,spec)
            r=capture(path,store)
            self.assertEqual(r['status'],'completed',r['error'])
            self.assertEqual(r['selected_ids'],['call-one'])
            self.assertEqual(r['samples'][1]['input_id'],'input-1')

    def test_prepared_request_refuses_changed_payload_before_probe(self):
        from cppperflab.lab import run
        with tempfile.TemporaryDirectory() as d:
            path,store=self.setup_manifest(d,manifest());r=capture(path,store)
            template=request_template(d);template['target']['argv']+=['{case_input}']
            bound=bind_sample(store,r['collection_id'],'a',template)
            source=Path(bound['target']['argv'][-1]);source.write_text('changed input')
            with mock.patch('cppperflab.lab.plan',side_effect=AssertionError('probe executed')):
                failed=run(bound,store)
            self.assertEqual(failed['status'],'failed')
            self.assertEqual(failed['error']['category'],'invalid_request')
            self.assertIn('before any probe',failed['error']['message'])

    def test_malformed_persisted_collection_rejected_without_repair(self):
        for issue in ('version','bundle','file','digest'):
            with tempfile.TemporaryDirectory() as d:
                path,store=self.setup_manifest(d,manifest());r=capture(path,store)
                saved=store.record_path(r['collection_id'],'collection')
                if issue=='version':r['schema_version']=2
                if issue=='bundle':r['samples'][0]['input']={}
                if issue=='file':r['samples'][0]['input']['files']=[None]
                if issue=='digest':r['evidence'][0]['sha256']='invalid'
                write_json(saved,r);before=saved.read_bytes()
                with self.subTest(issue=issue),self.assertRaises(LabError):verify_sample(store,r['collection_id'],'a')
                self.assertEqual(saved.read_bytes(),before)
