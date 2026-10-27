"""Synthetic concurrency comparison contracts; fixtures are not measurements."""
import unittest

from cppperflab.errors import LabError
from cppperflab.comparison import validated_samples
from cppperflab.paired import compare_blocks
from test_paired import POLICY, blocks
from test_core import comparison_result


class ConcurrentComparisonTests(unittest.TestCase):
    def data(self):
        result=blocks('AB',3)
        conditions={'concurrent_runs':2,'background_load':'uncontrolled','notes':'synthetic',
                    'synchronization_timeout_seconds':5,'minimum_overlap_fraction':.75,
                    'threads_per_variant':{'baseline':1,'candidate':1}}
        for block in result:
            for member in block['members']:
                member['run']['execution_context']={'kind':'concurrent_ab','concurrent_runs':2,
                    'competition_pair_identity':{'baseline':'a','candidate':'b'},'resource_conditions':dict(conditions),
                    'status':'PASS','overlap_fraction':.9}
        return result

    def test_competing_context_is_reported_as_scope(self):
        result=compare_blocks(self.data(),'time.wall',POLICY)
        self.assertEqual(result['execution_context']['kind'],'concurrent_ab')
        self.assertEqual(result['execution_context']['scope'],'competing pair')
        self.assertTrue(any('isolated serial speedup' in item for item in result['limitations']))

    def test_inadequate_overlap_has_no_score(self):
        data=self.data()
        data[0]['members'][0]['run']['execution_context']['overlap_fraction']=.5
        with self.assertRaises(LabError):
            compare_blocks(data,'time.wall',POLICY)

    def test_serial_and_concurrent_members_cannot_mix(self):
        data=self.data()
        data[-1]['members'][1]['run'].pop('execution_context')
        with self.assertRaises(LabError):
            compare_blocks(data,'time.wall',POLICY)

    def test_competition_pair_identity_must_match(self):
        data=self.data()
        data[1]['members'][0]['run']['execution_context']['competition_pair_identity']['candidate']='different'
        with self.assertRaises(LabError):
            compare_blocks(data,'time.wall',POLICY)

    def test_direct_comparison_cannot_bypass_overlap_gate(self):
        run=comparison_result('synthetic',values=[1])
        run['execution_context']={'kind':'concurrent_ab','concurrent_runs':2,
            'competition_pair_identity':{'baseline':'a','candidate':'b'},
            'resource_conditions':{'minimum_overlap_fraction':.75},
            'status':'INCONCLUSIVE','overlap_fraction':.9}
        with self.assertRaises(LabError):
            validated_samples(run,run,'time.wall')
