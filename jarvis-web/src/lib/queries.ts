// Typed data hooks. Every screen reads through these, so polling intervals, cache keys and
// invalidation rules live in one place.

import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from './api';
import type {
  Agent,
  AssistResponse,
  Approval,
  AuditEntry,
  AuditVerification,
  Device,
  DeviceCreated,
  Job,
  Memory,
  Runtime,
  SessionInfo,
  SystemStatus,
  Turn,
  VoiceProfile,
  VoiceUpdate,
} from './types';

export const keys = {
  status: ['status'] as const,
  runtime: ['runtime'] as const,
  agents: ['agents'] as const,
  voices: ['voices'] as const,
  activity: (id: string) => ['activity', id] as const,
  approvals: ['approvals'] as const,
  memories: (q: string) => ['memories', q] as const,
  audit: ['audit'] as const,
  auditVerify: ['audit', 'verify'] as const,
  jobs: ['jobs'] as const,
  devices: ['devices'] as const,
  sessions: ['sessions'] as const,
};

// ---------------------------------------------------------------- reads
export const useStatus = () =>
  useQuery({ queryKey: keys.status, queryFn: ({ signal }) => api<SystemStatus>('/api/system/status', { signal }), refetchInterval: 10_000 });

export const useRuntime = () =>
  useQuery({ queryKey: keys.runtime, queryFn: ({ signal }) => api<Runtime>('/api/runtime', { signal }), refetchInterval: 10_000 });

export const useAgents = () =>
  useQuery({ queryKey: keys.agents, queryFn: ({ signal }) => api<Agent[]>('/api/agents', { signal }), refetchInterval: 20_000 });

export const useVoices = () =>
  useQuery({ queryKey: keys.voices, queryFn: ({ signal }) => api<VoiceProfile[]>('/api/voices', { signal }), staleTime: 60_000 });

export const useActivity = (agentId: string) =>
  useQuery({
    queryKey: keys.activity(agentId),
    queryFn: ({ signal }) => api<Turn[]>(`/api/agents/${encodeURIComponent(agentId)}/activity?limit=20`, { signal }),
  });

export const useApprovals = () =>
  useQuery({ queryKey: keys.approvals, queryFn: ({ signal }) => api<Approval[]>('/api/approvals?limit=100', { signal }), refetchInterval: 8_000 });

export const useMemories = (q: string) =>
  useQuery({
    queryKey: keys.memories(q),
    queryFn: ({ signal }) => api<Memory[]>(`/api/memory?limit=200${q ? `&q=${encodeURIComponent(q)}` : ''}`, { signal }),
    placeholderData: keepPreviousData,
  });

export const useAudit = () =>
  useQuery({ queryKey: keys.audit, queryFn: ({ signal }) => api<AuditEntry[]>('/api/audit?limit=40', { signal }), refetchInterval: 10_000 });

export const useAuditVerification = () =>
  useQuery({ queryKey: keys.auditVerify, queryFn: ({ signal }) => api<AuditVerification>('/api/audit/verify', { signal }), refetchInterval: 30_000 });

export const useJobs = () => useQuery({ queryKey: keys.jobs, queryFn: ({ signal }) => api<Job[]>('/api/jobs', { signal }) });
export const useDevices = () => useQuery({ queryKey: keys.devices, queryFn: ({ signal }) => api<Device[]>('/api/devices', { signal }) });
export const useSessions = () => useQuery({ queryKey: keys.sessions, queryFn: ({ signal }) => api<SessionInfo[]>('/api/auth/sessions', { signal }) });

// ---------------------------------------------------------------- writes
export function useAssist() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (vars: { utterance: string; sessionId?: string }) =>
      api<AssistResponse>('/api/assist', { method: 'POST', body: { session_id: vars.sessionId ?? 'dashboard', utterance: vars.utterance } }),
    onSettled: () => {
      void client.invalidateQueries({ queryKey: keys.status });
      void client.invalidateQueries({ queryKey: keys.approvals });
      void client.invalidateQueries({ queryKey: keys.agents });
    },
  });
}

export function useSetAgentEnabled() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (vars: { id: string; enabled: boolean }) =>
      api(`/api/agents/${encodeURIComponent(vars.id)}/enabled`, { method: 'PUT', body: { enabled: vars.enabled } }),
    onSuccess: () => client.invalidateQueries({ queryKey: keys.agents }),
  });
}

export function useSaveVoice() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (vars: { id: string; patch: VoiceUpdate }) =>
      api<VoiceProfile>(`/api/agents/${encodeURIComponent(vars.id)}/voice`, { method: 'PUT', body: vars.patch }),
    onSuccess: () => Promise.all([client.invalidateQueries({ queryKey: keys.agents }), client.invalidateQueries({ queryKey: keys.voices })]),
  });
}

export function useResetVoice() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api<VoiceProfile>(`/api/agents/${encodeURIComponent(id)}/voice`, { method: 'DELETE' }),
    onSuccess: () => Promise.all([client.invalidateQueries({ queryKey: keys.agents }), client.invalidateQueries({ queryKey: keys.voices })]),
  });
}

export function useDecideApproval() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (vars: { id: string; granted: boolean }) =>
      api(`/api/approvals/${encodeURIComponent(vars.id)}/decision`, { method: 'POST', body: { granted: vars.granted } }),
    onSettled: () => {
      void client.invalidateQueries({ queryKey: keys.approvals });
      void client.invalidateQueries({ queryKey: keys.status });
    },
  });
}

export function useAddMemory() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (text: string) => api<Memory>('/api/memory', { method: 'POST', body: { text } }),
    onSuccess: () => client.invalidateQueries({ queryKey: ['memories'] }),
  });
}

export function useDeleteMemory() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api(`/api/memory/${encodeURIComponent(id)}`, { method: 'DELETE' }),
    onSuccess: () => client.invalidateQueries({ queryKey: ['memories'] }),
  });
}

export function useWipeMemories() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api<{ removed: number }>('/api/memory?confirm=DELETE-ALL', { method: 'DELETE' }),
    onSuccess: () => client.invalidateQueries({ queryKey: ['memories'] }),
  });
}

export function useRunJob() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api(`/api/jobs/${encodeURIComponent(id)}/run`, { method: 'POST' }),
    onSettled: () => client.invalidateQueries({ queryKey: keys.jobs }),
  });
}

export function useRegisterDevice() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => api<DeviceCreated>('/api/devices', { method: 'POST', body: { name } }),
    onSuccess: () => client.invalidateQueries({ queryKey: keys.devices }),
  });
}

export function useRevokeDevice() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api(`/api/devices/${encodeURIComponent(id)}`, { method: 'DELETE' }),
    onSuccess: () => client.invalidateQueries({ queryKey: keys.devices }),
  });
}

export const changePassword = (current: string, next: string) =>
  api('/api/auth/change-password', { method: 'POST', body: { current_password: current, new_password: next } });

export const logoutEverywhere = () => api('/api/auth/logout-all', { method: 'POST' });
