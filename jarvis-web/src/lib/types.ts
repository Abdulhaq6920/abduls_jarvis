// Wire types for the JARVIS backend API. They mirror jarvis_backend/schemas*.py and
// jarvis_shared/*; keep them in sync when the backend models change.

export type AgentId =
  | 'JARVIS' | 'FRIDAY' | 'VISION' | 'NATASHA' | 'WONG'
  | 'TONY' | 'SHURI' | 'QUICKSILVER' | 'NICK_FURY' | 'DR_STRANGE';

export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  username: string;
  role: string;
}

export interface AgentSpec {
  id: AgentId;
  display_name: string;
  role: string;
  personality: string[];
  capabilities: string[];
  tools: string[];
  routing_keywords: string[];
}

export interface VoiceProfile {
  agent_id: AgentId;
  voice_provider: string;
  voice_id: string;
  language: string;
  speed: number;
  pitch: number;
  style: string;
  enabled: boolean;
}

export interface VoiceUpdate {
  speed?: number;
  pitch?: number;
  style?: string;
  voice_id?: string;
  enabled?: boolean;
}

export interface AgentStats {
  turns: number;
  avg_latency_ms: number | null;
  last_active: string | null;
}

export interface Agent {
  spec: AgentSpec;
  voice: VoiceProfile;
  enabled: boolean;
  can_disable: boolean;
  stats: AgentStats;
}

export interface Turn {
  id: string;
  session_id: string;
  agent_id: string;
  utterance: string;
  speech: string;
  latency_ms: number;
  pending_approval_id: string | null;
  created_at: string;
}

export interface StageTrace {
  stage: string;
  detail: string;
  agent_id: AgentId | null;
}

export interface AssistResponse {
  session_id: string;
  responding_agent: AgentId;
  speech: string;
  keep_listening: boolean;
  trace: StageTrace[];
  pending_approval_id: string | null;
  delegation_note: string | null;
}

export type ApprovalStatus = 'PENDING' | 'GRANTED' | 'EXECUTING' | 'EXECUTED' | 'FAILED' | 'DENIED' | 'EXPIRED';
export type Risk = 'SAFE' | 'RESTRICTED' | 'DANGEROUS';

export interface Approval {
  id: string;
  agent_id: string;
  summary: string;
  risk: Risk | string;
  status: ApprovalStatus | string;
  request: Record<string, unknown> | null;
  result: { error?: string; output?: string; ok?: boolean } | null;
  created_at: string;
  resolved_at: string | null;
  resolved_by: string | null;
  executed_at: string | null;
}

export interface Memory {
  id: string;
  kind: string;
  text: string;
  tags: string[];
  agent_id: string | null;
  source: string;
  created_at: string;
}

export interface AuditEntry {
  id: number;
  ts: string;
  event: string;
  agent_id: string | null;
  session_id: string | null;
  data: Record<string, unknown>;
  hash: string;
}

export interface AuditVerification {
  ok: boolean;
  checked: number;
  first_bad_id: number | null;
  reason: string;
}

export interface JobRun {
  id: string;
  job_id: string;
  trigger: string;
  started_at: string;
  finished_at: string | null;
  ok: boolean;
  output: string;
  error: string;
}

export interface Job {
  id: string;
  name: string;
  cron: string;
  description: string;
  last_run: JobRun | null;
}

export interface Device {
  id: string;
  name: string;
  created_at: string;
  created_by: string;
  last_seen_at: string | null;
  revoked_at: string | null;
}

export interface DeviceCreated extends Device {
  secret: string;
}

export interface SessionInfo {
  id: string;
  created_at: string;
  expires_at: string;
  ip: string;
  user_agent: string;
}

export interface RuntimeDisconnected {
  connected: false;
}

export interface RuntimeConnected {
  connected: true;
  conversation_state: string;
  microphone_paused: boolean;
  wake_word: string;
  version: string;
  device: string;
  last_seen_seconds: number;
}

export type Runtime = RuntimeDisconnected | RuntimeConnected;

export interface ConfigFinding {
  level: 'error' | 'warning';
  code: string;
  message: string;
}

export interface RuntimeSnapshot {
  uptime_seconds: number;
  event_bus: string;
  event_loop_lag_ms: number;
  websockets: number;
  process: { rss_mb: number; threads: number };
  database: { dialect: string; pool_checked_out: number | null };
  cache: {
    backend: string;
    hits: number;
    misses: number;
    errors: number;
    last_error: string;
    local_items: number;
    local_item_cap: number;
  };
}

export interface SystemStatus {
  version: string;
  env: string;
  database_ok: boolean;
  cache_ok: boolean;
  scheduler_running: boolean;
  agents: number;
  pending_approvals: number;
  runtime: RuntimeSnapshot;
  config_findings: ConfigFinding[];
}

/** Events pushed over /ws/events. */
export interface LiveEvent {
  type: string;
  [key: string]: unknown;
}
