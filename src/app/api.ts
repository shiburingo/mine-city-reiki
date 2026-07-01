import type { AnalyticsData, AskResponse, BrowseCategory, DocHistoryItem, DocumentDetail, DocumentSummary, MinutesDayDetail, MinutesMeeting, MinutesMeetingDetail, MinutesSearchResult, MinutesSpeaker, MinutesStatus, SearchField, SearchResponse, SearchResult, SourceScope, SynonymItem, SynonymStatsItem, SyncRun, SyncStatus } from './types';

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

export async function runSync(sourceScope: SourceScope = 'all'): Promise<{ ok: boolean; summary: Record<string, any> }> {
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

export async function runDictionaryUpdate(includeWordnet = true, includeDomain = true): Promise<{ ok: boolean; summary: Record<string, any> }> {
  return apiFetch<{ ok: boolean; summary: Record<string, any> }>('/dictionary/update', {
    method: 'POST',
    body: JSON.stringify({ includeWordnet, includeDomain }),
  });
}

export async function runMinutesDictionaryUpdate(batchSize = 1000): Promise<{ ok: boolean; summary: Record<string, any> }> {
  return apiFetch<{ ok: boolean; summary: Record<string, any> }>('/dictionary/minutes/update', {
    method: 'POST',
    body: JSON.stringify({ batchSize }),
  });
}

export async function searchLaws(params: { fields: SearchField[]; source?: string; limit?: number; offset?: number; lawType?: string; fromDate?: string; toDate?: string; fuzzy?: boolean }): Promise<SearchResponse> {
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
  if (params.fuzzy) qs.set('fuzzy', '1');
  const data = await apiFetch<{ items: SearchResult[]; total: number }>(`/search?${qs.toString()}`);
  return { items: data.items || [], total: data.total ?? data.items?.length ?? 0 };
}

export async function fetchDocumentList(source?: SourceScope): Promise<{ items: DocumentSummary[]; browseCategories: BrowseCategory[] }> {
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

export async function fetchSynonyms(): Promise<{ items: SynonymItem[]; stats: SynonymStatsItem[] }> {
  const data = await apiFetch<{ items: SynonymItem[]; stats?: SynonymStatsItem[] }>('/synonyms');
  return { items: data.items || [], stats: data.stats || [] };
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
  const resp = await searchLaws({ fields, source, limit: 6, fuzzy: true });
  return resp.items.filter((r) => r.documentId !== excludeDocId).slice(0, 5);
}

export async function fetchMinutesStatus(): Promise<MinutesStatus> {
  return apiFetch<MinutesStatus>('/minutes/status');
}

export async function runMinutesSync(recentDays = 365): Promise<{ ok: boolean; started: boolean; recentDays: number }> {
  return apiFetch<{ ok: boolean; started: boolean; recentDays: number }>('/minutes/sync', {
    method: 'POST',
    body: JSON.stringify({ recentDays }),
  });
}

export async function searchMinutes(params: {
  q?: string;
  speaker?: string;
  role?: string;
  section?: string;
  meetingId?: number;
  dayId?: number;
  years?: string[];
  matchMode?: 'exact' | 'related';
  op?: 'AND' | 'OR';
  fromDate?: string;
  toDate?: string;
  limit?: number | 'all';
  context?: 'none' | 'wide';
}): Promise<{ items: MinutesSearchResult[]; total: number }> {
  const qs = new URLSearchParams();
  if (params.q) qs.set('q', params.q);
  if (params.speaker) qs.set('speaker', params.speaker);
  if (params.role && params.role !== 'all') qs.set('role', params.role);
  if (params.section && params.section !== 'all') qs.set('section', params.section);
  if (params.meetingId) qs.set('meetingId', String(params.meetingId));
  if (params.dayId) qs.set('dayId', String(params.dayId));
  if (params.years?.length) qs.set('years', params.years.join(','));
  if (params.matchMode) qs.set('matchMode', params.matchMode);
  if (params.op) qs.set('op', params.op);
  if (params.fromDate) qs.set('fromDate', params.fromDate);
  if (params.toDate) qs.set('toDate', params.toDate);
  if (params.limit) qs.set('limit', String(params.limit));
  if (params.context) qs.set('context', params.context);
  const data = await apiFetch<{ items: MinutesSearchResult[]; total: number }>(`/minutes/search?${qs.toString()}`);
  return { items: data.items || [], total: data.total ?? data.items?.length ?? 0 };
}

export async function fetchMinutesSpeakers(params: {
  role?: string;
  section?: string;
  meetingId?: number | null;
  years?: string[];
  fromDate?: string;
  toDate?: string;
} = {}): Promise<MinutesSpeaker[]> {
  const qs = new URLSearchParams();
  if (params.role && params.role !== 'all') qs.set('role', params.role);
  if (params.section && params.section !== 'all') qs.set('section', params.section);
  if (params.meetingId) qs.set('meetingId', String(params.meetingId));
  if (params.years?.length) qs.set('years', params.years.join(','));
  if (params.fromDate) qs.set('fromDate', params.fromDate);
  if (params.toDate) qs.set('toDate', params.toDate);
  const suffix = qs.toString() ? `?${qs.toString()}` : '';
  const data = await apiFetch<{ items: MinutesSpeaker[] }>(`/minutes/speakers${suffix}`);
  return data.items || [];
}

export async function fetchMinutesMeetings(): Promise<MinutesMeeting[]> {
  const data = await apiFetch<{ items: MinutesMeeting[] }>('/minutes/meetings');
  return data.items || [];
}

export async function fetchMinutesMeetingDetail(meetingId: number): Promise<MinutesMeetingDetail> {
  return apiFetch<MinutesMeetingDetail>(`/minutes/meetings/${meetingId}`);
}

export async function fetchMinutesDayDetail(dayId: number): Promise<MinutesDayDetail> {
  return apiFetch<MinutesDayDetail>(`/minutes/days/${dayId}`);
}
