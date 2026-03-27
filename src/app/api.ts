import type { AskResponse, DocumentDetail, SearchResult, SyncRun, SyncStatus } from './types';

const API_BASE = ((import.meta as any).env?.VITE_REIKI_API_BASE || '/mine-city-reiki-api/api').replace(/\/+$/, '');

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    credentials: 'include',
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(init?.headers || {}),
    },
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const error = typeof (data as any)?.error === 'string' ? (data as any).error : res.statusText;
    throw new Error(error || 'API error');
  }
  return data as T;
}

export async function fetchHealth() {
  return apiFetch<{ ok: boolean; service: string; version: string }>('/health');
}

export async function fetchSyncStatus(): Promise<SyncStatus> {
  return apiFetch<SyncStatus>('/sync/status');
}

export async function fetchSyncRuns(): Promise<SyncRun[]> {
  const data = await apiFetch<{ items: SyncRun[] }>('/sync/runs');
  return data.items || [];
}

export async function updateSyncSettings(payload: Partial<SyncStatus>): Promise<SyncStatus> {
  return apiFetch<SyncStatus>('/sync/settings', {
    method: 'PUT',
    body: JSON.stringify(payload),
  });
}

export async function runSync(sourceScope: 'all' | 'mine-city' | 'egov' = 'all'): Promise<{ ok: boolean; summary: Record<string, any> }> {
  return apiFetch<{ ok: boolean; summary: Record<string, any> }>('/sync/run', {
    method: 'POST',
    body: JSON.stringify({ sourceScope }),
  });
}

export async function searchLaws(params: { q: string; source?: string; limit?: number }): Promise<SearchResult[]> {
  const qs = new URLSearchParams();
  qs.set('q', params.q);
  if (params.source && params.source !== 'all') qs.set('source', params.source);
  if (params.limit) qs.set('limit', String(params.limit));
  const data = await apiFetch<{ items: SearchResult[] }>(`/search?${qs.toString()}`);
  return data.items || [];
}

export async function fetchDocumentDetail(id: number): Promise<DocumentDetail> {
  return apiFetch<DocumentDetail>(`/documents/${id}`);
}

export async function askQuestion(query: string): Promise<AskResponse> {
  return apiFetch<AskResponse>('/ask', {
    method: 'POST',
    body: JSON.stringify({ query }),
  });
}
