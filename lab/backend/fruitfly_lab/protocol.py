"""Versioned messages shared by the engine, server and recorder."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

PROTOCOL_VERSION = 1


@dataclass(frozen=True, slots=True)
class ModelMetadata:
    protocol_version: int
    body_names: tuple[str, ...]
    joint_names: tuple[str, ...]
    actuator_names: tuple[str, ...]
    leg_names: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class FlySnapshot:
    protocol_version: int
    sequence: int
    status: str
    sim_time_s: float
    body_pos_mm: list[list[float]]
    body_quat_wxyz: list[list[float]]
    joint_angle_rad: list[float]
    joint_velocity_rad_s: list[float]
    actuator_force: list[float]
    contact_found: list[bool]
    contact_force_contact_frame: list[list[float]]
    contact_pos_mm_world: list[list[float]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

