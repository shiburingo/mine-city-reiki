export type AuthUser = {
  id: number;
  username: string;
  roles: string[];
  apps: string[];
};

export type RevisionItem = {
  id: number;
  title: string;
  lawType: string;
  lawNumber: string;
  promulgatedAt: string | null;
  updatedAt: string | null;
  sourceUrl: string;
};

export type SourceScope = 'all' | 'mine-city' | 'egov' | 'local-public-service';
export type LawSource = Exclude<SourceScope, 'all'>;

export type SyncStatus = {
  enabled: boolean;
  dayOfMonth: number;
  hour: number;
  minute: number;
  timezone: string;
  sourceScope: SourceScope;
  lastStartedAt: string | null;
  lastFinishedAt: string | null;
  lastSuccessAt: string | null;
  lastError: string | null;
  documentCount: number;
  articleCount: number;
  runCount: number;
  mineCityDocumentCount: number;
  mineCityArticleCount: number;
  egovDocumentCount: number;
  egovArticleCount: number;
  localPublicServiceDocumentCount: number;
  localPublicServiceArticleCount: number;
  mineCityLatestRevisions: RevisionItem[];
  egovLatestRevisions: RevisionItem[];
  localPublicServiceLatestRevisions: RevisionItem[];
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
  source: LawSource;
  title: string;
  lawType: string;
  lawNumber: string;
  sourceUrl: string;
  articleNumber: string | null;
  articleTitle: string | null;
  snippet: string;
  categoryPath: string;
  matchReasons?: string[];
  promulgatedAt?: string | null;
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
  source: LawSource;
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

export type SearchField = {
  q: string;
  op: 'AND' | 'OR';
};

export type DocumentSummary = {
  id: number;
  source: LawSource;
  title: string;
  lawType: string;
  lawNumber: string;
  categoryPath: string;
  browseCategoryKey?: string;
  browseDocumentOrder?: number;
  promulgatedAt: string | null;
};

export type AskArticleResult = {
  articleId: number | null;
  articleNumber: string | null;
  articleTitle: string | null;
  articleText: string;
  score: number;
};

export type AskCandidateGroup = {
  documentId: number;
  source: LawSource;
  title: string;
  lawType: string;
  lawNumber: string;
  sourceUrl: string;
  categoryPath: string;
  topScore: number;
  articles: AskArticleResult[];
};

export type AskResponse = {
  query: string;
  normalizedQuery: string;
  keywords: string[];
  questionType: string;
  questionTypeLabel: string;
  answerLead: string;
  candidateGroups: AskCandidateGroup[];
  candidates: SearchResult[];
};

export type SynonymItem = {
  id: number;
  canonicalTerm: string;
  synonymTerm: string;
  priority: number;
  isActive: boolean;
};

export type DocHistoryItem = {
  id: number;
  contentHash: string;
  title: string;
  lawNumber: string;
  promulgatedAt: string | null;
  updatedAtSource: string;
  changedAt: string | null;
  fullText?: string;
};

export type AnalyticsData = {
  searchCacheHits: number;
  searchCacheEntries: number;
  askCacheHits: number;
  askCacheEntries: number;
  topSearchQueries: { query: string; hits: number }[];
  topAskQueries: { query: string; hits: number }[];
};

export type SearchResponse = {
  items: SearchResult[];
  total: number;
};

export type BrowseCategory = {
  key: string;
  label: string;
  trail: string;
};
