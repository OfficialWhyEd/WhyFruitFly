"""Tripod locomotion controller preserved from prove/02-cammina.py."""

from __future__ import annotations

import numpy as np
from flygym.anatomy import LEGS

STEP_FREQ_HZ = 4.0
AMP_YAW = np.deg2rad(22)
AMP_LIFT = np.deg2rad(16)
AMP_TIBIA = np.deg2rad(14)

GROUP_A = {"lf", "rm", "lh"}


def leg_phase(leg: str, t: float, frequency_hz: float = STEP_FREQ_HZ) -> float:
    offset = 0.0 if leg in GROUP_A else np.pi
    return 2 * np.pi * frequency_hz * t + offset


def compute_position_targets(
    fly: object,
    jointdof_order: list[object],
    t: float,
    frequency_hz: float = STEP_FREQ_HZ,
) -> np.ndarray:
    targets = np.zeros(len(jointdof_order))
    for index, jointdof in enumerate(jointdof_order):
        neutral = fly.jointdof_to_neutralangle[jointdof]
        leg = jointdof.child.pos
        link = jointdof.child.link
        axis = jointdof.axis.value
        if leg not in LEGS:
            targets[index] = neutral
            continue

        phase = leg_phase(leg, t, frequency_hz)
        delta = 0.0
        if link == "coxa" and axis == "yaw":
            delta = AMP_YAW * (-np.cos(phase))
        elif link == "trochanterfemur" and axis == "pitch":
            delta = AMP_LIFT * max(0.0, np.sin(phase))
        elif link == "tibia" and axis == "pitch":
            delta = AMP_TIBIA * np.cos(phase)
        targets[index] = neutral + delta
    return targets


def compute_adhesion(t: float, frequency_hz: float = STEP_FREQ_HZ) -> np.ndarray:
    return np.asarray(
        [0.0 if np.sin(leg_phase(leg, t, frequency_hz)) > 0.0 else 1.0 for leg in LEGS]
    )

