import unittest
from test_core import comparison_result
from cppperflab.comparison import compare, normalized_cost
from cppperflab.performance_summary import comparison_finding


class NormalizedCostTests(unittest.TestCase):
    def result(self, candidate=0.7, **kwargs):
        return compare(comparison_result('a'), comparison_result('b', values=[candidate]*7), **kwargs)

    def test_time_cost_and_interval(self):
        r = self.result()
        c = r['normalized_cost']
        self.assertAlmostEqual(c['value'], .7)
        self.assertEqual(c['baseline'], 1)
        self.assertEqual(c['interval_95_percent'], [.7,.7])
        self.assertEqual(c['acceptance_status'], 'PASS')
        self.assertEqual(c['evidence_state'], 'Estimated')
        self.assertEqual(comparison_finding(r)['normalized_cost'], c)

    def test_regression_remains_visible_but_fails(self):
        c = self.result(1.2)['normalized_cost']
        self.assertEqual(c['value'], 1.2)
        self.assertEqual(c['acceptance_status'], 'FAIL')

    def test_higher_objective_inverts_cost_direction(self):
        policy = dict(schema_version=1,min_samples=7,max_cv=.1,max_regression_fraction=.05,direction='higher')
        c = self.result(2, policy=policy)['normalized_cost']
        self.assertEqual(c['value'], .5)
        self.assertEqual(c['interval_95_percent'], [.5,.5])

    def test_interval_inversion_and_noisy_observation(self):
        r = compare(comparison_result('a',values=[.5,1,2]*3), comparison_result('b'))
        c = r['normalized_cost']
        lo,hi = r['bootstrap_95_percent_speedup']
        self.assertEqual(c['interval_95_percent'], [1/hi,1/lo])
        self.assertEqual(c['acceptance_status'], 'INCONCLUSIVE')
        self.assertIsNotNone(c['value'])

    def test_rejected_and_legacy_records_unscored(self):
        r = self.result()
        del r['input_evidence_state']
        self.assertIsNone(normalized_cost(r)['value'])
        for status in ('FAIL','INCONCLUSIVE','UNSUPPORTED'):
            c = normalized_cost(dict(metric='time.wall',comparable=False,acceptance=dict(status=status,reason='gate')))
            self.assertIsNone(c['value'])
            self.assertEqual(c['acceptance_status'], status)

    def test_diagnostic_has_no_implicit_objective(self):
        # Projection alone does not assign an optimization objective to IPC.
        r = self.result();r['metric']='cpu.ipc'
        self.assertIsNone(normalized_cost(r)['value'])
