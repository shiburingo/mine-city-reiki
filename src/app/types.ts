export type AuthUser = {
  id: number;
  username: string;
  roles: string[];
  apps: string[];
};

export type SyncStatus = {
  enabled: boolean;
  dayOfMonth: number;
  hour: number;
  minute: number;
  timezone: string;
  sourceScope: 'all' | 'mine-city' | 'egov';
  lastStartedAt: string | null;
  lastFinishedAt: string | null;
  lastSuccessAt: string | null;
  lastError: string | null;
  documentCount: number;
  articleCount: number;
  runCount: number;
};

export type SyncRun = {
  id: number;
  runType: 'manual' | 'scheduled';
  status: 'running' | 'success' | 'failed';
  startedAt: string;
  finishedAt: string | null;
  summary: Record<string, any>;
  errorText: string | null;
};

export type SearchResult = {
  score: number;
  documentId: number;
  articleId: number | null;
  source: 'mine-city' | 'egov';
  title: string;
  lawType: string;
  lawNumber: string;
  sourceUrl: string;
  articleNumber: string | null;
  articleTitle: string | null;
  snippet: string;
  categoryPath: string;
};

export type ArticleItem = {
  id: number;
  articleKey: string;
  articleNumber: string;
  articleTitle: string;
  parentPath: string;
  text: string;
};

export type DocumentDetail = {
  id: number;
  source: 'mine-city' | 'egov';
  externalId: string;
  title: string;
  lawType: string;
  lawNumber: string;
  categoryPath: string;
  sourceUrl: string;
  promulgatedAt: string | null;
  effectiveAt: string | null;
  updatedAtSource: string;
  fullText: string;
  articles: ArticleItem[];
};

export type AskResponse = {
  query: string;
  normalizedQuery: string;
  keywords: string[];
  answerLead: string;
  candidates: SearchResult[];
};
