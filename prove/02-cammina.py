"""Fa camminare il moscerino simulato (NeuroMechFly, flygym 2.1.0) per 1 secondo
di tempo simulato usando un controllore CPG (tripode alternato) scritto a mano,
perche' questa versione di flygym non include piu' `examples.locomotion` ne'
controllori di camminata pre-fatti (verificato leggendo il pacchetto installato:
niente CPG/HybridTurningController/dati di cammino precalcolati in flygym 2.1.0).

Salva:
  - 02-cammina.mp4  (video, camera "a tre quarti" che segue il corpo, 0.1x)
  - 02-cammina.png  (fotogramma a meta' corsa)
  - 02-cammina.txt  (versione flygym, distanza percorsa, tempo di calcolo, n. step)
"""

import os

# Deve essere impostata PRIMA di importare mujoco/flygym.
os.environ.setdefault("MUJOCO_GL", "glfw")
GL_BACKEND_USED = os.environ["MUJOCO_GL"]

import time
from pathlib import Path

import mujoco as mj
import numpy as np
import imageio.v3 as iio
from importlib.metadata import version as pkg_version

from flygym import Simulation
from flygym.compose.fly import NeuroMechFly, ActuatorType
from flygym.compose.world import FlatGroundWorld
from flygym.compose.pose import KinematicPosePreset
from flygym.anatomy import Skeleton, JointPreset, ActuatedDOFPreset, LEGS
from flygym.utils.math import Rotation3D

OUT_DIR = Path(__file__).parent
VIDEO_PATH = OUT_DIR / "02-cammina.mp4"
FRAME_PATH = OUT_DIR / "02-cammina.png"
TXT_PATH = OUT_DIR / "02-cammina.txt"

SIM_DURATION = 1.0  # secondi simulati
SETTLE_DURATION = 0.1  # secondi iniziali per far posare le zampe a terra
STEP_FREQ_HZ = 4.0  # frequenza del passo durante il cammino

AMP_YAW = np.deg2rad(22)  # coxa yaw: protrazione/retrazione (avanti/indietro)
AMP_LIFT = np.deg2rad(16)  # trocantere pitch: levazione durante lo swing
AMP_TIBIA = np.deg2rad(14)  # tibia pitch: estensione in stance / flessione in swing

# Gruppi del tripode alternato (fase 0 vs fase pi)
GROUP_A = {"lf", "rm", "lh"}
GROUP_B = {"rf", "lm", "rh"}


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
    fly.add_tracking_camera()  # camera "trackcam" a tre quarti, dietro e sopra
    return fly


def leg_phase(leg: str, t: float) -> float:
    offset = 0.0 if leg in GROUP_A else np.pi
    return 2 * np.pi * STEP_FREQ_HZ * t + offset


def compute_position_targets(
    fly: NeuroMechFly, jointdof_order: list, t: float
) -> np.ndarray:
    targets = np.zeros(len(jointdof_order))
    for i, jointdof in enumerate(jointdof_order):
        neutral = fly.jointdof_to_neutralangle[jointdof]
        leg = jointdof.child.pos
        link = jointdof.child.link
        axis = jointdof.axis.value

        if leg not in LEGS:
            targets[i] = neutral
            continue

        phase = leg_phase(leg, t)
        delta = 0.0
        if link == "coxa" and axis == "yaw":
            delta = AMP_YAW * (-np.cos(phase))
        elif link == "trochanterfemur" and axis == "pitch":
            delta = AMP_LIFT * max(0.0, np.sin(phase))
        elif link == "tibia" and axis == "pitch":
            delta = AMP_TIBIA * np.cos(phase)

        targets[i] = neutral + delta
    return targets


def compute_adhesion(t: float) -> np.ndarray:
    return np.array(
        [0.0 if np.sin(leg_phase(leg, t)) > 0.0 else 1.0 for leg in LEGS]
    )


