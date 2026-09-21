from __future__ import annotations

import unittest

import numpy as np

from fruitfly_lab.controller import compute_adhesion, leg_phase


class ControllerTests(unittest.TestCase):
    def test_tripod_groups_are_opposite(self) -> None:
        self.assertAlmostEqual(
            (leg_phase("rf", 0.0) - leg_phase("lf", 0.0)) % (2 * np.pi),
            np.pi,
        )

    def test_adhesion_has_one_tripod_per_phase(self) -> None:
        adhesion = compute_adhesion(0.03125)
        self.assertEqual(int(np.count_nonzero(adhesion)), 3)


if __name__ == "__main__":
    unittest.main()

