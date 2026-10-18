"""Behavioral regressions for fixed-native, four-influence expression fitting."""
from pathlib import Path
import sys
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools/lib'))
import moe_expression_fit as module


class RefinementTests(unittest.TestCase):
    def inputs(self):
        positions = np.array([[i * 2., 0., z] for i in range(4) for z in (-1., 1.)])
        blink = np.zeros((8, 3))
        blink[::2, 2], blink[1::2, 2] = .01, -1.99
        smile = np.zeros((8, 3))
        smile[:, 0] = np.linspace(.1, .4, 8)
        weights = np.tile([.3, 0., .2, 0.], (8, 1))
        native = np.tile([.5, 0., 0., 0.], (8, 1))
        native[0] = [.25, .25, 0., 0.]  # This row already uses all four slots.
        triangles = {'face': np.array([[0, 1, 2], [3, 4, 5], [6, 7, 0]])}
        return weights, {'blink': blink, 'smile': smile}, {'blink': [0, 1], 'smile': [2, 3]}, positions, triangles, native, {'blink'}

    def test_native_and_budget_preserved_with_real_four_limit(self):
        args = self.inputs()
        old_weights, old_native = args[0].copy(), args[5].copy()
        result, translations, report = module.refine_expression_weights(*args, iterations=12)
        np.testing.assert_array_equal(args[0], old_weights)
        np.testing.assert_array_equal(args[5], old_native)
        np.testing.assert_allclose(result.sum(axis=1), old_weights.sum(axis=1), atol=1e-9, rtol=0)
        self.assertTrue(np.all((result > 0).sum(axis=1) + (old_native > 0).sum(axis=1) <= 4))
        self.assertTrue(np.all(result[old_weights > 0] > 0))
        self.assertEqual(result[0, 1], 0.)
        self.assertTrue(np.all(result[:, 3] == 0.))  # Spare support belongs only to blink.
        self.assertTrue(np.any(result[1:, 1] > 0))
        self.assertEqual(report['closure_contacts']['status'], 'available')
        self.assertEqual(report['vertices_over_four'], 0)
        self.assertEqual(set(translations), {'blink', 'smile'})

    def test_pairwise_objective_decreases_and_support_is_bounded(self):
        args = self.inputs()
        result, _, report = module.refine_expression_weights(*args, iterations=15, pair_weight=64., closure_weight=4.)
        costs = np.array([report['objective_initial'], *report['objective_history']])
        self.assertTrue(np.all(np.diff(costs) <= 1e-8))
        self.assertLess(costs[-1], costs[0])
        self.assertTrue(np.all((result > 0).sum(axis=1) + (args[5] > 0).sum(axis=1) <= 4))

    def test_native_only_contact_endpoint_remains_fixed(self):
        args = list(self.inputs())
        args[0][0] = 0.
        args[5][0] = [1., 0., 0., 0.]
        result, _, report = module.refine_expression_weights(*args, iterations=10, pair_weight=16.)
        np.testing.assert_array_equal(result[0], np.zeros(4))
        self.assertEqual(report['closure_contacts']['status'], 'available')

    def test_missing_contacts_are_reported_without_fabrication(self):
        args = list(self.inputs())
        args[4] = {}
        result, _, report = module.refine_expression_weights(*args, iterations=3, allow_blink_support=False)
        self.assertEqual(report['closure_contacts']['status'], 'unavailable')
        self.assertEqual(report['closure_contacts']['pairs'], [])
        self.assertEqual(report['added_blink_support_candidates'], 0)
        np.testing.assert_array_equal(result > 0, args[0] > 0)

    def test_input_fifth_influence_rejected_even_when_tiny(self):
        args = self.inputs()
        args[0][0, 1] = 1e-30
        with self.assertRaisesRegex(ValueError, 'four actual nonzero'):
            module.refine_expression_weights(*args)

    def test_invalid_input_sum_is_rejected_without_normalization(self):
        args = self.inputs()
        args[5][0, 0] += .01
        with self.assertRaisesRegex(ValueError, 'already sum to one'):
            module.refine_expression_weights(*args)


class SharedMouthTests(unittest.TestCase):
    def inputs(self):
        t = np.linspace(0., 1., 12)
        positions = np.column_stack((t, np.sin(t), np.zeros(12)))
        native = np.tile([.25, .25, 0., 0.], (12, 1))
        weights = np.zeros((12, 4))
        weights[:6, 0], weights[6:, 2] = .5, .5
        targets = {'open': np.column_stack((t * .2, t, -t * .1)),
                   'smile': np.column_stack((t * .5, -.4 * t, .1 + t * .2))}
        triangles = {'lip': np.array([[i, i + 1, i + 2] for i in range(10)])}
        return weights, targets, positions, triangles, native

    def test_overlapping_keys_recover_missing_support_with_fixed_native_weights(self):
        args = self.inputs()
        before, native = args[0].copy(), args[4].copy()
        result, drives, report = module.fit_shared_expression_region(*args, iterations=20)
        np.testing.assert_array_equal(args[0], before)
        np.testing.assert_array_equal(args[4], native)
        np.testing.assert_allclose(result.sum(axis=1), before.sum(axis=1), atol=1e-9, rtol=0)
        self.assertTrue(np.all((result > 0).sum(axis=1) + (native > 0).sum(axis=1) <= 4))
        errors = [np.sum((result @ drives[key] - value) ** 2) for key, value in args[1].items()]
        old_error = sum(np.sum(value[np.arange(12) >= 6 if key == 'open' else np.arange(12) < 6] ** 2)
                        for key, value in args[1].items())
        self.assertLess(sum(errors), old_error * .1)
        self.assertEqual(report['positive_weights_below_export_floor'], 0)
        self.assertEqual(report['vertices_over_four'], 0)

    def test_tiny_fifth_positive_influence_is_rejected(self):
        args = list(self.inputs())
        args[0][0, 1:3] = [1e-30, 1e-30]
        with self.assertRaisesRegex(ValueError, 'four-influence'):
            module.fit_shared_expression_region(*args, iterations=2)

    def test_invalid_sum_is_not_repaired(self):
        args = list(self.inputs())
        args[4][0, 0] += .02
        with self.assertRaisesRegex(ValueError, 'already sum to one'):
            module.fit_shared_expression_region(*args, iterations=2)

    def test_no_weight_budget_stays_native_only(self):
        args = list(self.inputs())
        args[0][0] = 0.
        args[4][0] = [1., 0., 0., 0.]
        result, _, _ = module.fit_shared_expression_region(*args, iterations=3)
        np.testing.assert_array_equal(result[0], np.zeros(4))


if __name__ == '__main__':
    unittest.main()
