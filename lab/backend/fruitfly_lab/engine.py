"""Single-owner FlyGym locomotion engine."""

from __future__ import annotations

import os
from enum import StrEnum

# On this Windows machine GLFW is the verified MuJoCo backend.
os.environ.setdefault("MUJOCO_GL", "glfw")

import mujoco as mj
from flygym import Simulation
from flygym.anatomy import ActuatedDOFPreset, JointPreset, Skeleton
from flygym.compose.fly import ActuatorType, NeuroMechFly
from flygym.compose.pose import KinematicPosePreset
from flygym.compose.world import FlatGroundWorld
from flygym.utils.math import Rotation3D

from .controller import compute_adhesion, compute_position_targets
from .protocol import FlySnapshot, ModelMetadata, PROTOCOL_VERSION


class EngineStatus(StrEnum):
    READY = "ready"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    CLOSED = "closed"


def _name(value: object) -> str:
    for attribute in ("name", "value"):
        candidate = getattr(value, attribute, None)
        if isinstance(candidate, str):
            return candidate
    return str(value)


def build_fly() -> NeuroMechFly:
    fly = NeuroMechFly(name="fly")
    fly.colorize()
    skeleton = Skeleton(
        axis_order=fly.AXIS_ORDER_CLASS.DONTCARE,
        joint_preset=JointPreset.ALL_BIOLOGICAL,
    )
    neutral_pose = KinematicPosePreset.NEUTRAL
    fly.add_joints(skeleton, neutral_pose=neutral_pose)
    actuated_dofs = skeleton.get_actuated_dofs_from_preset(
        ActuatedDOFPreset.LEGS_ACTIVE_ONLY
    )
    fly.add_actuators(
        actuated_dofs,
        ActuatorType.POSITION,
        neutral_input=neutral_pose,
        kp=25.0,
    )
    fly.add_leg_adhesion(gain=8.0)
    return fly


class FlyGymLocomotionEngine:
    """Owns all MuJoCo state and must only be called from one process/thread."""

    settle_duration_s = 0.1

    def __init__(self, step_frequency_hz: float = 4.0) -> None:
        self.step_frequency_hz = float(step_frequency_hz)
        self.fly = build_fly()
        world = FlatGroundWorld()
        world.add_fly(
            self.fly,
            spawn_position=(0.0, 0.0, 1.0),
            spawn_rotation=Rotation3D("quat", (1.0, 0.0, 0.0, 0.0)),
        )
        self.sim = Simulation(world)
        self.position_order = self.fly.get_actuated_jointdofs_order(
            ActuatorType.POSITION
        )
        self.body_order = self.fly.get_bodysegs_order()
        self.joint_order = self.fly.get_jointdofs_order()
        self.leg_order = self.fly.get_legs_order()
        self.status = EngineStatus.READY
        self.sequence = 0
        mj.mj_forward(self.sim.mj_model, self.sim.mj_data)

    @property
    def timestep_s(self) -> float:
        return float(self.sim.mj_model.opt.timestep)

    def metadata(self) -> ModelMetadata:
        return ModelMetadata(
            protocol_version=PROTOCOL_VERSION,
            body_names=tuple(_name(item) for item in self.body_order),
            joint_names=tuple(_name(item) for item in self.joint_order),
            actuator_names=tuple(_name(item) for item in self.position_order),
            leg_names=tuple(str(item) for item in self.leg_order),
        )

    def start(self) -> EngineStatus:
        self._ensure_open()
        self.status = EngineStatus.RUNNING
        return self.status

    def pause(self) -> EngineStatus:
        self._ensure_open()
        if self.status is EngineStatus.RUNNING:
            self.status = EngineStatus.PAUSED
        return self.status

    def stop(self) -> EngineStatus:
        self._ensure_open()
        self.status = EngineStatus.STOPPED
        return self.status

    def reset(self) -> EngineStatus:
        self._ensure_open()
        self.sim.reset()
        mj.mj_forward(self.sim.mj_model, self.sim.mj_data)
        self.sequence = 0
        self.status = EngineStatus.READY
        return self.status

    def step(self, count: int = 1) -> FlySnapshot:
        self._ensure_open()
        if count < 0:
            raise ValueError("count must be non-negative")
        for _ in range(count):
            if self.status is not EngineStatus.RUNNING:
                break
            t = float(self.sim.mj_data.time)
            control_t = 0.0 if t < self.settle_duration_s else t - self.settle_duration_s
            targets = compute_position_targets(
                self.fly, self.position_order, control_t, self.step_frequency_hz
            )
            adhesion = (
                compute_adhesion(control_t, self.step_frequency_hz)
                if t >= self.settle_duration_s
                else [1.0] * len(self.leg_order)
            )
            self.sim.set_actuator_inputs(
                self.fly.name, ActuatorType.POSITION, targets
            )
            self.sim.set_leg_adhesion_states(self.fly.name, adhesion)
            self.sim.step()
            self.sequence += 1
        return self.snapshot()

    def snapshot(self) -> FlySnapshot:
        self._ensure_open()
        found, forces, _torques, positions, _normals, _tangents = (
            self.sim.get_ground_contact_info(self.fly.name)
        )
        return FlySnapshot(
            protocol_version=PROTOCOL_VERSION,
            sequence=self.sequence,
            status=self.status.value,
            sim_time_s=float(self.sim.mj_data.time),
            body_pos_mm=self.sim.get_body_positions(self.fly.name).copy().tolist(),
            body_quat_wxyz=self.sim.get_body_rotations(self.fly.name).copy().tolist(),
            joint_angle_rad=self.sim.get_joint_angles(self.fly.name).copy().tolist(),
            joint_velocity_rad_s=self.sim.get_joint_velocities(self.fly.name).copy().tolist(),
            actuator_force=self.sim.get_actuator_forces(
                self.fly.name, ActuatorType.POSITION
            ).copy().tolist(),
            contact_found=found.copy().astype(bool).tolist(),
            contact_force_contact_frame=forces.copy().tolist(),
            contact_pos_mm_world=positions.copy().tolist(),
        )

    def close(self) -> None:
        if self.status is not EngineStatus.CLOSED:
            self.sim.close()
            self.status = EngineStatus.CLOSED

    def _ensure_open(self) -> None:
        if self.status is EngineStatus.CLOSED:
            raise RuntimeError("engine is closed")

    def __enter__(self) -> "FlyGymLocomotionEngine":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

