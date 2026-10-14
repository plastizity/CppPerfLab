import copy
import unittest
from cppperflab.adapters import REGISTRY
from cppperflab.metrics import catalog, convert, derive_sample, evidence_state, observation
from cppperflab.errors import LabError
from cppperflab.protocol import metric


def counters():
    return {'index':0, 'metrics':[metric(n,v,'count','hardware-counter','target','raw.txt',running_percent=100)
            for n,v in [('cpu.instructions',1000),('cpu.cycles',500),('cpu.branches',200),('cpu.branch_misses',20)]]}


class MetricTests(unittest.TestCase):
    def test_catalog_discovers_providers(self):
        entries=catalog(REGISTRY,metric_id='cpu.ipc')['metrics']
        self.assertEqual(entries[0]['providers'],['perf-stat'])
        self.assertIn('memory.heap_peak_observed', {m['metric_id'] for m in catalog(REGISTRY,domain='D04')['metrics']})
        with self.assertRaises(LabError): catalog(REGISTRY,metric_id='imaginary')

    def test_units(self):
        self.assertAlmostEqual(convert(1000000000,'ns','seconds'),1)
        self.assertEqual(convert(2,'KiB','bytes'),2048)
        self.assertEqual(convert(20,'%','ratio'),.2)
        with self.assertRaises(LabError): convert(1,'bytes','seconds')

    def test_formula_and_raw_operands(self):
        values=derive_sample(counters())
        self.assertEqual([m['value'] for m in values],[2,.5,20,.1])
        self.assertTrue(all(m['evidence_state']=='Measured' for m in values))
        self.assertEqual(values[0]['sources'][0]['evidence'],'raw.txt')

    def test_reject_incompatible_pairs(self):
        for field,value in [('scope','other'),('unit','bytes'),('running_percent',50),('value',0),('event','cycles:u')]:
            s=counters();s['metrics'][1][field]=value
            self.assertEqual(derive_sample(s)[0]['evidence_state'],'Invalid',field)
        s=counters();s['metrics'].pop(1)
        self.assertEqual(derive_sample(s)[0]['evidence_state'],'Missing')
        s=counters();s['metrics'].append(copy.deepcopy(s['metrics'][0]))
        self.assertEqual(derive_sample(s)[0]['evidence_state'],'Invalid')

    def test_states_never_promote_predictions(self):
        for state in ('Estimated','Modeled'):
            s=counters();s['metrics'][0]['evidence_state']=state
            self.assertEqual(derive_sample(s)[0]['evidence_state'],state)
        for state in ('Unsupported','Missing','Invalid'):
            self.assertEqual(evidence_state(observation('x',state,reason='test')),state)
            with self.assertRaises(LabError): observation('x',state,value=0,reason='test')
        old=counters()['metrics'][0];old.pop('evidence_state');old.pop('evidence_version')
        self.assertEqual(evidence_state(old),'Measured')
        old['method']='unknown-static-predictor'
        self.assertEqual(evidence_state(old),'Invalid')

    def test_acceptance_policy_and_hard_correctness(self):
        from test_core import comparison_result
        from cppperflab.comparison import compare
        a=comparison_result('a');b=comparison_result('b',values=[.5]*7)
        policy={'schema_version':1,'min_samples':7,'max_cv':.02,'max_regression_fraction':.03,'min_speedup':1.1}
        self.assertEqual(compare(a,b,policy=policy)['acceptance']['status'],'PASS')
        b['correctness']['status']='failed';a['correctness']['status']='failed'
        with self.assertRaises(LabError) as caught:compare(a,b,policy=policy)
        self.assertEqual(caught.exception.details['acceptance']['status'],'FAIL')
        a=comparison_result('a');b=comparison_result('b',values=[2]*7)
        self.assertEqual(compare(a,b,policy=policy)['acceptance']['status'],'FAIL')
        policy['min_samples']=8
        self.assertEqual(compare(a,b,policy=policy)['acceptance']['status'],'INCONCLUSIVE')
        policy['max_cv']=float('nan')
        with self.assertRaises(LabError):compare(a,b,policy=policy)

    def test_comparison_blocks_model_and_missing_evidence(self):
        from test_core import comparison_result
        from cppperflab.comparison import compare
        a=comparison_result('a');b=comparison_result('b')
        for state in ('Modeled','Estimated','Unsupported','Missing','Invalid'):
            changed=copy.deepcopy(b)
            m=changed['samples'][0]['metrics'][0];m['evidence_state']=state
            if state in ('Unsupported','Missing','Invalid'):m.update(value=None,reason='test')
            with self.assertRaises(LabError) as caught:compare(a,changed)
            self.assertEqual(caught.exception.details['acceptance']['status'],
                             'UNSUPPORTED' if state=='Unsupported' else 'INCONCLUSIVE')

    def test_growth_ratios_and_semantics(self):
        from cppperflab.studies import fit_growth
        pts=[dict(x=n,median=n*n,n=9,cv=0,name='time.wall',unit='seconds',method='native',scope='process',run_id=str(n)) for n in (1,2,4,8)]
        growth=fit_growth(pts)
        self.assertEqual([x['value'] for x in growth['doubling_ratios']],[4]*3)
        self.assertEqual(growth['models'][0]['evidence_state'],'Modeled')
        self.assertEqual(growth['local_slopes'][0]['empirical_log_slope'],2)
        pts[-1]['unit']='ns'
        with self.assertRaises(LabError):fit_growth(pts)

    def test_descriptive_statistics_do_not_mix_states(self):
        from cppperflab.lab import summarize
        items=[metric('x',1,'count','hardware-counter','scope','raw'),
               metric('x',100,'count','hardware-counter','scope','raw',evidence_state='Modeled')]
        summaries=summarize([{'metrics':items}])
        self.assertEqual(len(summaries),2)
        self.assertEqual([s['n'] for s in summaries],[1,1])

    def test_cli_query_and_acceptance_archive(self):
        import io,json,tempfile
        from pathlib import Path
        from unittest.mock import patch
        from cppperflab.cli import main
        from test_views import ViewTests,BASELINE,CANDIDATE
        with tempfile.TemporaryDirectory() as temp:
            store,_=ViewTests().setup_run(temp)
            _,_=ViewTests().setup_run(temp,CANDIDATE,values=[.5]*7)
            policy=Path(temp)/'policy.json';policy.write_text(json.dumps(dict(schema_version=1,min_samples=7,max_cv=.1,max_regression_fraction=.05,min_speedup=1.1)))
            for argv in (['metrics','--metric','cpu.cpi'],['schema','evidence'],
                         ['compare',BASELINE,CANDIDATE,'--policy',str(policy)],
                         ['analyze',BASELINE,'--section','observations','--metric','cpu.ipc']):
                out=io.StringIO()
                with patch('sys.argv',['perflab',*argv,'--store',str(store.root),'--json']),patch('sys.stdout',out):
                    self.assertEqual(main(),0)
                result=json.loads(out.getvalue())['result']
                if argv[0]=='compare':
                    self.assertEqual(result['acceptance']['status'],'PASS')
                    self.assertEqual(store.read(result['comparison_id'],'comparison')['acceptance_policy']['min_speedup'],1.1)
                if argv[0]=='analyze':
                    self.assertEqual(result['data'][0]['evidence_state'],'Missing')
                    self.assertIsNone(result['data'][0]['value'])

    def test_derived_comparison_uses_same_core_and_direction(self):
        from test_core import comparison_result
        from cppperflab.comparison import compare
        a=comparison_result('a');b=comparison_result('b')
        a['samples']=[counters() for _ in range(7)];b['samples']=copy.deepcopy(a['samples'])
        for sample in b['samples']:sample['metrics'][0]['value']*=2
        result=compare(a,b,'cpu.ipc')
        self.assertEqual(result['acceptance']['status'],'INCONCLUSIVE')
        result=compare(a,b,'cpu.ipc',policy=dict(schema_version=1,min_samples=7,max_cv=.1,max_regression_fraction=.05,direction='higher',min_speedup=1.5))
        self.assertEqual(result['acceptance']['status'],'PASS')
        self.assertEqual(result['speedup'],.5) # legacy baseline/candidate ratio unchanged
        self.assertEqual(result['acceptance']['benefit_interval'],[2,2])

    def test_derived_comparison_keeps_operand_event_semantics(self):
        from test_core import comparison_result
        from cppperflab.comparison import compare
        a=comparison_result('a');b=comparison_result('b')
        a['samples']=[counters() for _ in range(7)];b['samples']=copy.deepcopy(a['samples'])
        for sample in b['samples']:
            for m in sample['metrics']:m['event']=m['name']+':u'
        self.assertEqual(derive_sample(b['samples'][0])[0]['evidence_state'],'Measured')
        with self.assertRaises(LabError):compare(a,b,'cpu.ipc')

    def test_comparison_rejects_partial_and_duplicate_samples(self):
        from test_core import comparison_result
        from cppperflab.comparison import compare
        a=comparison_result('a');b=comparison_result('b')
        b['samples'][0]['metrics']=[]
        with self.assertRaises(LabError):compare(a,b)
        b=comparison_result('b');b['samples'][0]['metrics']*=2
        with self.assertRaises(LabError):compare(a,b)

    def test_projection_preserves_legacy_file_and_unknown_state(self):
        import tempfile
        from test_views import ViewTests,BASELINE
        from cppperflab.views import build_view
        with tempfile.TemporaryDirectory() as temp:
            store,run=ViewTests().setup_run(temp)
            path=store.record_path(BASELINE);before=path.read_bytes()
            view=build_view(store,BASELINE)
            self.assertEqual(view['observations'][0]['evidence_state'],'Measured')
            self.assertEqual(path.read_bytes(),before)
            from cppperflab.metrics import run_observations
            run['samples'][0]['metrics'][0]['method']='unknown simulation'
            item=run_observations(run)[0]
            self.assertEqual(item['evidence_state'],'Invalid')
            self.assertIsNone(item['value'])

    def test_failed_capability_records_unsupported(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from test_core import request_template
        from cppperflab.lab import run
        from cppperflab.storage import Store
        with tempfile.TemporaryDirectory() as temp:
            request=request_template(temp)
            with patch('cppperflab.lab.plan',return_value={'selected':None,'reason':'test capability denied'}):
                result=run(request,Store(Path(temp)/'store'))
            self.assertEqual(result['status'],'failed')
            self.assertEqual(result['unavailable_observations'][0]['evidence_state'],'Unsupported')
            self.assertIsNone(result['unavailable_observations'][0]['value'])
            self.assertTrue((Path(result['evidence_directory'])/'plan.json').is_file())

    def test_missing_counter_coverage_is_not_full_coverage(self):
        sample=counters();sample['metrics'][1].pop('running_percent')
        self.assertEqual(derive_sample(sample)[0]['evidence_state'],'Invalid')
        sample=counters();sample['metrics'][1]['running_percent']=float('nan')
        self.assertEqual(derive_sample(sample)[0]['evidence_state'],'Invalid')

    def test_perf_unavailable_preserves_old_contract_and_new_state(self):
        import tempfile
        from pathlib import Path
        from cppperflab.adapters import parse_stat
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'perf.csv'
            path.write_text('<not supported>;;cycles;0;100\n')
            result=parse_stat(path,['cycles'])
            self.assertEqual(result['unavailable_metrics'],[{'event':'cycles','reason':'<not supported>'}])
            self.assertEqual(result['unavailable_observations'][0]['evidence_state'],'Unsupported')
            path.write_text('1;;cycles;0;nan\n')
            with self.assertRaises(LabError):parse_stat(path,['cycles'])

    def test_study_rejects_partial_hardware_counts_before_fitting(self):
        import tempfile,sys
        from pathlib import Path
        from unittest.mock import patch
        from test_core import request_template
        from cppperflab.studies import study
        from cppperflab.storage import Store
        from cppperflab.lab import summarize
        with tempfile.TemporaryDirectory() as temp:
            template=request_template(temp)
            spec=dict(name='partial',template='unused',axis=dict(parameter='n',values=[1,2,4,8],argument_index=2),
                      variants=[dict(id='base',executable=sys.executable,revision='r',artifacts=[])],budget=4,seed=1,metric='cpu.instructions')
            sample=counters();sample['metrics'][0]['running_percent']=50
            result=dict(run_id='test',status='completed',samples=[sample],summary=summarize([sample]))
            with patch('cppperflab.studies.load_study',return_value=(spec,template)),patch('cppperflab.studies.run',return_value=result):
                report=study(Path(temp)/'unused',Store(Path(temp)/'store'))
            self.assertEqual(report['status'],'failed')
            self.assertEqual(report['growth'],{})
