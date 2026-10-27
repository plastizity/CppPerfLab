"""Descriptor compatibility fixtures are not PMU measurements."""
import copy
from pathlib import Path
import tempfile
from unittest import mock
import unittest

from cppperflab.comparison import compare
from cppperflab.errors import LabError
from cppperflab.lab import summarize
from cppperflab.protocol import metric
from cppperflab.studies import fit_growth
from cppperflab.validity import evaluate_comparison_validity
from cppperflab.views import run_view
from test_core import comparison_result
from test_core import request_template
from cppperflab.lab import run
from cppperflab.storage import Store
from test_validity import claim
from test_views import run_result


def counter(key='a'*64, index=0):
    return metric('cpu.l1d_loads',100,'count','hardware-counter','target process',f'measurement-{index:03d}/perf.csv',
        event='L1-dcache-loads:u',running_percent=100,event_descriptor_id='linux_hw_cache_alias:L1-dcache-loads',
        event_descriptor_version=1,event_semantics_key=key,
        event_descriptor_evidence=f'measurement-{index:03d}/event-descriptors.json')


def runs():
    a,b=comparison_result('a'),comparison_result('b')
    for r in (a,b):
        r['request']['claim']={**claim(),'metric':'cpu.l1d_loads'}
        for index,s in enumerate(r['samples']):s['metrics'].append(counter(index=index))
    return a,b


class HierarchyComparisonTests(unittest.TestCase):
    def test_failed_collection_keeps_descriptor_reference_inside_measurement(self):
        class Adapter:
            id='perf-stat';batch=False
            def describe(self):return {'id':'perf-stat','mode':'fixture'}
            def prepare(self,command,*args,**kwargs):return command
            def parse(self,folder,options,record):
                (folder/'event-descriptors.json').write_text('{}')
                (folder/'perf.csv').write_text('<not supported>;;L1-dcache-load-misses;0;0\n')
                raise LabError('unavailable','Fixture unavailable collection',{'unavailable_observations':[
                    dict(name='cpu.l1d_load_misses',value=None,evidence_state='Unsupported',evidence_version=1,
                        reason='fixture',evidence='perf.csv',event_descriptor_evidence='event-descriptors.json')]})
        with tempfile.TemporaryDirectory() as temp:
            request=request_template(temp);request['measurement'].update(tool='perf-stat',warmups=0)
            plan={'selected':'perf-stat','probes':{'tools':{'perf-stat':{'version':'fixture'}}}}
            with mock.patch('cppperflab.lab.plan',return_value=plan),mock.patch.dict('cppperflab.lab.REGISTRY',{'perf-stat':Adapter()}):
                result=run(request,Store(Path(temp)/'store'))
            row=result['unavailable_observations'][0]
            self.assertEqual(row['event_descriptor_evidence'],'measurement-000/event-descriptors.json')
            self.assertTrue((Path(result['evidence_directory'])/row['event_descriptor_evidence']).is_file())
            self.assertEqual(row['evidence_state'],'Unsupported')

    def test_matching_descriptors_reuse_existing_comparison_and_validity(self):
        a,b=runs();result=compare(a,b,'cpu.l1d_loads')
        self.assertTrue(result['comparable'])
        self.assertEqual(result['validity']['status'],'PASS')

    def test_missing_or_mismatched_descriptor_cannot_pass(self):
        for field,value in [('event_semantics_key',None),('event_descriptor_id','other'),
                            ('event_descriptor_version',True),('event_descriptor_evidence',''),
                            ('event','cache-references:u')]:
            a,b=runs();b['samples'][0]['metrics'][-1][field]=value
            with self.subTest(field=field),self.assertRaises(LabError) as caught:compare(a,b,'cpu.l1d_loads')
            self.assertEqual(caught.exception.details['acceptance']['status'],'INCONCLUSIVE')
            self.assertEqual(evaluate_comparison_validity(a,b,'cpu.l1d_loads')['status'],'INCONCLUSIVE')
        for all_samples in (False,True):
            a,b=runs()
            for s in (b['samples'] if all_samples else b['samples'][:1]):s['metrics'][-1]['event_semantics_key']='b'*64
            with self.subTest(all_samples=all_samples),self.assertRaises(LabError):compare(a,b,'cpu.l1d_loads')
            self.assertEqual(evaluate_comparison_validity(a,b,'cpu.l1d_loads')['status'],'INCONCLUSIVE')

    def test_summaries_and_view_do_not_pool_different_descriptors(self):
        a,_=runs();a['samples']=a['samples'][:2]
        a['samples'][1]['metrics'][-1]['event_semantics_key']='b'*64
        a['summary']=summarize(a['samples'])
        summaries=[s for s in a['summary'] if s['name']=='cpu.l1d_loads']
        self.assertEqual([s['n'] for s in summaries],[1,1])
        with tempfile.TemporaryDirectory() as temp:
            fixture=run_result(a['run_id'],Path(temp))
            fixture['samples']=a['samples'];fixture['summary']=a['summary']
            for index,s in enumerate(fixture['samples']):
                s['index']=index;s['metrics'][0]['evidence']='raw.txt'
            view=run_view(fixture)
        groups=[r for r in view['metrics'] if r['summary']['name']=='cpu.l1d_loads']
        self.assertEqual([len(r['samples']) for r in groups],[1,1])

    def test_growth_cannot_combine_new_contexts(self):
        points=[dict(x=n,median=100*n,n=7,cv=0,name='cpu.l1d_loads',unit='count',method='hardware-counter',scope='target',
                     event_descriptor_id='linux_hw_cache_alias:L1-dcache-loads',event_descriptor_version=1,
                     event_semantics_key='a'*64) for n in (1,2,3,4)]
        fit_growth(points)
        changed=copy.deepcopy(points);changed[-1]['event_semantics_key']='b'*64
        with self.assertRaises(LabError):fit_growth(changed)
        del changed[-1]['event_semantics_key']
        with self.assertRaises(LabError):fit_growth(changed)


if __name__=='__main__':unittest.main()
