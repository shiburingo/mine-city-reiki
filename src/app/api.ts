import type { AnalyticsData, AskResponse, BrowseCategory, DocHistoryItem, DocumentDetail, DocumentSummary, SearchField, SearchResult, SearchResponse, SynonymItem, SyncRun, SyncStatus } from './types';

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

export async function runReindex(batchSize = 10): Promise<{ ok: boolean; summary: Record<string, any> }> {
  return apiFetch<{ ok: boolean; summary: Record<string, any> }>('/reindex/run', {
    method: 'POST',
    body: JSON.stringify({ batchSize }),
  });
}

export async function searchLaws(params: { fields: SearchField[]; source?: string; limit?: number; offset?: number; lawType?: string; fromDate?: string; toDate?: string }): Promise<SearchResponse> {
  const qs = new URLSearchParams();
  params.fields.forEach((f, i) => {
    if (f.q.trim()) {
      qs.set(`q${i + 1}`, f.q.trim());
      qs.set(`op${i + 1}`, f.op);
    }
  });
  if (params.source && params.source !== 'all') qs.set('source', params.source);
  if (params.limit) qs.set('limit', String(params.limit));
  if (params.offset) qs.set('offset', String(params.offset));
  if (params.lawType) qs.set('lawType', params.lawType);
  if (params.fromDate) qs.set('fromDate', params.fromDate);
  if (params.toDate) qs.set('toDate', params.toDate);
  const data = await apiFetch<{ items: SearchResult[]; total: number }>(`/search?${qs.toString()}`);
  return { items: data.items || [], total: data.total ?? data.items?.length ?? 0 };
}

export async function fetchDocumentList(source?: 'all' | 'mine-city' | 'egov'): Promise<{ items: DocumentSummary[]; browseCategories: BrowseCategory[] }> {
  const qs = new URLSearchParams();
  if (source && source !== 'all') qs.set('source', source);
  const data = await apiFetch<{ items: DocumentSummary[]; browseCategories?: BrowseCategory[] }>(`/documents?${qs.toString()}`);
  return { items: data.items || [], browseCategories: data.browseCategories || [] };
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

export async function fetchDocumentHistory(id: number): Promise<DocHistoryItem[]> {
  const data = await apiFetch<{ items: DocHistoryItem[] }>(`/documents/${id}/history`);
  return data.items || [];
}

export async function fetchSynonyms(): Promise<SynonymItem[]> {
  const data = await apiFetch<{ items: SynonymItem[] }>('/synonyms');
  return data.items || [];
}

export async function createSynonym(canonicalTerm: string, synonymTerm: string, priority?: number): Promise<SynonymItem> {
  return apiFetch<SynonymItem>('/synonyms', {
    method: 'POST',
    body: JSON.stringify({ canonicalTerm, synonymTerm, priority: priority ?? 10 }),
  });
}

export async function deleteSynonym(id: number): Promise<void> {
  await apiFetch<{ ok: boolean }>(`/synonyms/${id}`, { method: 'DELETE' });
}

export async function fetchAnalytics(): Promise<AnalyticsData> {
  return apiFetch<AnalyticsData>('/analytics');
}

export async function clearCache(scope: 'search' | 'ask' | 'all'): Promise<void> {
  await apiFetch<{ ok: boolean }>('/cache/clear', {
    method: 'POST',
    body: JSON.stringify({ scope }),
  });
}

export async function fetchLawTypes(): Promise<string[]> {
  const data = await apiFetch<{ items: string[] }>('/law-types');
  return data.items || [];
}

export async function fetchDocumentHistoryDetail(docId: number, historyId: number): Promise<DocHistoryItem> {
  return apiFetch<DocHistoryItem>(`/documents/${docId}/history/${historyId}`);
}

export function buildDocumentsCsvUrl(source?: string): string {
  const base = `${API_BASE}/documents?format=csv`;
  return source && source !== 'all' ? `${base}&source=${source}` : base;
}

export async function searchLawsForRelated(fields: SearchField[], excludeDocId: number, source?: string): Promise<SearchResult[]> {
  const resp = await searchLaws({ fields, source, limit: 6 });
  return resp.items.filter((r) => r.documentId !== excludeDocId).slice(0, 5);
}