def main():
    fly = build_fly()
    world = FlatGroundWorld()
    world.add_fly(
        fly,
        spawn_position=(0.0, 0.0, 1.0),
        spawn_rotation=Rotation3D("quat", (1.0, 0.0, 0.0, 0.0)),
    )

    sim = Simulation(world)
    dt = sim.mj_model.opt.timestep
    n_steps = int(round(SIM_DURATION / dt))

    camera_name = f"{fly.name}/trackcam"
    sim.set_renderer(
        camera_name,
        camera_res=(480, 640),
        playback_speed=0.1,
        output_fps=30,
    )

    pos_order = fly.get_actuated_jointdofs_order(ActuatorType.POSITION)

    # mj_resetDataKeyframe (chiamato dentro Simulation.__init__) imposta qpos/ctrl
    # ma non ricalcola le grandezze cartesiane derivate (xpos): serve un forward
    # esplicito prima di leggere la posizione iniziale del corpo, altrimenti xpos
    # resta a zero (stato di default di MjData appena creato).
    mj.mj_forward(sim.mj_model, sim.mj_data)

    initial_pos = sim.get_body_positions(fly.name)[
        fly.get_bodysegs_order().index(fly.root_segment)
    ].copy()

    mid_frame = None
    mid_step = n_steps // 2

    compute_start = time.perf_counter()
    for step in range(n_steps):
        t = step * dt
        if t < SETTLE_DURATION:
            pos_targets = compute_position_targets(fly, pos_order, 0.0)
            adhesion = np.ones(len(LEGS))
        else:
            walk_t = t - SETTLE_DURATION
            pos_targets = compute_position_targets(fly, pos_order, walk_t)
            adhesion = compute_adhesion(walk_t)

        sim.set_actuator_inputs(fly.name, ActuatorType.POSITION, pos_targets)
        sim.set_leg_adhesion_states(fly.name, adhesion)

        sim.step()
        sim.render_as_needed()

        if step == mid_step:
            mid_frame = sim.renderer.frames[camera_name][-1].copy()
    compute_elapsed = time.perf_counter() - compute_start

    final_pos = sim.get_body_positions(fly.name)[
        fly.get_bodysegs_order().index(fly.root_segment)
    ].copy()

    sim.renderer.save_video(VIDEO_PATH)

    if mid_frame is None:
        # fallback: nessun render esattamente a meta' passo, prendi il frame
        # bufferizzato piu' vicino a meta' corsa
        frames = sim.renderer.frames[camera_name]
        mid_frame = frames[len(frames) // 2]
    iio.imwrite(FRAME_PATH, mid_frame)

    distance_xy = float(np.linalg.norm(final_pos[:2] - initial_pos[:2]))
    distance_3d = float(np.linalg.norm(final_pos - initial_pos))

    flygym_version = pkg_version("flygym")

    lines = [
        f"flygym version: {flygym_version}",
        f"backend OpenGL (MUJOCO_GL): {GL_BACKEND_USED}",
        f"passi di simulazione: {n_steps} (dt={dt:.1e} s, durata simulata={SIM_DURATION} s)",
        f"tempo di calcolo (solo stepping fisico + render): {compute_elapsed:.3f} s",
        f"posizione iniziale corpo (c_thorax, mm): {initial_pos.tolist()}",
        f"posizione finale corpo (c_thorax, mm): {final_pos.tolist()}",
        f"distanza percorsa (piano xy, mm): {distance_xy:.4f}",
        f"distanza percorsa (3D, mm): {distance_3d:.4f}",
        f"video: {VIDEO_PATH.name}",
        f"fotogramma meta' corsa: {FRAME_PATH.name}",
    ]
    TXT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("\n".join(lines))

    mean_pixel = float(np.asarray(mid_frame).mean())
    print(f"media pixel PNG (controllo non-nero/non-vuoto): {mean_pixel:.2f}")


if __name__ == "__main__":
    main()
