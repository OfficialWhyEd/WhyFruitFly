from __future__ import annotations

import unittest

from connectome_adapter import AsyncConnectomeAdapter, EpisodeRequest


class AdapterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.adapter = AsyncConnectomeAdapter(model="fake", default_timeout_s=3.0)

    async def asyncTearDown(self) -> None:
        self.adapter.close()

    async def test_sugar_extends_proboscis_without_starting_walk(self) -> None:
        result = await self.adapter.decide(EpisodeRequest("sugar", seed=11))
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.map_id, "fruitfly-behavior-v1")
        self.assertEqual(result.behavior.locomotion, "stop")
        self.assertTrue(result.behavior.extend_proboscis)
        self.assertEqual(result.behavior.turn, 0.0)
        self.assertGreater(result.rates_hz["proboscis_motor"], 20.0)
        archive_spikes = result.to_archive_record()["spikes"]
        self.assertGreater(len(archive_spikes), 0)
        self.assertIsInstance(archive_spikes[0]["flywire_id"], str)

    async def test_walk_and_turn_are_behavioral_not_actuator_commands(self) -> None:
        straight = await self.adapter.decide(EpisodeRequest("walk"))
        left = await self.adapter.decide(EpisodeRequest("turn_left"))
        right = await self.adapter.decide(EpisodeRequest("turn_right"))
        self.assertEqual(straight.behavior.locomotion, "go")
        self.assertEqual(straight.behavior.turn, 0.0)
        self.assertLess(left.behavior.turn, 0.0)
        self.assertGreater(right.behavior.turn, 0.0)
        self.assertNotIn("actuator", left.to_archive_record()["decision"])

    async def test_halt_population_overrides_walk_population(self) -> None:
        result = await self.adapter.decide(EpisodeRequest("halt"))
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.behavior.locomotion, "stop")

    async def test_timeout_is_explicit_and_next_episode_recovers(self) -> None:
        generation = self.adapter.generation
        timed_out = await self.adapter.decide(
            EpisodeRequest("slow"), timeout_s=0.05
        )
        self.assertEqual(timed_out.status, "timeout")
        self.assertEqual(timed_out.behavior.locomotion, "stop")
        self.assertTrue(timed_out.worker_restarted)
        self.assertGreater(self.adapter.generation, generation)
        recovered = await self.adapter.decide(EpisodeRequest("walk"), timeout_s=3.0)
        self.assertEqual(recovered.status, "ok")
        self.assertEqual(recovered.behavior.locomotion, "go")

    async def test_worker_error_is_explicit_and_recoverable(self) -> None:
        failed = await self.adapter.decide(EpisodeRequest("fail"))
        self.assertEqual(failed.status, "error")
        self.assertIn("requested fake backend failure", failed.error or "")
        self.assertTrue(failed.worker_restarted)
        recovered = await self.adapter.decide(EpisodeRequest("sugar"))
        self.assertEqual(recovered.status, "ok")


if __name__ == "__main__":
    unittest.main()
