export type Metadata = {
  protocol_version: number
  body_names: string[]
  joint_names: string[]
  actuator_names: string[]
  leg_names: string[]
}

export type Snapshot = {
  protocol_version: number
  sequence: number
  status: 'ready' | 'running' | 'paused' | 'stopped' | 'closed'
  sim_time_s: number
  body_pos_mm: number[][]
  body_quat_wxyz: number[][]
  joint_angle_rad: number[]
  joint_velocity_rad_s: number[]
  actuator_force: number[]
  contact_found: boolean[]
  contact_force_contact_frame: number[][]
  contact_pos_mm_world: number[][]
}

export type LabMessage =
  | { type: 'metadata'; payload: Metadata }
  | { type: 'snapshot'; payload: Snapshot }
  | { type: 'heartbeat'; status: string; wall_time_ns: number }
  | { type: 'ack'; request_id: string; command: string; status?: string; duplicate?: boolean }
  | { type: 'error' | 'fatal'; error: string; request_id?: string }
  | { type: 'archive'; event: 'recording' | 'sealed' | 'failed' | 'recovered'; run_id?: string; error?: string }

export type ArchiveInfo = {
  enabled: boolean
  recording_run_id: string | null
  runs: number
  stored_bytes: number
  free_bytes?: number
  margin_bytes?: number
  by_state?: Record<string, number>
}

export type RunSummary = {
  id: string
  state: string
  seed: number
  stop_reason: string | null
  created_ns: number
  experiment: string
  metrics: Record<string, number>
}

