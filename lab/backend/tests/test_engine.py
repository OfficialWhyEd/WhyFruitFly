from __future__ import annotations

import unittest

from fruitfly_lab import EngineStatus, FlyGymLocomotionEngine


class EngineTests(unittest.TestCase):
    def test_shapes_and_state_machine(self) -> None:
        with FlyGymLocomotionEngine() as engine:
            metadata = engine.metadata()
            self.assertEqual(len(metadata.body_names), 69)
            self.assertEqual(len(metadata.joint_names), 126)
            self.assertEqual(len(metadata.actuator_names), 42)
            self.assertEqual(len(metadata.leg_names), 6)

            self.assertEqual(engine.start(), EngineStatus.RUNNING)
            snapshot = engine.step(10)
            self.assertEqual(snapshot.sequence, 10)
            self.assertAlmostEqual(snapshot.sim_time_s, 10 * engine.timestep_s)
            self.assertEqual(len(snapshot.body_pos_mm), 69)
            self.assertEqual(len(snapshot.joint_angle_rad), 126)
            self.assertEqual(len(snapshot.actuator_force), 42)
            self.assertEqual(len(snapshot.contact_found), 6)

            self.assertEqual(engine.pause(), EngineStatus.PAUSED)
            paused = engine.step(10)
            self.assertEqual(paused.sequence, 10)
            self.assertEqual(engine.reset(), EngineStatus.READY)
            self.assertEqual(engine.snapshot().sequence, 0)


if __name__ == "__main__":
    unittest.main()
