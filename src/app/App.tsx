import { startTransition, useDeferredValue, useEffect, useMemo, useRef, useState } from 'react';
import { BarChart2, Bookmark, BookMarked, BookOpen, ChevronLeft, ChevronRight, Clock, Database, Download, FileSearch, Printer, RefreshCw, Search, Settings2, Star, Trash2, X } from 'lucide-react';
import { PortalHeader } from '@mine-troutfarm/ui';
import {
  askQuestion,
  buildDocumentsCsvUrl,
  clearCache,
  createSynonym,
  deleteSynonym,
  fetchAnalytics,
  fetchDocumentDetail,
  fetchDocumentHistory,
  fetchDocumentHistoryDetail,
  fetchDocumentList,
  fetchLawTypes,
  fetchMinutesDayDetail,
  fetchMinutesMeetingDetail,
  fetchMinutesMeetings,
  fetchMinutesSpeakers,
  fetchMinutesStatus,
  fetchSyncRuns,
  fetchSyncStatus,
  fetchSynonyms,
  runMinutesSync,
  runDictionaryUpdate,
  runMinutesDictionaryUpdate,
  runReindex,
  runSync,
  searchLaws,
  searchLawsForRelated,
  searchMinutes,
  updateSyncSettings,
} from './api';
import { fetchAuthConfig, fetchMe, login, logout } from './authApi';
import type { AnalyticsData, AskCandidateGroup, AskResponse, AuthUser, BrowseCategory, DocHistoryItem, DocumentDetail, DocumentSummary, MinutesDayDetail, MinutesExchangeItem, MinutesMeeting, MinutesMeetingDetail, MinutesSearchResult, MinutesSpeaker, MinutesStatus, MinutesTable, RevisionItem, SearchField, SearchResult, SourceScope, SyncRun, SyncStatus, SynonymItem, SynonymStatsItem } from './types';
import { ArticleContent, type ArticleLinkMap, type SourceAnchorLinkMap, type SourceDocumentLinkMap } from './ArticleContent';

const TABS = [
  { id: 'dashboard', label: 'ダッシュボード', icon: Database },
  { id: 'browse', label: '閲覧', icon: BookOpen },
  { id: 'search', label: '例規検索', icon: Search },
  { id: 'minutes', label: '会議録検索システム', icon: FileSearch },
  { id: 'ask', label: '質問', icon: FileSearch },
  { id: 'bookmarks', label: 'ブックマーク', icon: Bookmark },
  { id: 'settings', label: '設定', icon: Settings2 },
] as const;

const SEARCH_HISTORY_KEY = 'reiki_search_history';
const MINUTES_SEARCH_HISTORY_KEY = 'minutes_search_history_v1';
const BOOKMARKS_KEY = 'reiki_bookmarks';
type BrowseSource = 'mine-city' | 'egov' | 'local-public-service';
type BrowseTreeNode = {
  key: string;
  label: string;
  orderKey?: string;
  children: BrowseTreeNode[];
  docs: DocumentSummary[];
};
type ArticleGroupNode = {
  key: string;
  label: string;
  children: ArticleGroupNode[];
  articles: DocumentDetail['articles'];
};
type SearchResultGroup = {
  documentId: number;
  source: SearchResult['source'];
  title: string;
  lawType: string;
  lawNumber: string;
  sourceUrl: string;
  categoryPath: string;
  maxScore: number;
  hits: SearchResult[];
};
const ORDER_COLLATOR = new Intl.Collator('ja-JP', { numeric: true, sensitivity: 'base' });

function loadSearchHistory(): string[] {
  try {
    return JSON.parse(localStorage.getItem(SEARCH_HISTORY_KEY) || '[]');
  } catch {
    return [];
  }
}

function saveSearchHistory(items: string[]): void {
  localStorage.setItem(SEARCH_HISTORY_KEY, JSON.stringify(items.slice(0, 10)));
}

type MinutesSearchHistoryItem = {
  id: string;
  label: string;
  query: string;
  speaker: string;
  role: string;
  section: string;
  meetingId: number | null;
  fromDate: string;
  toDate: string;
  matchMode: 'exact' | 'related';
  op: 'AND' | 'OR';
  includeReplies: boolean;
  createdAt: string;
};
type MinutesPage = 'home' | 'browse' | 'keyword' | 'speaker' | 'collection' | 'collectionResults' | 'history' | 'results' | 'detail' | 'meetingDetail';
type MinutesSearchMethodPage = Extract<MinutesPage, 'browse' | 'keyword' | 'speaker' | 'collection'>;
type MinutesBrowseSectionFilter = 'all' | string;
type MinutesSearchLimit = 30 | 60 | 100 | 200 | 'all';
const DEFAULT_MINUTES_MATCH_MODE: MinutesSearchHistoryItem['matchMode'] = 'exact';
const DEFAULT_MINUTES_OP: MinutesSearchHistoryItem['op'] = 'AND';
const DEFAULT_MINUTES_INCLUDE_REPLIES = true;
const DEFAULT_MINUTES_INCLUDE_CHAIR = false;
const DEFAULT_MINUTES_SORT_ORDER: 'new' | 'old' = 'new';
const DEFAULT_MINUTES_SEARCH_LIMIT: MinutesSearchLimit = 30;
const MINUTES_INITIAL_RENDER_LIMIT = 200;
const MINUTES_RENDER_BATCH_SIZE = 200;
const MINUTES_SEARCH_LIMIT_OPTIONS: { value: MinutesSearchLimit; label: string }[] = [
  { value: 30, label: '30件' },
  { value: 60, label: '60件' },
  { value: 100, label: '100件' },
  { value: 200, label: '200件' },
  { value: 'all', label: '無制限' },
];

function loadMinutesSearchHistory(): MinutesSearchHistoryItem[] {
  try {
    const raw = JSON.parse(localStorage.getItem(MINUTES_SEARCH_HISTORY_KEY) || '[]');
    return Array.isArray(raw) ? raw.slice(0, 20) : [];
  } catch {
    return [];
  }
}

function saveMinutesSearchHistory(items: MinutesSearchHistoryItem[]): void {
  localStorage.setItem(MINUTES_SEARCH_HISTORY_KEY, JSON.stringify(items.slice(0, 20)));
}

function loadBookmarks(): number[] {
  try {
    return JSON.parse(localStorage.getItem(BOOKMARKS_KEY) || '[]');
  } catch {
    return [];
  }
}

function saveBookmarks(ids: number[]): void {
  localStorage.setItem(BOOKMARKS_KEY, JSON.stringify(ids));
}

function asNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function runProgress(run: SyncRun | null): { current: number; total: number; percent: number; label: string } | null {
  if (!run || run.status !== 'running') return null;
  const current = asNumber(run.summary?.progressCurrent);
  const total = asNumber(run.summary?.progressTotal);
  if (current == null || total == null || total <= 0) return null;
  const percent = Math.max(0, Math.min(100, (current / total) * 100));
  return {
    current,
    total,
    percent,
    label: typeof run.summary?.progressLabel === 'string' ? run.summary.progressLabel : '',
  };
}

function ProgressMeter({ title, run }: { title: string; run: SyncRun | null }) {
  const progress = runProgress(run);
  if (!progress) return null;
  return (
    <div className="mt-4 rounded-2xl border bg-background px-4 py-3">
      <div className="flex items-center justify-between gap-3 text-sm">
        <span className="font-medium">{title}</span>
        <span className="text-muted-foreground">{progress.current}/{progress.total}</span>
      </div>
      <div className="mt-2 h-2 overflow-hidden rounded-full bg-muted">
        <div className="h-full rounded-full bg-primary transition-all" style={{ width: `${progress.percent}%` }} />
      </div>
      {progress.label ? <p className="mt-2 text-xs text-muted-foreground">{progress.label}</p> : null}
    </div>
  );
}

function syncRunLabel(run: SyncRun): string {
  const operation = String(run.summary?.operation || '');
  if (operation === 'reindex') return '再索引';
  if (operation === 'dictionary-update') return '関連語辞書更新';
  if (operation === 'minutes-dictionary-update') return '会議録辞書作成';
  if (operation === 'minutes-sync') return '会議録同期';
  return run.runType === 'scheduled' ? '定期同期' : '手動同期';
}

type TabId = (typeof TABS)[number]['id'];

type SyncForm = {
  enabled: boolean;
  dayOfMonth: number;
  hour: number;
  minute: number;
  sourceScope: SourceScope;
};

const EMPTY_SYNC_STATUS: SyncStatus = {
  enabled: false,
  dayOfMonth: 1,
  hour: 3,
  minute: 0,
  timezone: 'Asia/Tokyo',
  sourceScope: 'all',
  lastStartedAt: null,
  lastFinishedAt: null,
  lastSuccessAt: null,
  lastError: null,
  documentCount: 0,
  articleCount: 0,
  runCount: 0,
  mineCityDocumentCount: 0,
  mineCityArticleCount: 0,
  egovDocumentCount: 0,
  egovArticleCount: 0,
  localPublicServiceDocumentCount: 0,
  localPublicServiceArticleCount: 0,
  mineCityLatestRevisions: [],
  egovLatestRevisions: [],
  localPublicServiceLatestRevisions: [],
};

const EMPTY_MINUTES_STATUS: MinutesStatus = {
  dayCount: 0,
  utteranceCount: 0,
  tableCount: 0,
  speakerCount: 0,
  latestRun: null,
  latestDays: [],
};

function formatDateTime(value: string | null | undefined): string {
  if (!value) return '未実行';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat('ja-JP', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date);
}

function parseDate(value: string | null | undefined): Date | null {
  if (!value) return null;
  const date = new Date(`${value}T00:00:00`);
  return Number.isNaN(date.getTime()) ? null : date;
}

function calendarYearFromDate(value: string | null | undefined): number | null {
  const date = parseDate(value);
  return date ? date.getFullYear() : null;
}

function calendarYearLabel(year: number): string {
  if (year >= 2019) return `令和${toFullWidthNumber(year - 2018)}年`;
  if (year >= 1989) return `平成${toFullWidthNumber(year - 1988)}年`;
  return `${toFullWidthNumber(year)}年`;
}

function calendarYearLabelFromDate(value: string | null | undefined): string {
  const year = calendarYearFromDate(value);
  return year ? calendarYearLabel(year) : '年不明';
}

function toFullWidthNumber(value: number | string): string {
  return String(value).replace(/[0-9]/g, (char) => String.fromCharCode(char.charCodeAt(0) + 0xfee0));
}

function formatJapaneseEraYearMonth(value: string | null | undefined): string {
  const date = parseDate(value);
  if (!date) return '';
  const year = date.getFullYear();
  const month = date.getMonth() + 1;
  if (year >= 2019) return `令和${toFullWidthNumber(year - 2018)}年${toFullWidthNumber(month)}月`;
  if (year >= 1989) return `平成${toFullWidthNumber(year - 1988)}年${toFullWidthNumber(month)}月`;
  return `${toFullWidthNumber(year)}年${toFullWidthNumber(month)}月`;
}

function formatJapaneseEraYear(value: string | null | undefined): string {
  const date = parseDate(value);
  if (!date) return '';
  const year = date.getFullYear();
  if (year >= 2019) return `令和${toFullWidthNumber(year - 2018)}年`;
  if (year >= 1989) return `平成${toFullWidthNumber(year - 1988)}年`;
  return `${toFullWidthNumber(year)}年`;
}

function stripMeetingDatePrefix(value: string): string {
  return value
    .replace(/^\s*(令和|平成|昭和)[0-9０-９元]+年[0-9０-９]+月[0-9０-９]+日\s*/u, '')
    .replace(/^\s*[0-9０-９]{4}年[0-9０-９]+月[0-9０-９]+日\s*/u, '')
    .trim();
}

function formatMinutesMeetingBrowseTitle(meeting: MinutesMeeting): string {
  const title = stripMeetingDatePrefix(meeting.meetingName || meeting.title || '会議録');
  if ((meeting.section || '').includes('委員')) {
    const yearLabel = formatJapaneseEraYear(meeting.fromDate || meeting.toDate);
    return [yearLabel, title].filter(Boolean).join('　');
  }
  const dateLabel = formatJapaneseEraYearMonth(meeting.fromDate || meeting.toDate);
  return [dateLabel, title].filter(Boolean).join('　');
}

function formatMinutesTableCaption(table: MinutesTable): string {
  return (table.caption || '表')
    .replace(/\s+minutes-\d+-p\d+-t\d+$/u, '')
    .trim() || '表';
}

function minutesSectionOrder(section: string): number {
  if (section === '本会議') return 0;
  if (section === '常任委員会') return 1;
  if (section === '特別委員会') return 2;
  return 9;
}

function sourceLabel(source: string): string {
  if (source === 'mine-city') return '美祢市例規';
  if (source === 'egov') return '地方自治法';
  if (source === 'local-public-service') return '地方公務員法';
  return '全ソース';
}

function minutesRoleLabel(role: string): string {
  if (role.startsWith('title:')) return role.slice('title:'.length);
  if (role === 'questioner') return '質問者';
  if (role === 'answerer') return '答弁者';
  if (role === 'chair') return '議事進行';
  if (role === 'secretariat') return '事務局';
  if (role === 'report') return '報告';
  if (role === 'other') return 'その他';
  return '未分類';
}

function minutesRoleClass(role: string): string {
  if (role.startsWith('title:')) return 'border-sky-200 bg-sky-50 text-sky-800';
  if (role === 'questioner') return 'border-emerald-200 bg-emerald-50 text-emerald-800';
  if (role === 'answerer') return 'border-sky-200 bg-sky-50 text-sky-800';
  if (role === 'chair') return 'border-amber-200 bg-amber-50 text-amber-800';
  if (role === 'secretariat') return 'border-slate-200 bg-slate-50 text-slate-700';
  if (role === 'report') return 'border-lime-200 bg-lime-50 text-lime-800';
  return 'border-border bg-muted text-muted-foreground';
}

const MINUTES_EXECUTIVE_TITLE_FALLBACKS = [
  '市長',
  '副市長',
  '教育長',
  '病院事業管理者',
  '代表監査委員',
  '会計管理者',
  '部長',
  '次長',
  '課長',
  '局長',
  '消防長',
  '事務局長',
  '所長',
  '室長',
  '支所長',
  'センター長',
  '参事',
  '主幹',
];

const MINUTES_EXECUTIVE_TITLE_ORDER = [
  '市長',
  '副市長',
  '教育長',
  '病院事業管理者',
  '代表監査委員',
  'デジタル推進部長',
  '総務企画部長',
  '市民福祉部長',
  '建設農林部長',
  '観光商工部長',
  '総務企画部理事',
  '地方創生監',
  '会計管理者',
  '教育委員会事務局長',
  '上下水道局長',
  '病院事業局管理部長',
  '消防長',
  '総務企画部次長',
  '建設農林部次長',
  'デジタル推進部次長',
  '市民福祉部次長',
  '観光商工部次長',
  '総務企画部総務課長',
];

const MINUTES_EXECUTIVE_TITLE_ORDER_MAP = new Map(
  MINUTES_EXECUTIVE_TITLE_ORDER.map((title, index) => [title, index * 10]),
);

const MINUTES_EXECUTIVE_ORGANIZATION_ORDER = [
  'デジタル推進部',
  '総務企画部',
  '市民福祉部',
  '建設農林部',
  '観光商工部',
  '教育委員会',
  '上下水道局',
  '病院事業局',
  '消防',
  '農業委員会',
];

function minutesExecutiveOrganizationRank(title: string): number {
  const index = MINUTES_EXECUTIVE_ORGANIZATION_ORDER.findIndex((name) => title.includes(name));
  return index >= 0 ? index : MINUTES_EXECUTIVE_ORGANIZATION_ORDER.length;
}

function minutesExecutiveTitleRank(title: string): number {
  const normalized = normalizeOrderText(title).replace(/\s+/g, '');
  const exactRank = MINUTES_EXECUTIVE_TITLE_ORDER_MAP.get(normalized);
  if (exactRank != null) return exactRank;
  const organizationRank = minutesExecutiveOrganizationRank(normalized);
  if (normalized.endsWith('部長')) return 500 + organizationRank;
  if (normalized.endsWith('理事')) return 540 + organizationRank;
  if (normalized.endsWith('監')) return 550 + organizationRank;
  if (normalized.endsWith('会計管理者')) return 560 + organizationRank;
  if (normalized.endsWith('事務局長')) return 570 + organizationRank;
  if (normalized.endsWith('局長')) return 580 + organizationRank;
  if (normalized.endsWith('消防長')) return 590 + organizationRank;
  if (normalized.endsWith('次長')) return 650 + organizationRank;
  if (normalized.endsWith('課長')) return 720 + organizationRank;
  if (normalized.endsWith('室長')) return 760 + organizationRank;
  if (normalized.endsWith('所長')) return 800 + organizationRank;
  if (normalized.endsWith('センター長')) return 820 + organizationRank;
  if (normalized.endsWith('支所長')) return 840 + organizationRank;
  if (normalized.endsWith('参事')) return 860 + organizationRank;
  if (normalized.endsWith('主幹')) return 880 + organizationRank;
  return 999;
}

function isMinutesCouncilTitle(title: string): boolean {
  const normalized = normalizeOrderText(title).replace(/\s+/g, '');
  return /^(議長|副議長|委員長|副委員長|議員|委員|[0-9０-９]+番)$/.test(normalized);
}

function minutesSpeakerCandidateRank(roles: Set<string>, titles: Set<string>): number {
  const normalizedTitles = [...titles].map((title) => normalizeOrderText(title).replace(/\s+/g, '')).filter(Boolean);
  if (roles.has('questioner') || roles.has('chair') || normalizedTitles.some(isMinutesCouncilTitle)) return 0;
  if (normalizedTitles.includes('市長')) return 10;
  if (normalizedTitles.includes('副市長')) return 20;
  if (roles.has('answerer')) return 30;
  if (roles.has('secretariat')) return 40;
  if (roles.has('report')) return 50;
  return 90;
}

function groupSearchResults(items: SearchResult[]): SearchResultGroup[] {
  const groups = new Map<number, SearchResultGroup>();
  for (const item of items) {
    const existing = groups.get(item.documentId);
    if (existing) {
      existing.hits.push(item);
      existing.maxScore = Math.max(existing.maxScore, item.score);
      continue;
    }
    groups.set(item.documentId, {
      documentId: item.documentId,
      source: item.source,
      title: item.title,
      lawType: item.lawType,
      lawNumber: item.lawNumber,
      sourceUrl: item.sourceUrl,
      categoryPath: item.categoryPath,
      maxScore: item.score,
      hits: [item],
    });
  }
  return [...groups.values()];
}

function normalizeOrderText(value: string | null | undefined): string {
  return (value || '').normalize('NFKC').replace(/\s+/g, ' ').trim();
}

function compareOrderText(a: string | null | undefined, b: string | null | undefined): number {
  return ORDER_COLLATOR.compare(normalizeOrderText(a), normalizeOrderText(b));
}

function firstContentLine(value: string | null | undefined): string {
  return normalizeOrderText(value)
    .replace(/^…+/, '')
    .replace(/^\.\.\./, '')
    .trim();
}

function previewText(value: string | null | undefined, maxLength = 110): string {
  const text = firstContentLine(value);
  return text.length > maxLength ? `${text.slice(0, maxLength)}...` : text;
}

function compareDocumentSummary(a: DocumentSummary, b: DocumentSummary): number {
  const byLawNumber = compareOrderText(a.lawNumber, b.lawNumber);
  if (byLawNumber !== 0) return byLawNumber;
  const byTitle = compareOrderText(a.title, b.title);
  if (byTitle !== 0) return byTitle;
  return a.id - b.id;
}

function compareCategoryKey(a: string | null | undefined, b: string | null | undefined): number {
  const left = (a || '').split('.').filter(Boolean);
  const right = (b || '').split('.').filter(Boolean);
  const max = Math.max(left.length, right.length);
  for (let index = 0; index < max; index += 1) {
    const leftPart = left[index];
    const rightPart = right[index];
    if (leftPart == null) return -1;
    if (rightPart == null) return 1;
    const leftNumber = Number(leftPart);
    const rightNumber = Number(rightPart);
    const leftIsNumber = Number.isFinite(leftNumber);
    const rightIsNumber = Number.isFinite(rightNumber);
    if (leftIsNumber && rightIsNumber && leftNumber !== rightNumber) {
      return leftNumber - rightNumber;
    }
    const byText = compareOrderText(leftPart, rightPart);
    if (byText !== 0) return byText;
  }
  return 0;
}

function compareMineCityDocument(a: DocumentSummary, b: DocumentSummary): number {
  const byCategory = compareCategoryKey(a.browseCategoryKey, b.browseCategoryKey);
  if (byCategory !== 0) return byCategory;
  const byOrder = (a.browseDocumentOrder || 0) - (b.browseDocumentOrder || 0);
  if (byOrder !== 0) return byOrder;
  return compareDocumentSummary(a, b);
}

function buildBrowseTree(source: BrowseSource, docs: DocumentSummary[], categories: BrowseCategory[] = []): BrowseTreeNode[] {
  if (source !== 'mine-city') {
    const rootLabel = source === 'egov' ? '地方自治法' : '地方公務員法';
    return [
      {
        key: `${source}-root`,
        label: rootLabel,
        orderKey: '0',
        children: [],
        docs: [...docs].sort(compareDocumentSummary),
      },
    ];
  }

  const root: BrowseTreeNode = { key: 'root', label: 'root', orderKey: '', children: [], docs: [] };
  const childIndex = new Map<string, BrowseTreeNode>();

  const ensurePath = (trail: string, catKey: string): BrowseTreeNode => {
    const parts = trail
      .split(/\s*\/\s*/)
      .map((p) => p.trim())
      .filter(Boolean);
    let current = root;
    let currentPath = '';
    for (let depth = 0; depth < parts.length; depth++) {
      const part = parts[depth];
      currentPath = currentPath ? `${currentPath}/${part}` : part;
      const lookupKey = `${current.key}>${part}`;
      let next = childIndex.get(lookupKey);
      if (!next) {
        const keyParts = catKey.split('.').filter(Boolean).slice(0, depth + 1);
        next = { key: currentPath, label: part, orderKey: keyParts.join('.'), children: [], docs: [] };
        current.children.push(next);
        childIndex.set(lookupKey, next);
      }
      current = next;
    }
    return current;
  };

  // カテゴリツリーから空カテゴリも含めてノードを生成
  for (const cat of categories) {
    if (cat.trail) {
      ensurePath(cat.trail, cat.key);
    }
  }

  // ドキュメントをツリーに配置
  for (const doc of docs) {
    const trail = doc.categoryPath || '未分類';
    const node = ensurePath(trail, doc.browseCategoryKey || '');
    node.docs.push(doc);
  }

  const sortTree = (nodes: BrowseTreeNode[]) => {
    nodes.sort((a, b) => {
      const byOrder = compareCategoryKey(a.orderKey, b.orderKey);
      if (byOrder !== 0) return byOrder;
      return compareOrderText(a.label, b.label);
    });
    for (const node of nodes) {
      node.docs.sort(compareMineCityDocument);
      sortTree(node.children);
    }
  };

  sortTree(root.children);
  return root.children;
}

function toneForStatus(sync: SyncStatus): 'ok' | 'error' | 'neutral' {
  if (sync.lastError) return 'error';
  if (sync.lastSuccessAt) return 'ok';
  return 'neutral';
}

function syncBadgeText(sync: SyncStatus): string {
  if (sync.lastError) return `同期失敗 ${formatDateTime(sync.lastFinishedAt)}`;
  if (sync.lastSuccessAt) return `同期済み ${formatDateTime(sync.lastSuccessAt)}`;
  return '未同期';
}

function snippet(text: string): string {
  const compact = (text || '').replace(/\s+/g, ' ').trim();
  if (compact.length <= 180) return compact;
  return `${compact.slice(0, 180)}…`;
}

function cleanSearchSnippet(text: string): string {
  return (text || '')
    .replace(/__REIKI_LINK_START__.*?__REIKI_LINK_TEXT__(.*?)__REIKI_LINK_END__/g, '$1')
    .replace(/[A-Za-z0-9%._~:/?#\[\]@!$&'()*+,;=-]*__REIKI_LINK_END__/g, '')
    .replace(/__REIKI_LINK_START__[A-Za-z0-9%._~:/?#\[\]@!$&'()*+,;=-]*/g, '')
    .replace(/__REIKI_LINK_TEXT__[A-Za-z0-9%._~:/?#\[\]@!$&'()*+,;=-]*/g, '')
    .replace(/__REIKI_LINK_[A-Z_]*/g, '')
    .replace(/(?:START|TART|ART|RT|TEXT|EXT|XT|END|ND|D)__/g, '')
    .replace(/\s+/g, ' ')
    .trim();
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function buildHighlightTerms(query: string): string[] {
  const phrase = query.trim().replace(/\s+/g, ' ');
  const terms = new Set<string>();
  if (phrase) terms.add(phrase);
  for (const part of phrase.split(/\s+/)) {
    const term = part.trim();
    if (term) terms.add(term);
  }
  return [...terms].sort((a, b) => b.length - a.length);
}

function normalizeHighlightTerms(terms: string[]): string[] {
  const seen = new Set<string>();
  const result: string[] = [];
  for (const term of terms) {
    const normalized = term.trim();
    const key = normalized.toLocaleLowerCase();
    if (!normalized || seen.has(key)) continue;
    seen.add(key);
    result.push(normalized);
  }
  return result.sort((a, b) => b.length - a.length);
}

function renderHighlightedText(text: string, terms: string[], relatedTerms: string[] = []): Array<string | JSX.Element> {
  const exactTerms = normalizeHighlightTerms(terms);
  const exactLowerTerms = new Set(exactTerms.map((term) => term.toLocaleLowerCase()));
  const filteredRelatedTerms = normalizeHighlightTerms(relatedTerms).filter((term) => !exactLowerTerms.has(term.toLocaleLowerCase()));
  if ((!exactTerms.length && !filteredRelatedTerms.length) || !text) return [text];
  const allTerms = normalizeHighlightTerms([...exactTerms, ...filteredRelatedTerms]);
  const pattern = new RegExp(`(${allTerms.map(escapeRegExp).join('|')})`, 'gi');
  const relatedLowerTerms = new Set(filteredRelatedTerms.map((term) => term.toLocaleLowerCase()));
  return text.split(pattern).map((part, index) => (
    exactLowerTerms.has(part.toLocaleLowerCase()) ? (
      <mark key={`${index}-${part}`} className="rounded bg-yellow-200/90 px-0.5 text-inherit">
        {part}
      </mark>
    ) : relatedLowerTerms.has(part.toLocaleLowerCase()) ? (
      <mark key={`${index}-${part}`} className="rounded bg-emerald-200/90 px-0.5 text-inherit ring-1 ring-emerald-300/70">
        {part}
      </mark>
    ) : part
  ));
}

function buildArticleGroupTree(articles: DocumentDetail['articles']): ArticleGroupNode[] {
  const roots: ArticleGroupNode[] = [];
  const nodeIndex = new Map<string, ArticleGroupNode>();
  const ensureNode = (segments: string[]): ArticleGroupNode => {
    let path = '';
    let siblings = roots;
    let current: ArticleGroupNode | null = null;
    for (const segment of segments) {
      path = path ? `${path} / ${segment}` : segment;
      let node = nodeIndex.get(path);
      if (!node) {
        node = { key: path, label: segment, children: [], articles: [] };
        nodeIndex.set(path, node);
        siblings.push(node);
      }
      current = node;
      siblings = node.children;
    }
    if (!current) {
      const fallback = '本則';
      let node = nodeIndex.get(fallback);
      if (!node) {
        node = { key: fallback, label: fallback, children: [], articles: [] };
        nodeIndex.set(fallback, node);
        roots.push(node);
      }
      return node;
    }
    return current;
  };
  for (const article of articles) {
    const segments = (article.parentPath || '')
      .split(/\s*\/\s*/)
      .map((part) => part.trim())
      .filter(Boolean);
    const target = ensureNode(segments.length > 0 ? segments : ['本則']);
    target.articles.push(article);
  }
  return roots;
}

function countNodeArticles(node: ArticleGroupNode): number {
  const childCount = node.children.reduce((sum, child) => sum + countNodeArticles(child), 0);
  return node.articles.length + childCount;
}

function headingClass(depth: number): string {
  if (depth <= 0) return 'text-xl';
  if (depth === 1) return 'text-lg';
  if (depth === 2) return 'text-base';
  return 'text-sm';
}

function normalizeArticleRef(value: string): string {
  return value.replace(/\s+/g, '').trim();
}

function articleRefAliases(articleNumber: string, articleTitle: string): string[] {
  const rawValues = [articleNumber, articleTitle].filter(Boolean);
  const aliases = new Set<string>();
  for (const raw of rawValues) {
    const normalized = normalizeArticleRef(raw);
    if (!normalized) continue;
    aliases.add(normalized);
    const withoutParen = normalized.replace(/[（(].*$/, '');
    if (withoutParen) aliases.add(withoutParen);
    const tableMatch = normalized.match(/^(別表第[〇一二三四五六七八九十百千万\d]+)/);
    if (tableMatch) aliases.add(tableMatch[1]);
    const formMatch = normalized.match(/^(様式第[〇一二三四五六七八九十百千万\d]+号?)/);
    if (formMatch) aliases.add(formMatch[1]);
  }
  return [...aliases];
}

function buildArticleLinkMap(articles: DocumentDetail['articles'], anchorPrefix: string): ArticleLinkMap {
  const links: ArticleLinkMap = {};
  for (const article of articles) {
    for (const alias of articleRefAliases(article.articleNumber || '', article.articleTitle || '')) {
      links[alias] = `#${anchorPrefix}-${article.id}`;
    }
  }
  return links;
}

function buildSourceAnchorLinkMap(doc: DocumentDetail, anchorPrefix: string): SourceAnchorLinkMap {
  const links: SourceAnchorLinkMap = {};
  const articleIds = new Set(doc.articles.map((article) => article.id));
  for (const [sourceAnchorId, articleId] of Object.entries(doc.sourceAnchorMap || {})) {
    if (articleIds.has(articleId)) {
      links[sourceAnchorId] = `#${anchorPrefix}-${articleId}`;
    }
  }
  return links;
}

function buildSourceDocumentLinkMap(doc: DocumentDetail): SourceDocumentLinkMap {
  return doc.sourceDocumentMap || {};
}

function articleHeadingTone(depth: number): string {
  if (depth <= 0) return 'border-l-4 border-primary bg-accent/35 px-3 py-2';
  if (depth === 1) return 'border-l-2 border-border px-3 py-1.5';
  return 'px-3 py-1 text-muted-foreground';
}

function scrollElementIntoContainer(
  elementId: string,
  container: HTMLElement | null,
  block: ScrollLogicalPosition = 'start',
  behavior: ScrollBehavior = 'smooth',
  offsetPixels = 24,
): void {
  const target = document.getElementById(elementId);
  if (!target) return;
  if (container?.contains(target)) {
    const targetRect = target.getBoundingClientRect();
    const containerRect = container.getBoundingClientRect();
    const offset = block === 'center' ? (container.clientHeight - targetRect.height) / 2 : offsetPixels;
    const top = targetRect.top - containerRect.top + container.scrollTop - offset;
    const nextTop = Math.max(top, 0);
    if (Math.abs(container.scrollTop - nextTop) > 1) {
      container.scrollTo({ top: nextTop, behavior });
    }
    return;
  }
  target.scrollIntoView({ behavior, block });
}

type DiffLine = { type: 'same' | 'del' | 'add'; text: string };

function computeDiff(oldText: string, newText: string): DiffLine[] {
  const oldLines = (oldText || '').split('\n');
  const newLines = (newText || '').split('\n');
  const m = oldLines.length;
  const n = newLines.length;
  // Limit LCS to avoid O(m*n) blow-up on huge texts
  if (m * n > 200_000) {
    return [
      { type: 'del', text: `（旧版 ${m} 行 — 差分が大きすぎるため省略）` },
      { type: 'add', text: `（新版 ${n} 行）` },
    ];
  }
  const dp: number[][] = Array.from({ length: m + 1 }, () => new Array(n + 1).fill(0));
  for (let i = 1; i <= m; i++) {
    for (let j = 1; j <= n; j++) {
      dp[i][j] = oldLines[i - 1] === newLines[j - 1] ? dp[i - 1][j - 1] + 1 : Math.max(dp[i - 1][j], dp[i][j - 1]);
    }
  }
  const result: DiffLine[] = [];
  let i = m, j = n;
  while (i > 0 || j > 0) {
    if (i > 0 && j > 0 && oldLines[i - 1] === newLines[j - 1]) {
      result.unshift({ type: 'same', text: oldLines[i - 1] });
      i--; j--;
    } else if (j > 0 && (i === 0 || dp[i][j - 1] >= dp[i - 1][j])) {
      result.unshift({ type: 'add', text: newLines[j - 1] });
      j--;
    } else {
      result.unshift({ type: 'del', text: oldLines[i - 1] });
      i--;
    }
  }
  return result;
}

function RevisionPanel({ title, items }: { title: string; items: RevisionItem[] }) {
  return (
    <div className="rounded-3xl border bg-card p-6 shadow-sm">
      <h2 className="text-xl font-semibold">{title}</h2>
      <div className="mt-4 space-y-3">
        {items.length === 0 ? (
          <p className="text-sm text-muted-foreground">データがありません。同期後に表示されます。</p>
        ) : (
          items.map((item) => (
            <div key={item.id} className="rounded-2xl border bg-background p-4 text-sm">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-xs text-muted-foreground">{item.lawType || '例規'}{item.lawNumber ? `　${item.lawNumber}` : ''}</p>
                  <p className="mt-1 truncate font-medium">{item.title}</p>
                </div>
                <a
                  href={item.sourceUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="shrink-0 rounded-lg border px-2 py-1 text-xs hover:bg-accent"
                >
                  原文
                </a>
              </div>
              <dl className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
                {item.promulgatedAt ? <div><dt className="inline">公布 </dt><dd className="inline">{item.promulgatedAt}</dd></div> : null}
                {item.updatedAt ? <div><dt className="inline">DB更新 </dt><dd className="inline">{item.updatedAt.slice(0, 10)}</dd></div> : null}
              </dl>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

function LoginCard({ onLogin }: { onLogin: (username: string, password: string) => Promise<void> }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await onLogin(username, password);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'ログインに失敗しました。');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="min-h-screen bg-background text-foreground">
      <main className="mx-auto flex min-h-screen max-w-md items-center px-4">
        <form className="w-full rounded-3xl border bg-card p-6 shadow-sm" onSubmit={submit}>
          <h1 className="text-2xl font-semibold">美祢市例規</h1>
          <p className="mt-2 text-sm text-muted-foreground">ログインして例規データベースを利用します。</p>
          <div className="mt-6 space-y-4">
            <label className="block space-y-2 text-sm">
              <span className="font-medium">ユーザー名</span>
              <input className="h-11 w-full rounded-xl border bg-input-background px-3" value={username} onChange={(e) => setUsername(e.target.value)} />
            </label>
            <label className="block space-y-2 text-sm">
              <span className="font-medium">パスワード</span>
              <input className="h-11 w-full rounded-xl border bg-input-background px-3" type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
            </label>
          </div>
          {error ? <p className="mt-4 text-sm text-red-600">{error}</p> : null}
          <button className="mt-6 inline-flex h-11 w-full items-center justify-center rounded-xl bg-primary px-4 font-semibold text-primary-foreground disabled:opacity-60" disabled={submitting} type="submit">
            {submitting ? 'ログイン中…' : 'ログイン'}
          </button>
        </form>
      </main>
    </div>
  );
}

function AppShell() {
  const [authEnabled, setAuthEnabled] = useState<boolean | null>(null);
  const [user, setUser] = useState<AuthUser | null>(null);
  const [tab, setTab] = useState<TabId>('dashboard');
  const [syncStatus, setSyncStatus] = useState<SyncStatus>(EMPTY_SYNC_STATUS);
  const [syncRuns, setSyncRuns] = useState<SyncRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [globalError, setGlobalError] = useState<string | null>(null);

  const [searchFields, setSearchFields] = useState<SearchField[]>([
    { q: '', op: 'AND' },
    { q: '', op: 'AND' },
    { q: '', op: 'AND' },
    { q: '', op: 'AND' },
  ]);
  const [searchSource, setSearchSource] = useState<SourceScope>('all');
  const [searching, setSearching] = useState(false);
  const [results, setResults] = useState<SearchResult[]>([]);
  const [searchTotal, setSearchTotal] = useState(0);
  const [searchPage, setSearchPage] = useState(0);
  const [selectedDoc, setSelectedDoc] = useState<DocumentDetail | null>(null);
  const [selectedDocId, setSelectedDocId] = useState<number | null>(null);
  const [pendingSelectedArticleHit, setPendingSelectedArticleHit] = useState<{ documentId: number; articleId: number } | null>(null);
  const [activeSelectedArticleHit, setActiveSelectedArticleHit] = useState<{ documentId: number; articleId: number } | null>(null);

  const [searchLawType, setSearchLawType] = useState('');
  const [searchFromDate, setSearchFromDate] = useState('');
  const [searchToDate, setSearchToDate] = useState('');
  const [searchFuzzy, setSearchFuzzy] = useState(false);
  const [lawTypeOptions, setLawTypeOptions] = useState<string[]>([]);
  const [showAdvancedFilter, setShowAdvancedFilter] = useState(false);

  const [relatedResults, setRelatedResults] = useState<SearchResult[]>([]);
  const [relatedLoading, setRelatedLoading] = useState(false);

  const [bookmarkIds, setBookmarkIds] = useState<number[]>(() => loadBookmarks());
  const [bookmarkDocs, setBookmarkDocs] = useState<DocumentDetail[]>([]);
  const [bookmarksLoading, setBookmarksLoading] = useState(false);

  const [analyticsData, setAnalyticsData] = useState<AnalyticsData | null>(null);
  const [synonymItems, setSynonymItems] = useState<SynonymItem[]>([]);
  const [synonymStats, setSynonymStats] = useState<SynonymStatsItem[]>([]);
  const [synonymLoading, setSynonymLoading] = useState(false);
  const [newSynonymCanonical, setNewSynonymCanonical] = useState('');
  const [newSynonymTerm, setNewSynonymTerm] = useState('');

  const [historyDoc, setHistoryDoc] = useState<{ id: number; title: string; currentFullText?: string } | null>(null);
  const [docHistory, setDocHistory] = useState<DocHistoryItem[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [diffEntry, setDiffEntry] = useState<DocHistoryItem | null>(null);
  const [diffLoading, setDiffLoading] = useState(false);

  const searchHistoryRef = useRef<string[]>(loadSearchHistory());
  const selectedArticleScrollRef = useRef<HTMLDivElement | null>(null);
  const browseArticleScrollRef = useRef<HTMLDivElement | null>(null);
  const minutesReaderScrollRef = useRef<HTMLDivElement | null>(null);
  const minutesReaderScrollFrameRef = useRef<number | null>(null);
  const [selectedReturnScrollTop, setSelectedReturnScrollTop] = useState<number | null>(null);
  const [browseReturnScrollTop, setBrowseReturnScrollTop] = useState<number | null>(null);
  const [searchSuggest, setSearchSuggest] = useState<string[]>([]);
  const [showSuggest, setShowSuggest] = useState(false);

  const [browseSource, setBrowseSource] = useState<BrowseSource>('mine-city');
  const [browseList, setBrowseList] = useState<DocumentSummary[]>([]);
  const [browseCategories, setBrowseCategories] = useState<BrowseCategory[]>([]);
  const [browseLoading, setBrowseLoading] = useState(false);
  const [browseDocId, setBrowseDocId] = useState<number | null>(null);
  const [browseDoc, setBrowseDoc] = useState<DocumentDetail | null>(null);
  const [browseDocLoading, setBrowseDocLoading] = useState(false);
  const [pendingBrowseAnchor, setPendingBrowseAnchor] = useState<string | null>(null);
  const [pendingSelectedAnchor, setPendingSelectedAnchor] = useState<string | null>(null);

  const [question, setQuestion] = useState('');
  const [asking, setAsking] = useState(false);
  const [answer, setAnswer] = useState<AskResponse | null>(null);
  const [questionHistory, setQuestionHistory] = useState<string[]>([]);
  const [expandedArticleKeys, setExpandedArticleKeys] = useState<Set<string>>(new Set());

  const [minutesStatus, setMinutesStatus] = useState<MinutesStatus>(EMPTY_MINUTES_STATUS);
  const [minutesSpeakers, setMinutesSpeakers] = useState<MinutesSpeaker[]>([]);
  const [allMinutesSpeakers, setAllMinutesSpeakers] = useState<MinutesSpeaker[]>([]);
  const [minutesMeetings, setMinutesMeetings] = useState<MinutesMeeting[]>([]);
  const [minutesQuery, setMinutesQuery] = useState('');
  const [minutesSpeaker, setMinutesSpeaker] = useState('');
  const [minutesRole, setMinutesRole] = useState('all');
  const [minutesSection, setMinutesSection] = useState('all');
  const [minutesMeetingId, setMinutesMeetingId] = useState<number | null>(null);
  const [minutesSearchYear, setMinutesSearchYear] = useState('');
  const [minutesFromDate, setMinutesFromDate] = useState('');
  const [minutesToDate, setMinutesToDate] = useState('');
  const [minutesMatchMode, setMinutesMatchMode] = useState<'exact' | 'related'>(DEFAULT_MINUTES_MATCH_MODE);
  const [minutesOp, setMinutesOp] = useState<'AND' | 'OR'>(DEFAULT_MINUTES_OP);
  const [minutesIncludeReplies, setMinutesIncludeReplies] = useState(DEFAULT_MINUTES_INCLUDE_REPLIES);
  const [minutesIncludeChair, setMinutesIncludeChair] = useState(DEFAULT_MINUTES_INCLUDE_CHAIR);
  const [minutesSortOrder, setMinutesSortOrder] = useState<'new' | 'old'>(DEFAULT_MINUTES_SORT_ORDER);
  const [minutesLimit, setMinutesLimit] = useState<MinutesSearchLimit>(DEFAULT_MINUTES_SEARCH_LIMIT);
  const [minutesPage, setMinutesPage] = useState<MinutesPage>('home');
  const [minutesSearchReturnPage, setMinutesSearchReturnPage] = useState<MinutesPage>('home');
  const [minutesBrowseFiscalYear, setMinutesBrowseFiscalYear] = useState('');
  const [minutesBrowseSection, setMinutesBrowseSection] = useState<MinutesBrowseSectionFilter>('all');
  const [minutesResultMode, setMinutesResultMode] = useState<'utterance' | 'meeting' | 'table'>('utterance');
  const [minutesReaderMode, setMinutesReaderMode] = useState<'unit' | 'list' | 'full' | 'toc' | 'materials'>('unit');
  const [minutesExpandedResultIds, setMinutesExpandedResultIds] = useState<Set<number>>(new Set());
  const [minutesHistory, setMinutesHistory] = useState<MinutesSearchHistoryItem[]>(() => loadMinutesSearchHistory());
  const [minutesResults, setMinutesResults] = useState<MinutesSearchResult[]>([]);
  const [minutesTotal, setMinutesTotal] = useState(0);
  const [minutesVisibleResultCount, setMinutesVisibleResultCount] = useState(0);
  const [minutesSearching, setMinutesSearching] = useState(false);
  const [minutesSyncing, setMinutesSyncing] = useState(false);
  const [selectedMinutesResult, setSelectedMinutesResult] = useState<MinutesSearchResult | null>(null);
  const [minutesDayDetail, setMinutesDayDetail] = useState<MinutesDayDetail | null>(null);
  const [minutesDetailLoading, setMinutesDetailLoading] = useState(false);
  const [selectedMinutesMeetingDetail, setSelectedMinutesMeetingDetail] = useState<MinutesMeetingDetail | null>(null);
  const [selectedMinutesMeetingDayId, setSelectedMinutesMeetingDayId] = useState<number | null>(null);
  const [minutesMeetingDetailLoading, setMinutesMeetingDetailLoading] = useState(false);

  const [syncForm, setSyncForm] = useState<SyncForm>({
    enabled: false,
    dayOfMonth: 1,
    hour: 3,
    minute: 0,
    sourceScope: 'all',
  });

  async function loadBrowseList(source: BrowseSource) {
    setBrowseLoading(true);
    setGlobalError(null);
    try {
      const data = await fetchDocumentList(source);
      setBrowseList(data.items);
      setBrowseCategories(data.browseCategories);
      setBrowseDocId(null);
      setBrowseDoc(null);
    } catch (err) {
      setGlobalError(err instanceof Error ? err.message : '一覧取得に失敗しました。');
    } finally {
      setBrowseLoading(false);
    }
  }

  async function bootstrap() {
    setLoading(true);
    setGlobalError(null);
    try {
      const enabled = await fetchAuthConfig();
      setAuthEnabled(enabled);
      if (enabled) {
        const me = await fetchMe();
        setUser(me);
        if (!me) {
          setLoading(false);
          return;
        }
      }
      const [status, runs, minutes] = await Promise.all([
        fetchSyncStatus(),
        fetchSyncRuns(),
        fetchMinutesStatus().catch(() => EMPTY_MINUTES_STATUS),
      ]);
      setSyncStatus(status);
      setSyncRuns(runs);
      setMinutesStatus(minutes);
      setSyncForm({
        enabled: status.enabled,
        dayOfMonth: status.dayOfMonth,
        hour: status.hour,
        minute: status.minute,
        sourceScope: status.sourceScope,
      });
    } catch (err) {
      setGlobalError(err instanceof Error ? err.message : '初期化に失敗しました。');
    } finally {
      setLoading(false);
    }
  }

  // パーマリンク: 起動時にハッシュから状態を復元
  useEffect(() => {
    const hash = window.location.hash.slice(1);
    if (!hash) return;
    const params = new URLSearchParams(hash);
    const t = params.get('tab') as TabId | null;
    if (t && TABS.some((tb) => tb.id === t)) setTab(t);
    const docId = params.get('doc');
    if (docId) setSelectedDocId(Number(docId));
  }, []);

  useEffect(() => {
    void bootstrap();
  }, []);

  // パーマリンク: tab/doc 変更時にハッシュ更新
  useEffect(() => {
    const params = new URLSearchParams();
    params.set('tab', tab);
    if (selectedDocId) params.set('doc', String(selectedDocId));
    window.history.replaceState(null, '', `#${params.toString()}`);
  }, [tab, selectedDocId]);

  useEffect(() => {
    if (tab === 'browse' && browseList.length === 0 && !browseLoading) {
      void loadBrowseList(browseSource);
    }
    if (tab === 'dashboard' && !analyticsData) {
      void loadAnalytics();
    }
    if (tab === 'settings' && synonymItems.length === 0) {
      void loadSynonyms();
    }
    if (tab === 'bookmarks') {
      void loadBookmarkDocs();
    }
    if (tab === 'search' && lawTypeOptions.length === 0) {
      void loadLawTypes();
    }
    if (tab === 'minutes') {
      void loadMinutesStatus();
      if (minutesSpeakers.length === 0) void loadMinutesSpeakers();
      if (allMinutesSpeakers.length === 0) void loadAllMinutesSpeakers();
      if (minutesMeetings.length === 0) void loadMinutesMeetings();
    }
  }, [tab]);

  useEffect(() => {
    if (tab !== 'settings') return;
    const hasRunning = syncRuns.some((run) => run.status === 'running');
    if (!hasRunning) return;
    const timer = window.setInterval(() => {
      void (async () => {
        try {
          const [status, runs] = await Promise.all([fetchSyncStatus(), fetchSyncRuns()]);
          setSyncStatus(status);
          setSyncRuns(runs);
          if (!runs.some((run) => run.status === 'running' && run.summary?.operation === 'minutes-sync')) {
            setMinutesSyncing(false);
          }
        } catch {
          // keep the existing screen state when polling fails
        }
      })();
    }, 3000);
    return () => window.clearInterval(timer);
  }, [tab, syncRuns]);

  useEffect(() => {
    if (tab !== 'minutes') return;
    const hasRunningMinutes = syncRuns.some((run) => run.status === 'running' && run.summary?.operation === 'minutes-sync')
      || minutesStatus.latestRun?.status === 'running';
    if (!hasRunningMinutes) return;
    const timer = window.setInterval(() => {
      void (async () => {
        try {
          const [status, runs] = await Promise.all([fetchMinutesStatus(), fetchSyncRuns()]);
          setMinutesStatus(status);
          setSyncRuns(runs);
          if (status.latestRun?.status !== 'running') setMinutesSyncing(false);
        } catch {
          // keep the existing screen state when polling fails
        }
      })();
    }, 3000);
    return () => window.clearInterval(timer);
  }, [tab, syncRuns, minutesStatus.latestRun?.status]);

  useEffect(() => {
    if (tab !== 'minutes' || (minutesPage !== 'speaker' && minutesPage !== 'collection')) return;
    void loadMinutesSpeakers({
      role: minutesRole,
      section: minutesSection,
      meetingId: minutesMeetingId,
      fromDate: minutesFromDate,
      toDate: minutesToDate,
    });
  }, [tab, minutesPage, minutesRole, minutesSection, minutesMeetingId, minutesFromDate, minutesToDate]);

  useEffect(() => {
    if (selectedDocId == null) return;
    setSelectedReturnScrollTop(null);
    let cancelled = false;
    void (async () => {
      try {
        const detail = await fetchDocumentDetail(selectedDocId);
        if (!cancelled) {
          setSelectedDoc(detail);
          void loadRelatedArticles(detail);
        }
      } catch (err) {
        if (!cancelled) setGlobalError(err instanceof Error ? err.message : '条文取得に失敗しました。');
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [selectedDocId]);

  useEffect(() => {
    if (browseDocId == null) return;
    setBrowseReturnScrollTop(null);
    let cancelled = false;
    setBrowseDocLoading(true);
    void (async () => {
      try {
        const detail = await fetchDocumentDetail(browseDocId);
        if (!cancelled) setBrowseDoc(detail);
      } catch (err) {
        if (!cancelled) setGlobalError(err instanceof Error ? err.message : '条文取得に失敗しました。');
      } finally {
        if (!cancelled) setBrowseDocLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [browseDocId]);

  useEffect(() => {
    if (!browseDoc || !pendingBrowseAnchor) return;
    const articleId = browseDoc.sourceAnchorMap?.[pendingBrowseAnchor];
    if (!articleId) {
      setPendingBrowseAnchor(null);
      return;
    }
    window.setTimeout(() => {
      scrollElementIntoContainer(`barticle-${articleId}`, browseArticleScrollRef.current);
      setPendingBrowseAnchor(null);
    }, 0);
  }, [browseDoc, pendingBrowseAnchor]);

  useEffect(() => {
    if (!selectedDoc || !pendingSelectedAnchor) return;
    const articleId = selectedDoc.sourceAnchorMap?.[pendingSelectedAnchor];
    if (!articleId) {
      setPendingSelectedAnchor(null);
      return;
    }
    window.setTimeout(() => {
      scrollElementIntoContainer(`article-${articleId}`, selectedArticleScrollRef.current);
      setPendingSelectedAnchor(null);
    }, 0);
  }, [selectedDoc, pendingSelectedAnchor]);

  useEffect(() => {
    if (!selectedDoc || !pendingSelectedArticleHit) return;
    if (selectedDoc.id !== pendingSelectedArticleHit.documentId) return;
    const articleId = pendingSelectedArticleHit.articleId;
    window.setTimeout(() => {
      scrollElementIntoContainer(`article-${articleId}`, selectedArticleScrollRef.current, 'center');
      setPendingSelectedArticleHit(null);
    }, 0);
  }, [selectedDoc, pendingSelectedArticleHit]);

  useEffect(() => {
    if (tab !== 'minutes') return;
    if (selectedMinutesResult && minutesDayDetail?.id === selectedMinutesResult.dayId) return;
    void loadMinutesDay(selectedMinutesResult);
  }, [tab, selectedMinutesResult?.dayId, minutesDayDetail?.id]);

  useEffect(() => {
    return () => {
      if (minutesReaderScrollFrameRef.current != null) {
        window.cancelAnimationFrame(minutesReaderScrollFrameRef.current);
      }
    };
  }, []);

  async function handleLogin(username: string, password: string) {
    const loggedIn = await login(username, password);
    if (!loggedIn) throw new Error('ユーザー名またはパスワードが正しくありません。');
    setUser(loggedIn);
    await bootstrap();
  }

  async function handleLogout() {
    await logout();
    setUser(null);
    setSelectedDoc(null);
    setSelectedDocId(null);
    await bootstrap();
  }

  function openBrowseSourceDocument(documentId: number, sourceAnchorId?: string) {
    setBrowseSource('mine-city');
    if (sourceAnchorId) setPendingBrowseAnchor(sourceAnchorId);
    setBrowseDocId(documentId);
    setTab('browse');
  }

  function openSelectedSourceDocument(documentId: number, sourceAnchorId?: string) {
    if (sourceAnchorId) setPendingSelectedAnchor(sourceAnchorId);
    setActiveSelectedArticleHit(null);
    setPendingSelectedArticleHit(null);
    setSelectedDocId(documentId);
    setTab('search');
  }

  function rememberBrowseReturnPosition() {
    const container = browseArticleScrollRef.current;
    if (!container) return;
    setBrowseReturnScrollTop(container.scrollTop);
  }

  function rememberSelectedReturnPosition() {
    const container = selectedArticleScrollRef.current;
    if (!container) return;
    setSelectedReturnScrollTop(container.scrollTop);
  }

  function returnBrowseLinkPosition() {
    if (browseReturnScrollTop == null) return;
    browseArticleScrollRef.current?.scrollTo({ top: browseReturnScrollTop, behavior: 'smooth' });
    setBrowseReturnScrollTop(null);
  }

  function returnSelectedLinkPosition() {
    if (selectedReturnScrollTop == null) return;
    selectedArticleScrollRef.current?.scrollTo({ top: selectedReturnScrollTop, behavior: 'smooth' });
    setSelectedReturnScrollTop(null);
  }

  function openSearchResult(item: SearchResult) {
    setSelectedDocId(item.documentId);
    if (item.articleId != null) {
      const hit = { documentId: item.documentId, articleId: item.articleId };
      setPendingSelectedArticleHit(hit);
      setActiveSelectedArticleHit(hit);
    } else {
      setPendingSelectedArticleHit(null);
      setActiveSelectedArticleHit(null);
    }
    setTab('search');
  }

  async function loadAnalytics() {
    try {
      const data = await fetchAnalytics();
      setAnalyticsData(data);
    } catch {
      // analytics is optional
    }
  }

  async function loadSynonyms() {
    setSynonymLoading(true);
    try {
      const { items, stats } = await fetchSynonyms();
      setSynonymItems(items);
      setSynonymStats(stats);
    } catch (err) {
      setGlobalError(err instanceof Error ? err.message : '同義語取得に失敗しました。');
    } finally {
      setSynonymLoading(false);
    }
  }

  async function handleAddSynonym() {
    if (user?.isGuest) {
      setGlobalError('ゲスト権限では同義語を変更できません。');
      return;
    }
    if (!newSynonymCanonical.trim() || !newSynonymTerm.trim()) return;
    try {
      const item = await createSynonym(newSynonymCanonical.trim(), newSynonymTerm.trim());
      setSynonymItems((prev) => [...prev, item].sort((a, b) => a.canonicalTerm.localeCompare(b.canonicalTerm, 'ja')));
      setNewSynonymCanonical('');
      setNewSynonymTerm('');
    } catch (err) {
      setGlobalError(err instanceof Error ? err.message : '同義語追加に失敗しました。');
    }
  }

  async function triggerDictionaryUpdate() {
    if (user?.isGuest) {
      setGlobalError('ゲスト権限では関連語辞書を更新できません。');
      return;
    }
    setBusy(true);
    setGlobalError(null);
    try {
      await runDictionaryUpdate(true, true);
      const runs = await fetchSyncRuns();
      setSyncRuns(runs);
    } catch (err) {
      setGlobalError(err instanceof Error ? err.message : '関連語辞書更新の起動に失敗しました。');
    } finally {
      setBusy(false);
    }
  }

  async function triggerMinutesDictionaryUpdate() {
    if (user?.isGuest) {
      setGlobalError('ゲスト権限では会議録辞書を作成できません。');
      return;
    }
    setBusy(true);
    setGlobalError(null);
    try {
      await runMinutesDictionaryUpdate(1000);
      const runs = await fetchSyncRuns();
      setSyncRuns(runs);
    } catch (err) {
      setGlobalError(err instanceof Error ? err.message : '会議録辞書作成の起動に失敗しました。');
    } finally {
      setBusy(false);
    }
  }

  async function handleDeleteSynonym(id: number) {
    if (user?.isGuest) {
      setGlobalError('ゲスト権限では同義語を削除できません。');
      return;
    }
    try {
      await deleteSynonym(id);
      setSynonymItems((prev) => prev.filter((s) => s.id !== id));
    } catch (err) {
      setGlobalError(err instanceof Error ? err.message : '同義語削除に失敗しました。');
    }
  }

  async function loadBookmarkDocs() {
    const ids = loadBookmarks();
    setBookmarkIds(ids);
    if (ids.length === 0) { setBookmarkDocs([]); return; }
    setBookmarksLoading(true);
    try {
      const docs = await Promise.all(ids.map((id) => fetchDocumentDetail(id)));
      setBookmarkDocs(docs);
    } catch (err) {
      setGlobalError(err instanceof Error ? err.message : 'ブックマーク取得に失敗しました。');
    } finally {
      setBookmarksLoading(false);
    }
  }

  function toggleBookmark(docId: number) {
    setBookmarkIds((prev) => {
      const next = prev.includes(docId) ? prev.filter((id) => id !== docId) : [...prev, docId];
      saveBookmarks(next);
      return next;
    });
  }

  async function openHistory(docId: number, title: string, currentFullText?: string) {
    setHistoryDoc({ id: docId, title, currentFullText });
    setHistoryLoading(true);
    setDiffEntry(null);
    setDocHistory([]);
    try {
      const items = await fetchDocumentHistory(docId);
      setDocHistory(items);
    } catch (err) {
      setGlobalError(err instanceof Error ? err.message : '変更履歴取得に失敗しました。');
    } finally {
      setHistoryLoading(false);
    }
  }

  async function loadLawTypes() {
    try {
      const types = await fetchLawTypes();
      setLawTypeOptions(types);
    } catch {
      // optional
    }
  }

  async function loadMinutesStatus() {
    try {
      const status = await fetchMinutesStatus();
      setMinutesStatus(status);
    } catch (err) {
      setGlobalError(err instanceof Error ? err.message : '会議録ステータス取得に失敗しました。');
    }
  }

  async function loadMinutesSpeakers(params: {
    role?: string;
    section?: string;
    meetingId?: number | null;
    fromDate?: string;
    toDate?: string;
  } = {}) {
    try {
      const speakers = await fetchMinutesSpeakers(params);
      setMinutesSpeakers(speakers);
    } catch {
      // optional
    }
  }

  async function loadAllMinutesSpeakers() {
    try {
      const speakers = await fetchMinutesSpeakers();
      setAllMinutesSpeakers(speakers);
    } catch {
      // optional
    }
  }

  async function loadMinutesMeetings() {
    try {
      const meetings = await fetchMinutesMeetings();
      setMinutesMeetings(meetings);
    } catch {
      // optional
    }
  }

  async function triggerMinutesSync(recentDays = 365) {
    if (user?.isGuest) {
      setGlobalError('ゲスト権限では会議録同期を実行できません。');
      return;
    }
    setMinutesSyncing(true);
    setGlobalError(null);
    try {
      await runMinutesSync(recentDays);
      const [status, runs, speakers, meetings] = await Promise.all([fetchMinutesStatus(), fetchSyncRuns(), fetchMinutesSpeakers(), fetchMinutesMeetings()]);
      setMinutesStatus(status);
      setSyncRuns(runs);
      setMinutesSpeakers(speakers);
      setAllMinutesSpeakers(speakers);
      setMinutesMeetings(meetings);
    } catch (err) {
      setGlobalError(err instanceof Error ? err.message : '会議録同期の起動に失敗しました。');
      setMinutesSyncing(false);
    }
  }

  async function submitMinutesSearch(overrides: Partial<{
    query: string;
    speaker: string;
    role: string;
    section: string;
    meetingId: number | null;
    fromDate: string;
    toDate: string;
    matchMode: 'exact' | 'related';
    op: 'AND' | 'OR';
    includeReplies: boolean;
    limit: MinutesSearchLimit;
    context: 'none' | 'wide';
    resultMode: 'utterance' | 'meeting' | 'table';
    destinationPage: MinutesPage;
  }> = {}) {
    const query = (overrides.query ?? minutesQuery).trim();
    const speaker = (overrides.speaker ?? minutesSpeaker).trim();
    const role = overrides.role ?? minutesRole;
    const section = overrides.section ?? minutesSection;
    const meetingId = overrides.meetingId ?? minutesMeetingId;
    const fromDate = overrides.fromDate ?? minutesFromDate;
    const toDate = overrides.toDate ?? minutesToDate;
    const matchMode = overrides.matchMode ?? minutesMatchMode;
    const op = overrides.op ?? minutesOp;
    const includeReplies = overrides.includeReplies ?? minutesIncludeReplies;
    const limit = overrides.limit ?? minutesLimit;
    const context = overrides.context ?? 'none';
    const resultMode = overrides.resultMode;
    const destinationPage = overrides.destinationPage ?? 'results';
    const meeting = meetingId ? minutesMeetings.find((item) => item.id === meetingId) || null : null;
    if (
      !query
      && !speaker
      && role === 'all'
      && section === 'all'
      && !meetingId
      && !fromDate
      && !toDate
    ) return;
    setMinutesSearching(true);
    setGlobalError(null);
    try {
      const historyLabel = [
        query || '',
        speaker ? `発言者:${speaker}` : '',
        meeting ? meeting.meetingName : '',
        role !== 'all' ? minutesRoleLabel(role) : '',
      ].filter(Boolean).join(' / ') || '会議録検索';
      const historyItem: MinutesSearchHistoryItem = {
        id: `${Date.now()}`,
        label: historyLabel,
        query,
        speaker,
        role,
        section,
        meetingId,
        fromDate,
        toDate,
        matchMode,
        op,
        includeReplies,
        createdAt: new Date().toISOString(),
      };
      setMinutesHistory((prev) => {
        const next = [historyItem, ...prev.filter((item) => JSON.stringify({ ...item, id: '', createdAt: '' }) !== JSON.stringify({ ...historyItem, id: '', createdAt: '' }))].slice(0, 20);
        saveMinutesSearchHistory(next);
        return next;
      });
      const resp = await searchMinutes({
        q: query || undefined,
        speaker: speaker || undefined,
        role,
        section,
        meetingId: meetingId || undefined,
        matchMode,
        op,
        fromDate: fromDate || undefined,
        toDate: toDate || undefined,
        limit,
        context,
      });
      startTransition(() => {
        setMinutesResults(resp.items);
        setMinutesTotal(resp.total);
        setMinutesVisibleResultCount(limit === 'all' ? Math.min(MINUTES_INITIAL_RENDER_LIMIT, resp.items.length) : resp.items.length);
        setSelectedMinutesResult(resp.items[0] || null);
        setMinutesExpandedResultIds(new Set());
        setMinutesResultMode(resultMode ?? ((speaker || role !== 'all') ? 'meeting' : 'utterance'));
        setMinutesSearchReturnPage(minutesPage);
        setMinutesPage(destinationPage);
      });
    } catch (err) {
      setGlobalError(err instanceof Error ? err.message : '会議録検索に失敗しました。');
    } finally {
      setMinutesSearching(false);
    }
  }

  function applyMinutesHistory(item: MinutesSearchHistoryItem, run = false) {
    setMinutesQuery(item.query);
    setMinutesSpeaker(item.speaker);
    setMinutesRole(item.role);
    setMinutesSection(item.section);
    setMinutesMeetingId(item.meetingId);
    setMinutesFromDate(item.fromDate);
    setMinutesToDate(item.toDate);
    setMinutesSearchYear(item.fromDate && item.toDate && item.fromDate.slice(0, 4) === item.toDate.slice(0, 4) && item.fromDate.endsWith('-01-01') && item.toDate.endsWith('-12-31') ? item.fromDate.slice(0, 4) : '');
    setMinutesMatchMode(item.matchMode);
    setMinutesOp(item.op);
    setMinutesIncludeReplies(item.includeReplies);
    if (run) {
      void submitMinutesSearch({
        query: item.query,
        speaker: item.speaker,
        role: item.role,
        section: item.section,
        meetingId: item.meetingId,
        fromDate: item.fromDate,
        toDate: item.toDate,
        matchMode: item.matchMode,
        op: item.op,
        includeReplies: item.includeReplies,
      });
    }
  }

  function resetMinutesSearchFields() {
    setMinutesQuery('');
    setMinutesSpeaker('');
    setMinutesRole('all');
    setMinutesSection('all');
    setMinutesMeetingId(null);
    setMinutesSearchYear('');
    setMinutesFromDate('');
    setMinutesToDate('');
  }

  function resetMinutesSearchOptions() {
    setMinutesMatchMode(DEFAULT_MINUTES_MATCH_MODE);
    setMinutesOp(DEFAULT_MINUTES_OP);
    setMinutesIncludeReplies(DEFAULT_MINUTES_INCLUDE_REPLIES);
    setMinutesIncludeChair(DEFAULT_MINUTES_INCLUDE_CHAIR);
    setMinutesSortOrder(DEFAULT_MINUTES_SORT_ORDER);
    setMinutesLimit(DEFAULT_MINUTES_SEARCH_LIMIT);
  }

  function resetMinutesSearchResults(resultMode: 'utterance' | 'meeting' | 'table' = 'utterance') {
    setMinutesResults([]);
    setMinutesTotal(0);
    setMinutesVisibleResultCount(0);
    setMinutesExpandedResultIds(new Set());
    setSelectedMinutesResult(null);
    setMinutesResultMode(resultMode);
    setMinutesSearchReturnPage('home');
  }

  function resetMinutesSearchState(resultMode: 'utterance' | 'meeting' | 'table' = 'utterance') {
    resetMinutesSearchFields();
    resetMinutesSearchOptions();
    resetMinutesSearchResults(resultMode);
  }

  function goToMinutesSearchMethod(page: MinutesSearchMethodPage) {
    if (page !== minutesPage) {
      resetMinutesSearchState(page === 'speaker' ? 'meeting' : 'utterance');
    }
    setMinutesPage(page);
  }

  function clearMinutesSearch() {
    resetMinutesSearchState('utterance');
  }

  function clearMinutesSpeakerSearch() {
    resetMinutesSearchState('meeting');
    setMinutesPage('speaker');
  }

  function clearMinutesCollectionSearch() {
    resetMinutesSearchState('utterance');
    setMinutesPage('collection');
  }

  function runMinutesSpeakerSearch(speakerName: string) {
    const speaker = speakerName.trim();
    if (!speaker) return;
    setMinutesQuery('');
    setMinutesSpeaker(speaker);
    void submitMinutesSearch({
      query: '',
      speaker,
      matchMode: 'exact',
      op: 'AND',
      limit: minutesLimit,
    });
  }

  function submitMinutesCollection() {
    const speaker = minutesSpeaker.trim();
    if (!speaker) {
      setGlobalError('発言集を作成する発言者を選択してください。');
      return;
    }
    setMinutesQuery('');
    setMinutesSortOrder('old');
    void submitMinutesSearch({
      query: '',
      speaker,
      matchMode: 'exact',
      op: 'AND',
      includeReplies: minutesIncludeReplies,
      limit: minutesLimit,
      context: 'wide',
      resultMode: 'utterance',
      destinationPage: 'collectionResults',
    });
  }

  function setMinutesSearchYearRange(value: string) {
    setMinutesSearchYear(value);
    setMinutesMeetingId(null);
    if (!value) {
      setMinutesFromDate('');
      setMinutesToDate('');
      return;
    }
    setMinutesFromDate(`${value}-01-01`);
    setMinutesToDate(`${value}-12-31`);
  }

  function selectMinutesResult(result: MinutesSearchResult) {
    setSelectedMinutesResult(result);
    setMinutesReaderMode('unit');
    setMinutesPage('detail');
  }

  async function openMinutesMeeting(meeting: MinutesMeeting) {
    setMinutesMeetingDetailLoading(true);
    setGlobalError(null);
    setMinutesMeetingId(meeting.id);
    setMinutesSection(meeting.section || 'all');
    setMinutesQuery('');
    setMinutesSpeaker('');
    setSelectedMinutesResult(null);
    setMinutesDayDetail(null);
    setSelectedMinutesMeetingDetail(null);
    setSelectedMinutesMeetingDayId(null);
    setMinutesPage('meetingDetail');
    try {
      const detail = await fetchMinutesMeetingDetail(meeting.id);
      setSelectedMinutesMeetingDetail(detail);
      setSelectedMinutesMeetingDayId(detail.days[0]?.id ?? null);
    } catch (err) {
      setGlobalError(err instanceof Error ? err.message : '会議録の取得に失敗しました。');
      setMinutesPage('browse');
    } finally {
      setMinutesMeetingDetailLoading(false);
    }
  }

  function toggleMinutesResultExpanded(id: number) {
    setMinutesExpandedResultIds((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }

  async function loadMinutesDay(result: MinutesSearchResult | null) {
    if (!result) {
      setMinutesDayDetail(null);
      return;
    }
    setMinutesDetailLoading(true);
    try {
      const detail = await fetchMinutesDayDetail(result.dayId);
      setMinutesDayDetail(detail);
    } catch {
      setMinutesDayDetail(null);
    } finally {
      setMinutesDetailLoading(false);
    }
  }

  async function loadRelatedArticles(doc: DocumentDetail) {
    if (!doc.title) return;
    setRelatedLoading(true);
    setRelatedResults([]);
    try {
      const titleWords = doc.title.replace(/（.*?）/g, '').trim().split(/\s+/).slice(0, 3).join(' ');
      const fields: SearchField[] = [{ q: titleWords, op: 'AND' }, { q: '', op: 'AND' }, { q: '', op: 'AND' }, { q: '', op: 'AND' }];
      const related = await searchLawsForRelated(fields, doc.id, doc.source);
      setRelatedResults(related);
    } catch {
      // optional
    } finally {
      setRelatedLoading(false);
    }
  }

  async function openDiffView(histItem: DocHistoryItem, docId: number) {
    if (histItem.fullText !== undefined) {
      setDiffEntry(histItem);
      return;
    }
    setDiffLoading(true);
    try {
      const detail = await fetchDocumentHistoryDetail(docId, histItem.id);
      setDiffEntry(detail);
    } catch (err) {
      setGlobalError(err instanceof Error ? err.message : '履歴詳細取得に失敗しました。');
    } finally {
      setDiffLoading(false);
    }
  }

  async function handleClearCache(scope: 'search' | 'ask' | 'all') {
    if (user?.isGuest) {
      setGlobalError('ゲスト権限ではキャッシュを削除できません。');
      return;
    }
    setBusy(true);
    setGlobalError(null);
    try {
      await clearCache(scope);
    } catch (err) {
      setGlobalError(err instanceof Error ? err.message : 'キャッシュクリアに失敗しました。');
    } finally {
      setBusy(false);
    }
  }

  async function submitSearch(page = 0) {
    if (!searchFields.some((f) => f.q.trim())) return;
    setSearching(true);
    setGlobalError(null);
    setSearchPage(page);
    setRelatedResults([]);
    // オートコンプリート履歴に追加
    const q0 = searchFields[0].q.trim();
    if (q0) {
      const hist = [q0, ...searchHistoryRef.current.filter((h) => h !== q0)].slice(0, 10);
      searchHistoryRef.current = hist;
      saveSearchHistory(hist);
    }
    try {
      const resp = await searchLaws({
        fields: searchFields,
        source: searchSource,
        limit: 20,
        offset: page * 20,
        lawType: searchLawType || undefined,
        fromDate: searchFromDate || undefined,
        toDate: searchToDate || undefined,
        fuzzy: searchFuzzy,
      });
      setResults(resp.items);
      setSearchTotal(resp.total);
      if (resp.items.length > 0) openSearchResult(resp.items[0]);
      else {
        setSelectedDoc(null);
        setSelectedDocId(null);
        setPendingSelectedArticleHit(null);
        setActiveSelectedArticleHit(null);
      }
    } catch (err) {
      setGlobalError(err instanceof Error ? err.message : '検索に失敗しました。');
    } finally {
      setSearching(false);
    }
  }

  async function submitQuestion(q?: string) {
    const queryText = (q ?? question).trim();
    if (!queryText) return;
    if (q) setQuestion(q);
    setAsking(true);
    setGlobalError(null);
    setExpandedArticleKeys(new Set());
    try {
      const resp = await askQuestion(queryText);
      setAnswer(resp);
      setQuestionHistory((prev) => {
        const next = [queryText, ...prev.filter((h) => h !== queryText)].slice(0, 5);
        return next;
      });
    } catch (err) {
      setGlobalError(err instanceof Error ? err.message : '質問処理に失敗しました。');
    } finally {
      setAsking(false);
    }
  }

  async function saveSyncSettings() {
    if (user?.isGuest) {
      setGlobalError('ゲスト権限では同期設定を変更できません。');
      return;
    }
    setBusy(true);
    setGlobalError(null);
    try {
      const status = await updateSyncSettings(syncForm);
      setSyncStatus(status);
      setSyncForm({
        enabled: status.enabled,
        dayOfMonth: status.dayOfMonth,
        hour: status.hour,
        minute: status.minute,
        sourceScope: status.sourceScope,
      });
      const runs = await fetchSyncRuns();
      setSyncRuns(runs);
    } catch (err) {
      setGlobalError(err instanceof Error ? err.message : '同期設定の保存に失敗しました。');
    } finally {
      setBusy(false);
    }
  }

  async function triggerSync(scope: SourceScope) {
    if (user?.isGuest) {
      setGlobalError('ゲスト権限では同期を実行できません。');
      return;
    }
    setBusy(true);
    setGlobalError(null);
    try {
      await runSync(scope);
      const [status, runs] = await Promise.all([fetchSyncStatus(), fetchSyncRuns()]);
      setSyncStatus(status);
      setSyncRuns(runs);
    } catch (err) {
      setGlobalError(err instanceof Error ? err.message : '同期に失敗しました。');
    } finally {
      setBusy(false);
    }
  }

  async function triggerReindex() {
    if (user?.isGuest) {
      setGlobalError('ゲスト権限では再索引を実行できません。');
      return;
    }
    setBusy(true);
    setGlobalError(null);
    try {
      await runReindex(10);
      const runs = await fetchSyncRuns();
      setSyncRuns(runs);
    } catch (err) {
      setGlobalError(err instanceof Error ? err.message : '再索引の起動に失敗しました。');
    } finally {
      setBusy(false);
    }
  }

  const runningSyncRun = useMemo(
    () => syncRuns.find((run) => run.status === 'running' && !['reindex', 'dictionary-update', 'minutes-dictionary-update', 'minutes-sync'].includes(String(run.summary?.operation || ''))) ?? null,
    [syncRuns],
  );

  const runningReindexRun = useMemo(
    () => syncRuns.find((run) => run.status === 'running' && run.summary?.operation === 'reindex') ?? null,
    [syncRuns],
  );

  const runningDictionaryRun = useMemo(
    () => syncRuns.find((run) => run.status === 'running' && run.summary?.operation === 'dictionary-update') ?? null,
    [syncRuns],
  );

  const runningMinutesDictionaryRun = useMemo(
    () => syncRuns.find((run) => run.status === 'running' && run.summary?.operation === 'minutes-dictionary-update') ?? null,
    [syncRuns],
  );

  const runningMinutesRun = useMemo(
    () => syncRuns.find((run) => run.status === 'running' && run.summary?.operation === 'minutes-sync') ?? null,
    [syncRuns],
  );

  const statCards = useMemo(
    () => [
      {
        label: '例規数',
        total: `${syncStatus.documentCount.toLocaleString()}件`,
        rows: [
          { label: '美祢市例規', value: `${syncStatus.mineCityDocumentCount.toLocaleString()}件` },
          { label: '地方自治法', value: `${syncStatus.egovDocumentCount.toLocaleString()}件` },
          { label: '地方公務員法', value: `${syncStatus.localPublicServiceDocumentCount.toLocaleString()}件` },
        ],
      },
      {
        label: '条文数',
        total: `${syncStatus.articleCount.toLocaleString()}条`,
        rows: [
          { label: '美祢市例規', value: `${syncStatus.mineCityArticleCount.toLocaleString()}条` },
          { label: '地方自治法', value: `${syncStatus.egovArticleCount.toLocaleString()}条` },
          { label: '地方公務員法', value: `${syncStatus.localPublicServiceArticleCount.toLocaleString()}条` },
        ],
      },
      {
        label: '会議録',
        total: `${minutesStatus.dayCount.toLocaleString()}件`,
        rows: [
          { label: '発言', value: `${minutesStatus.utteranceCount.toLocaleString()}件` },
          { label: '発言者', value: `${minutesStatus.speakerCount.toLocaleString()}人` },
          { label: '表', value: `${minutesStatus.tableCount.toLocaleString()}件` },
        ],
      },
    ],
    [minutesStatus, syncStatus],
  );

  const browseTree = useMemo(() => buildBrowseTree(browseSource, browseList, browseCategories), [browseList, browseSource, browseCategories]);
  const browseDocArticleTree = useMemo(() => buildArticleGroupTree(browseDoc?.articles || []), [browseDoc]);
  const selectedDocArticleTree = useMemo(() => buildArticleGroupTree(selectedDoc?.articles || []), [selectedDoc]);
  const groupedResults = useMemo(() => groupSearchResults(results), [results]);
  const selectedSearchResult = useMemo(() => {
    if (!selectedDocId) return null;
    if (activeSelectedArticleHit?.documentId === selectedDocId) {
      const activeHit = results.find((item) => (
        item.documentId === activeSelectedArticleHit.documentId
        && item.articleId === activeSelectedArticleHit.articleId
      ));
      if (activeHit) return activeHit;
    }
    return results.find((item) => item.documentId === selectedDocId) || null;
  }, [activeSelectedArticleHit, results, selectedDocId]);
  const selectedSearchHighlightTerms = selectedSearchResult?.highlightTerms?.length
    ? selectedSearchResult.highlightTerms
    : searchFields.flatMap((f) => (f.q.trim() ? f.q.trim().split(/\s+/) : []));
  const selectedSearchRelatedHighlightTerms = selectedSearchResult?.relatedHighlightTerms || [];
  const deferredMinutesSpeaker = useDeferredValue(minutesSpeaker);
  const filteredMinutesSpeakers = useMemo(() => {
    const needle = deferredMinutesSpeaker.trim();
    return minutesSpeakers
      .filter((speaker) => !needle || `${speaker.displayName} ${speaker.title}`.includes(needle));
  }, [minutesSpeakers, deferredMinutesSpeaker]);
  const groupedMinutesSpeakers = useMemo(() => {
    const groups = new Map<string, {
      displayName: string;
      title: string;
      roleSummary: string;
      utteranceCount: number;
      candidateRank: number;
      roles: Set<string>;
      titles: Set<string>;
    }>();
    for (const speaker of filteredMinutesSpeakers) {
      const displayName = (speaker.displayName || '氏名なし').trim();
      const key = displayName.replace(/\s+/g, '');
      const existing = groups.get(key);
      if (existing) {
        existing.utteranceCount += speaker.utteranceCount;
        if (speaker.role) existing.roles.add(speaker.role);
        if (speaker.title) existing.titles.add(speaker.title);
        continue;
      }
      groups.set(key, {
        displayName,
        title: speaker.title || '',
        roleSummary: minutesRoleLabel(speaker.role),
        utteranceCount: speaker.utteranceCount,
        roles: new Set(speaker.role ? [speaker.role] : []),
        titles: new Set(speaker.title ? [speaker.title] : []),
      });
    }
    return [...groups.values()]
      .map((speaker) => {
        const titles = [...speaker.titles].filter(Boolean);
        const roles = [...speaker.roles].filter(Boolean);
        return {
          displayName: speaker.displayName,
          title: titles.slice(0, 2).join(' / '),
          roleSummary: roles.length === 1 ? minutesRoleLabel(roles[0]) : '複数区分',
          utteranceCount: speaker.utteranceCount,
          candidateRank: minutesSpeakerCandidateRank(speaker.roles, speaker.titles),
        };
      })
      .sort((a, b) => {
        if (a.candidateRank !== b.candidateRank) return a.candidateRank - b.candidateRank;
        return b.utteranceCount - a.utteranceCount || a.displayName.localeCompare(b.displayName, 'ja-JP');
      });
  }, [filteredMinutesSpeakers]);
  const minutesExecutiveTitleFilters = useMemo(() => {
    const counts = new Map<string, number>();
    const sourceSpeakers = allMinutesSpeakers.length > 0 ? allMinutesSpeakers : minutesSpeakers;
    for (const speaker of sourceSpeakers) {
      if (speaker.role !== 'answerer') continue;
      const title = (speaker.title || '').trim();
      if (!title) continue;
      counts.set(title, (counts.get(title) || 0) + speaker.utteranceCount);
    }
    if (counts.size === 0) {
      return MINUTES_EXECUTIVE_TITLE_FALLBACKS.map((title) => ({ title, count: null as number | null }));
    }
    return [...counts.entries()]
      .map(([title, count]) => ({ title, count }))
      .sort((a, b) => {
        const byRank = minutesExecutiveTitleRank(a.title) - minutesExecutiveTitleRank(b.title);
        if (byRank !== 0) return byRank;
        const byTitle = compareOrderText(a.title, b.title);
        if (byTitle !== 0) return byTitle;
        return b.count - a.count;
      });
  }, [allMinutesSpeakers, minutesSpeakers]);
  const minutesBrowseFiscalYears = useMemo(() => {
    return [...new Set(minutesMeetings.map((meeting) => calendarYearFromDate(meeting.fromDate || meeting.toDate)).filter((year): year is number => year != null))]
      .sort((a, b) => b - a);
  }, [minutesMeetings]);
  const effectiveMinutesBrowseFiscalYear = minutesBrowseFiscalYear || (minutesBrowseFiscalYears[0] ? String(minutesBrowseFiscalYears[0]) : 'all');
  const minutesBrowseSections = useMemo(() => {
    const counts = new Map<string, number>();
    const selectedYear = effectiveMinutesBrowseFiscalYear === 'all' ? null : Number(effectiveMinutesBrowseFiscalYear);
    for (const meeting of minutesMeetings) {
      const calendarYear = calendarYearFromDate(meeting.fromDate || meeting.toDate);
      if (selectedYear != null && calendarYear !== selectedYear) continue;
      const section = meeting.section || '未分類';
      counts.set(section, (counts.get(section) || 0) + 1);
    }
    return [...counts.entries()]
      .sort(([left], [right]) => {
        const byOrder = minutesSectionOrder(left) - minutesSectionOrder(right);
        return byOrder || left.localeCompare(right, 'ja-JP');
      })
      .map(([section, count]) => ({ section, count }));
  }, [minutesMeetings, effectiveMinutesBrowseFiscalYear]);
  const browsedMinutesMeetings = useMemo(() => {
    const selectedYear = effectiveMinutesBrowseFiscalYear === 'all' ? null : Number(effectiveMinutesBrowseFiscalYear);
    return minutesMeetings
      .filter((meeting) => {
        const calendarYear = calendarYearFromDate(meeting.fromDate || meeting.toDate);
        if (selectedYear != null && calendarYear !== selectedYear) return false;
        if (minutesBrowseSection !== 'all' && (meeting.section || '未分類') !== minutesBrowseSection) return false;
        return true;
      })
      .sort((a, b) => {
        const bySection = minutesSectionOrder(a.section || '') - minutesSectionOrder(b.section || '');
        if (bySection !== 0) return bySection;
        const left = a.fromDate || a.toDate || '';
        const right = b.fromDate || b.toDate || '';
        const byDate = left.localeCompare(right);
        if (byDate !== 0) return byDate;
        return (a.meetingName || a.title).localeCompare(b.meetingName || b.title, 'ja-JP', { numeric: true });
      });
  }, [minutesMeetings, effectiveMinutesBrowseFiscalYear, minutesBrowseSection]);
  const searchMinutesMeetingOptions = useMemo(() => {
    const selectedYear = minutesSearchYear ? Number(minutesSearchYear) : null;
    return minutesMeetings.filter((meeting) => {
      if (minutesSection !== 'all' && (meeting.section || '未分類') !== minutesSection) return false;
      if (selectedYear != null && calendarYearFromDate(meeting.fromDate || meeting.toDate) !== selectedYear) return false;
      return true;
    });
  }, [minutesMeetings, minutesSearchYear, minutesSection]);
  const browsedMinutesMeetingsBySection = useMemo(() => {
    const groups = new Map<string, MinutesMeeting[]>();
    for (const meeting of browsedMinutesMeetings) {
      const section = meeting.section || '未分類';
      const rows = groups.get(section) || [];
      rows.push(meeting);
      groups.set(section, rows);
    }
    return [...groups.entries()].sort(([left], [right]) => {
      const byOrder = minutesSectionOrder(left) - minutesSectionOrder(right);
      return byOrder || left.localeCompare(right, 'ja-JP');
    });
  }, [browsedMinutesMeetings]);
  const deferredMinutesResults = useDeferredValue(minutesResults);
  const meetingGroupedMinutesResults = useMemo(() => {
    const groups = new Map<number, { dayId: number; title: string; section: string; meetingDate: string | null; count: number; speakers: Set<string>; first: MinutesSearchResult }>();
    for (const result of deferredMinutesResults) {
      const existing = groups.get(result.dayId);
      if (existing) {
        existing.count += 1;
        existing.speakers.add(result.speakerName);
        continue;
      }
      groups.set(result.dayId, {
        dayId: result.dayId,
        title: result.meetingName || result.dayTitle,
        section: result.section,
        meetingDate: result.meetingDate,
        count: 1,
        speakers: new Set([result.speakerName]),
        first: result,
      });
    }
    return [...groups.values()];
  }, [deferredMinutesResults]);
  const sortedMinutesResults = useMemo(() => {
    const values = [...deferredMinutesResults];
    values.sort((a, b) => {
      const left = a.meetingDate || '';
      const right = b.meetingDate || '';
      const byDate = left.localeCompare(right);
      if (byDate !== 0) return minutesSortOrder === 'old' ? byDate : -byDate;
      return a.order - b.order;
    });
    return values;
  }, [deferredMinutesResults, minutesSortOrder]);
  const visibleSortedMinutesResults = useMemo(() => {
    if (!minutesVisibleResultCount || sortedMinutesResults.length <= minutesVisibleResultCount) return sortedMinutesResults;
    return sortedMinutesResults.slice(0, minutesVisibleResultCount);
  }, [minutesVisibleResultCount, sortedMinutesResults]);
  const groupedVisibleMinutesResults = useMemo(() => {
    const groups = new Map<number, {
      dayId: number;
      title: string;
      section: string;
      meetingDate: string | null;
      items: MinutesSearchResult[];
    }>();
    for (const result of visibleSortedMinutesResults) {
      const existing = groups.get(result.dayId);
      if (existing) {
        existing.items.push(result);
        continue;
      }
      groups.set(result.dayId, {
        dayId: result.dayId,
        title: result.meetingName || result.dayTitle,
        section: result.section,
        meetingDate: result.meetingDate,
        items: [result],
      });
    }
    return [...groups.values()];
  }, [visibleSortedMinutesResults]);
  const minutesCollectionGroups = useMemo(() => {
    type CollectionItem = MinutesExchangeItem & {
      isHit: boolean;
      isTargetSpeaker: boolean;
      highlightTerms: string[];
      relatedHighlightTerms: string[];
    };
    const groups = new Map<number, {
      dayId: number;
      meetingDate: string | null;
      section: string;
      meetingName: string;
      dayTitle: string;
      pdfUrl: string;
      items: CollectionItem[];
      seen: Set<number>;
      itemById: Map<number, CollectionItem>;
    }>();
    const targetSpeaker = minutesSpeaker.trim();
    for (const result of sortedMinutesResults) {
      const group = groups.get(result.dayId) || {
        dayId: result.dayId,
        meetingDate: result.meetingDate,
        section: result.section,
        meetingName: result.meetingName,
        dayTitle: result.dayTitle,
        pdfUrl: result.pdfUrl,
        items: [],
        seen: new Set<number>(),
        itemById: new Map<number, CollectionItem>(),
      };
      const baseItem: MinutesExchangeItem = {
        id: result.id,
        order: result.order,
        speakerName: result.speakerName,
        speakerTitle: result.speakerTitle,
        speakerRole: result.speakerRole,
        speechType: result.speechType,
        text: result.text,
        pageStart: result.pageStart,
        pageEnd: result.pageEnd,
        positionTopStart: 0,
        positionTopEnd: 0,
      };
      let sourceItems = minutesIncludeReplies && result.exchange.length > 0 ? result.exchange : [baseItem];
      if (minutesIncludeReplies && sourceItems.length > 1) {
        const hitIndex = sourceItems.findIndex((item) => item.id === result.id);
        if (hitIndex >= 0) {
          let startIndex = hitIndex;
          if (result.speakerRole !== 'questioner') {
            for (let i = hitIndex - 1; i >= 0; i -= 1) {
              if (sourceItems[i].speakerRole === 'questioner') {
                startIndex = i;
                break;
              }
            }
          }
          let endIndex = sourceItems.length - 1;
          for (let i = hitIndex + 1; i < sourceItems.length; i += 1) {
            const next = sourceItems[i];
            const nextIsTarget = targetSpeaker
              ? next.speakerName === targetSpeaker || next.speakerTitle === targetSpeaker
              : next.id === result.id;
            if (next.speakerRole === 'questioner' && !nextIsTarget) {
              endIndex = i - 1;
              break;
            }
          }
          sourceItems = sourceItems.slice(startIndex, endIndex + 1);
        } else {
          sourceItems = [baseItem];
        }
      }
      for (const item of sourceItems) {
        if (item.speakerRole === 'chair' && !minutesIncludeChair) continue;
        const isHit = item.id === result.id;
        const isTargetSpeaker = targetSpeaker ? item.speakerName === targetSpeaker || item.speakerTitle === targetSpeaker : isHit;
        if (group.seen.has(item.id)) {
          const existing = group.itemById.get(item.id);
          if (existing) {
            existing.isHit = existing.isHit || isHit;
            existing.isTargetSpeaker = existing.isTargetSpeaker || isTargetSpeaker;
            if (isHit) {
              existing.highlightTerms = result.highlightTerms || [];
              existing.relatedHighlightTerms = result.relatedHighlightTerms || [];
            }
          }
          continue;
        }
        group.seen.add(item.id);
        const collectionItem = {
          ...item,
          isHit,
          isTargetSpeaker,
          highlightTerms: isHit ? result.highlightTerms || [] : [],
          relatedHighlightTerms: isHit ? result.relatedHighlightTerms || [] : [],
        };
        group.itemById.set(item.id, collectionItem);
        group.items.push(collectionItem);
      }
      groups.set(result.dayId, group);
    }
    return [...groups.values()]
      .map((group) => ({
        ...group,
        items: group.items.length > 1 ? group.items.sort((a, b) => a.order - b.order) : group.items,
      }))
      .filter((group) => group.items.length > 0);
  }, [minutesIncludeChair, minutesIncludeReplies, minutesSpeaker, sortedMinutesResults]);
  const minutesCollectionItemCount = useMemo(
    () => minutesCollectionGroups.reduce((sum, group) => sum + group.items.length, 0),
    [minutesCollectionGroups],
  );
  const minutesHighlightTerms = useMemo(() => buildHighlightTerms(minutesQuery), [minutesQuery]);
  const currentDayUtterances = minutesDayDetail?.utterances || [];
  const currentDayContentItems = minutesDayDetail?.contentItems?.length
    ? minutesDayDetail.contentItems
    : currentDayUtterances.map((utterance) => ({ type: 'utterance' as const, utterance }));
  const selectedMinutesUnitItems = useMemo(() => {
    if (!selectedMinutesResult) return [];
    if (!minutesIncludeReplies) return [selectedMinutesResult];
    if (selectedMinutesResult.exchange.length > 0) return selectedMinutesResult.exchange;
    if (!minutesDayDetail || minutesDayDetail.id !== selectedMinutesResult.dayId) return [selectedMinutesResult];
    const index = minutesDayDetail.utterances.findIndex((item) => item.id === selectedMinutesResult.id);
    if (index < 0) return [selectedMinutesResult];
    return minutesDayDetail.utterances.slice(Math.max(0, index - 2), index + 5);
  }, [minutesDayDetail, minutesIncludeReplies, selectedMinutesResult]);
  const selectedMinutesUtteranceIndex = useMemo(
    () => currentDayUtterances.findIndex((item) => item.id === selectedMinutesResult?.id),
    [currentDayUtterances, selectedMinutesResult?.id],
  );
  const selectedMinutesSpeakerNames = useMemo(() => {
    const names = new Map<string, number>();
    for (const item of currentDayUtterances) {
      const key = `${item.speakerTitle} ${item.speakerName}`.trim();
      if (!key) continue;
      names.set(key, (names.get(key) || 0) + 1);
    }
    return [...names.entries()].sort((a, b) => b[1] - a[1]).slice(0, 40);
  }, [currentDayUtterances]);
  const selectedMinutesDayId = selectedMinutesResult?.dayId ?? null;
  const selectedDayMinutesHits = useMemo(() => {
    if (!selectedMinutesDayId) return [];
    const hits = sortedMinutesResults.filter((result) => result.dayId === selectedMinutesDayId);
    const deduped = new Map<number, MinutesSearchResult>();
    for (const hit of hits) {
      deduped.set(hit.id, hit);
    }
    return [...deduped.values()].sort((a, b) => a.order - b.order);
  }, [selectedMinutesDayId, sortedMinutesResults]);
  const selectedDayMinutesHitIds = useMemo(
    () => new Set(selectedDayMinutesHits.map((hit) => hit.id)),
    [selectedDayMinutesHits],
  );
  const selectedDayMinutesHitTermMap = useMemo(() => {
    const map = new Map<number, { highlightTerms: string[]; relatedHighlightTerms: string[] }>();
    for (const hit of selectedDayMinutesHits) {
      map.set(hit.id, {
        highlightTerms: hit.highlightTerms || [],
        relatedHighlightTerms: hit.relatedHighlightTerms || [],
      });
    }
    return map;
  }, [selectedDayMinutesHits]);
  const firstSelectedDayMinutesHitId = selectedDayMinutesHits[0]?.id ?? null;

  function minutesExactHighlightTerms(item?: { id?: number; highlightTerms?: string[] } | null): string[] {
    return item?.highlightTerms?.length
      ? item.highlightTerms
      : selectedDayMinutesHitTermMap.get(Number(item?.id || 0))?.highlightTerms || minutesHighlightTerms;
  }

  function minutesRelatedHighlightTerms(item?: { id?: number; relatedHighlightTerms?: string[] } | null): string[] {
    return item?.relatedHighlightTerms?.length
      ? item.relatedHighlightTerms
      : selectedDayMinutesHitTermMap.get(Number(item?.id || 0))?.relatedHighlightTerms || [];
  }

  function scrollMinutesUtteranceIntoView(utteranceId: number | null | undefined) {
    if (!utteranceId) return;
    if (minutesReaderScrollFrameRef.current != null) {
      window.cancelAnimationFrame(minutesReaderScrollFrameRef.current);
    }
    minutesReaderScrollFrameRef.current = window.requestAnimationFrame(() => {
      minutesReaderScrollFrameRef.current = window.requestAnimationFrame(() => {
        minutesReaderScrollFrameRef.current = null;
        scrollElementIntoContainer(`minutes-utterance-${utteranceId}`, minutesReaderScrollRef.current, 'start', 'auto', 12);
      });
    });
  }

  function selectMinutesHit(hit: MinutesSearchResult) {
    setSelectedMinutesResult(hit);
    if (!['unit', 'full', 'list'].includes(minutesReaderMode)) {
      setMinutesReaderMode('unit');
    }
  }

  useEffect(() => {
    if (minutesPage !== 'detail' || !['unit', 'full', 'list'].includes(minutesReaderMode)) return;
    if (!currentDayUtterances.length) return;
    const selectedId = selectedMinutesResult?.id ?? null;
    const targetId = minutesReaderMode === 'unit' || minutesReaderMode === 'list'
      ? selectedId
      : selectedId && selectedDayMinutesHitIds.has(selectedId)
        ? selectedId
        : firstSelectedDayMinutesHitId;
    scrollMinutesUtteranceIntoView(targetId);
  }, [
    minutesPage,
    minutesReaderMode,
    currentDayUtterances.length,
    selectedMinutesResult?.id,
    selectedDayMinutesHitIds,
    firstSelectedDayMinutesHitId,
  ]);

  function moveSelectedMinutesUtterance(delta: number) {
    if (!minutesDayDetail || selectedMinutesUtteranceIndex < 0) return;
    const next = minutesDayDetail.utterances[selectedMinutesUtteranceIndex + delta];
    if (!next || !selectedMinutesResult) return;
    setSelectedMinutesResult({
      ...selectedMinutesResult,
      id: next.id,
      order: next.order,
      speakerName: next.speakerName,
      speakerTitle: next.speakerTitle,
      speakerRole: next.speakerRole,
      speechType: next.speechType,
      text: next.text,
      pageStart: next.pageStart,
      pageEnd: next.pageEnd,
      snippet: next.text.slice(0, 180),
      exchange: minutesDayDetail.utterances.slice(Math.max(0, selectedMinutesUtteranceIndex + delta - 2), selectedMinutesUtteranceIndex + delta + 5),
    });
  }

  const renderMinutesTableCard = (table: MinutesTable, compact = false): JSX.Element => (
    <div key={`table-${table.id}`} className={`rounded-2xl border bg-[#fbfdfb] ${compact ? 'p-3' : 'p-4'}`}>
      <p className={`mb-2 font-semibold ${compact ? 'text-xs' : 'text-sm'}`}>
        {formatMinutesTableCaption(table)}
      </p>
      <div
        className={`overflow-auto ${compact ? 'text-xs' : 'text-sm'} [&_table]:w-full [&_td]:border [&_td]:px-2 [&_td]:py-1 [&_th]:border [&_th]:bg-[#e6efe9] [&_th]:px-2 [&_th]:py-1`}
        dangerouslySetInnerHTML={{ __html: table.html }}
      />
    </div>
  );

  const isMinutesStructuralLine = (line: string): boolean => {
    return /^(日程第|〔|【|（|第[0-9０-９一二三四五六七八九十]+[、 　]|[0-9０-９]+[、.．)]|[（(][0-9０-９一二三四五六七八九十]+[）)])/.test(line.trim());
  };

  const renderMinutesText = (text: string, className = 'mt-3 text-base leading-8', highlightTerms: string[] = [], relatedHighlightTerms: string[] = []): JSX.Element => {
    const lines = text.split('\n').map((line) => line.trim()).filter(Boolean);
    return (
      <div className={`${className} min-w-0 max-w-full whitespace-normal break-words [overflow-wrap:anywhere]`}>
        {lines.map((line, index) => (
          <p key={`${index}-${line.slice(0, 16)}`} className="m-0 min-w-0 max-w-full break-words [overflow-wrap:anywhere]" style={{ textIndent: isMinutesStructuralLine(line) ? '0' : '1em' }}>
            {renderHighlightedText(line, highlightTerms, relatedHighlightTerms)}
          </p>
        ))}
      </div>
    );
  };

  const renderArticleNavTree = (nodes: ArticleGroupNode[], anchorPrefix: string, depth = 0): JSX.Element => (
    <div className={depth === 0 ? 'space-y-2' : 'mt-1 space-y-1 border-l border-border/70 pl-3'}>
      {nodes.map((node) => (
        <details key={`${anchorPrefix}-nav-${node.key}`} open={depth <= 1}>
          <summary className="cursor-pointer list-none rounded-lg px-2 py-1.5 text-sm font-semibold marker:hidden hover:bg-accent">
            <div className="flex items-center justify-between gap-2">
              <span className={depth === 0 ? 'text-foreground' : 'text-muted-foreground'}>{node.label}</span>
              <span className="shrink-0 text-xs text-muted-foreground">{countNodeArticles(node)}条</span>
            </div>
          </summary>
          <div className="space-y-1 pb-1 pl-2">
            {node.articles.length > 0 ? (
              <div className="space-y-1">
                {node.articles.map((article) => (
                  <a key={`${anchorPrefix}-nav-article-${article.id}`} className="block rounded-lg px-2 py-1.5 text-sm leading-snug hover:bg-accent" href={`#${anchorPrefix}-${article.id}`}>
                    {article.articleNumber}{article.articleTitle ? `　${article.articleTitle}` : ''}
                  </a>
                ))}
              </div>
            ) : null}
            {node.children.length > 0 ? renderArticleNavTree(node.children, anchorPrefix, depth + 1) : null}
          </div>
        </details>
      ))}
    </div>
  );

  const renderArticleBodyTree = (
    nodes: ArticleGroupNode[],
    anchorPrefix: string,
    keywords: string[],
    relatedKeywords: string[],
    articleLinks: ArticleLinkMap,
    sourceAnchorLinks: SourceAnchorLinkMap,
    sourceDocumentLinks: SourceDocumentLinkMap,
    sourceUrl: string,
    onSourceDocumentLink: (documentId: number, sourceAnchorId?: string) => void,
    onInternalAnchorLink?: () => void,
    activeArticleId: number | null = null,
    depth = 0,
  ): JSX.Element => (
    <div className={depth === 0 ? 'space-y-7' : 'mt-4 space-y-5'}>
      {nodes.map((node) => (
        <section key={`${anchorPrefix}-body-${node.key}`} className={depth === 0 ? 'space-y-4 border-b pb-7 last:border-b-0' : 'space-y-4'}>
          <h3 className={`${headingClass(depth)} ${articleHeadingTone(depth)} rounded-lg font-semibold`}>{node.label}</h3>
          {node.articles.length > 0 ? (
            <div className="space-y-5">
              {node.articles.map((article) => (
                <article
                  key={`${anchorPrefix}-article-${article.id}`}
                  id={`${anchorPrefix}-${article.id}`}
                  className={`scroll-mt-24 border-b pb-5 transition-colors last:border-b-0 ${
                    article.id === activeArticleId ? 'rounded-2xl bg-primary/10 p-4 ring-2 ring-primary/35' : 'target:bg-accent/30'
                  }`}
                >
                  <h4 className="text-lg font-semibold">{article.articleNumber}{article.articleTitle ? `　${article.articleTitle}` : ''}</h4>
                  <div className="mt-3">
                    <ArticleContent
                      text={article.text}
                      keywords={keywords}
                      relatedKeywords={relatedKeywords}
                      articleLinks={articleLinks}
                      sourceAnchorLinks={sourceAnchorLinks}
                      sourceDocumentLinks={sourceDocumentLinks}
                      sourceUrl={sourceUrl}
                      onSourceDocumentLink={onSourceDocumentLink}
                      onInternalAnchorLink={onInternalAnchorLink}
                    />
                  </div>
                </article>
              ))}
            </div>
          ) : null}
          {node.children.length > 0 ? renderArticleBodyTree(node.children, anchorPrefix, keywords, relatedKeywords, articleLinks, sourceAnchorLinks, sourceDocumentLinks, sourceUrl, onSourceDocumentLink, onInternalAnchorLink, activeArticleId, depth + 1) : null}
        </section>
      ))}
    </div>
  );
  const isHenLabel = (label: string) => /^第[0-9一二三四五六七八九十百千]+編\b/.test(label);
  const renderBrowseTree = (nodes: BrowseTreeNode[], depth = 0): JSX.Element => (
    <div className={depth === 0 ? 'space-y-3' : 'mt-2 space-y-2'}>
      {nodes.map((node) => (
        <details
          key={node.key}
          defaultOpen={!isHenLabel(node.label)}
          className={`rounded-2xl border bg-background ${depth > 0 ? 'border-dashed' : ''}`}
        >
          <summary className="cursor-pointer list-none rounded-2xl px-4 py-3 text-sm font-semibold marker:hidden hover:bg-accent">
            <div className="flex items-center justify-between gap-3">
              <span>{node.label}</span>
              <span className="text-xs font-medium text-muted-foreground">
                {node.docs.length + node.children.reduce((sum, child) => sum + child.docs.length, 0)}件
              </span>
            </div>
          </summary>
          <div className="space-y-3 px-3 pb-3">
            {node.docs.length > 0 ? (
              <div className="space-y-1">
                {node.docs.map((doc) => (
                  <button
                    key={doc.id}
                    type="button"
                    onClick={() => setBrowseDocId(doc.id)}
                    className={`w-full rounded-xl px-3 py-2 text-left text-sm transition ${
                      browseDocId === doc.id ? 'bg-primary text-primary-foreground' : 'hover:bg-accent'
                    }`}
                  >
                    <span className="block font-medium leading-snug">{doc.title}</span>
                    {doc.lawNumber ? <span className="block text-xs opacity-70">{doc.lawNumber}</span> : null}
                  </button>
                ))}
              </div>
            ) : null}
            {node.children.length > 0 ? (
              <div className={`rounded-2xl ${depth >= 0 ? 'border-l-2 border-border/70 pl-3' : ''}`}>
                {renderBrowseTree(node.children, depth + 1)}
              </div>
            ) : null}
          </div>
        </details>
      ))}
    </div>
  );

  const renderMinutesTopBar = (): JSX.Element => (
    <div className="border-b bg-[#173f36] px-6 py-5 text-white">
      <div className="flex flex-col gap-5 lg:flex-row lg:items-center lg:justify-between">
        <div>
          <button
            type="button"
            onClick={() => setMinutesPage('home')}
            className="text-left"
          >
            <h2 className="text-2xl font-semibold tracking-tight">会議録検索システム</h2>
          </button>
          <p className="mt-1 text-sm text-white/75">検索方法を選んでから、条件入力、検索結果、本文閲覧へ順に進みます。</p>
        </div>
        <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
          {([
            ['browse', '会議録の閲覧', BookOpen],
            ['keyword', '言葉から検索', Search],
            ['speaker', '発言者から検索', FileSearch],
            ['collection', '発言集作成', BookMarked],
          ] as const).map(([page, label, Icon]) => {
            const active = minutesPage === page || (page === 'collection' && minutesPage === 'collectionResults');
            return (
              <button
                key={page}
                type="button"
                onClick={() => goToMinutesSearchMethod(page)}
                className={`inline-flex min-w-36 items-center justify-center gap-2 rounded-2xl border px-4 py-3 text-sm font-semibold transition ${
                  active
                    ? 'border-white bg-white text-[#173f36] shadow-sm'
                    : 'border-white/20 bg-white/10 text-white hover:border-white/40 hover:bg-white/15'
                }`}
              >
                <Icon className="size-4" />
                {label}
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );

  const renderMinutesBackButton = (label = '検索方法へ戻る'): JSX.Element => (
    <button
      type="button"
      onClick={() => setMinutesPage('home')}
      className="inline-flex items-center gap-2 rounded-xl border bg-white px-3 py-2 text-sm font-semibold text-[#37564d] hover:bg-[#edf6f0]"
    >
      <ChevronLeft className="size-4" />
      {label}
    </button>
  );

  const renderMinutesSearchOptions = (compact = false): JSX.Element => (
    <div className={`rounded-2xl border bg-[#f8fbf8] ${compact ? 'p-4' : 'p-5'}`}>
      <p className="text-sm font-semibold text-[#173f36]">検索条件</p>
      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <label className="flex items-center gap-2 rounded-xl bg-white px-3 py-2 text-sm">
          <input type="radio" checked={minutesMatchMode === 'exact'} onChange={() => setMinutesMatchMode('exact')} />
          完全一致
        </label>
        <label className="flex items-center gap-2 rounded-xl bg-white px-3 py-2 text-sm">
          <input type="radio" checked={minutesMatchMode === 'related'} onChange={() => setMinutesMatchMode('related')} />
          関連語検索
        </label>
        <label className="flex items-center gap-2 rounded-xl bg-white px-3 py-2 text-sm">
          <input type="radio" checked={minutesOp === 'AND'} onChange={() => setMinutesOp('AND')} />
          AND
        </label>
        <label className="flex items-center gap-2 rounded-xl bg-white px-3 py-2 text-sm">
          <input type="radio" checked={minutesOp === 'OR'} onChange={() => setMinutesOp('OR')} />
          OR
        </label>
      </div>
      <label className="mt-3 flex items-center gap-2 rounded-xl bg-white px-3 py-2 text-sm">
        <input type="checkbox" checked={minutesIncludeReplies} onChange={(e) => setMinutesIncludeReplies(e.target.checked)} />
        質問・答弁など前後の関連発言を本文閲覧に表示
      </label>
      {renderMinutesLimitSelector()}
    </div>
  );

  const renderMinutesLimitSelector = (): JSX.Element => (
    <div className="mt-4 rounded-2xl border bg-white p-3">
      <p className="text-xs font-semibold text-[#173f36]">表示件数</p>
      <div className="mt-2 flex flex-wrap gap-2">
        {MINUTES_SEARCH_LIMIT_OPTIONS.map((option) => (
          <button
            key={String(option.value)}
            type="button"
            onClick={() => setMinutesLimit(option.value)}
            className={`rounded-full border px-3 py-1.5 text-xs font-semibold transition ${
              minutesLimit === option.value
                ? 'border-[#2f765e] bg-[#dff2e5] text-[#173f36]'
                : 'bg-[#fbfdfb] text-[#37564d] hover:border-[#79b28d]'
            }`}
          >
            {option.label}
          </button>
        ))}
      </div>
    </div>
  );

  const renderMinutesCommonFilters = (showKeyword: boolean, showSpeaker: boolean, onEnter?: () => void): JSX.Element => (
    <div className="grid gap-4 lg:grid-cols-2">
      {showKeyword ? (
        <label className="space-y-2 text-sm lg:col-span-2">
          <span className="font-semibold text-[#173f36]">検索語</span>
          <input
            className="h-12 w-full rounded-xl border bg-white px-4 text-base"
            placeholder="例: 観光、公共交通、学校給食"
            value={minutesQuery}
            onChange={(e) => setMinutesQuery(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') (onEnter ? onEnter() : void submitMinutesSearch()); }}
          />
        </label>
      ) : null}
      {showSpeaker ? (
        <label className="space-y-2 text-sm lg:col-span-2">
          <span className="font-semibold text-[#173f36]">発言者・役職</span>
          <input
            className="h-12 w-full rounded-xl border bg-white px-4 text-base"
            list="minutes-speakers"
            placeholder="氏名、議員番号、市長、課長など"
            value={minutesSpeaker}
            onChange={(e) => setMinutesSpeaker(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') (onEnter ? onEnter() : void submitMinutesSearch()); }}
          />
          <datalist id="minutes-speakers">
            {minutesSpeakers.slice(0, 300).map((speaker) => (
              <option key={`${speaker.displayName}-${speaker.title}-${speaker.role}`} value={speaker.displayName}>
                {speaker.title ? `${speaker.title} / ${minutesRoleLabel(speaker.role)}` : minutesRoleLabel(speaker.role)}
              </option>
            ))}
          </datalist>
        </label>
      ) : null}
      <label className="space-y-2 text-sm">
        <span className="font-semibold text-[#173f36]">発言区分</span>
        <select className="h-11 w-full rounded-xl border bg-white px-3 text-sm" value={minutesRole} onChange={(e) => setMinutesRole(e.target.value)}>
          <option value="all">すべて</option>
          <option value="questioner">質問者</option>
          <option value="answerer">答弁者</option>
          <option value="chair">議事進行</option>
          <option value="secretariat">事務局</option>
          <option value="report">報告</option>
          <option value="unknown">未分類</option>
          <optgroup label="執行部の所属・役職">
            {minutesExecutiveTitleFilters.map((item) => (
              <option key={item.title} value={`title:${item.title}`}>
                {item.title}{item.count != null ? `（${item.count.toLocaleString()}発言）` : ''}
              </option>
            ))}
          </optgroup>
        </select>
      </label>
      <label className="space-y-2 text-sm">
        <span className="font-semibold text-[#173f36]">会議種別</span>
        <select className="h-11 w-full rounded-xl border bg-white px-3 text-sm" value={minutesSection} onChange={(e) => { setMinutesSection(e.target.value); setMinutesMeetingId(null); }}>
          <option value="all">すべて</option>
          <option value="本会議">本会議</option>
          <option value="常任委員会">常任委員会</option>
          <option value="特別委員会">特別委員会</option>
        </select>
      </label>
      <label className="space-y-2 text-sm lg:col-span-2">
        <span className="font-semibold text-[#173f36]">会議名</span>
        <select
          className="h-11 w-full rounded-xl border bg-white px-3 text-sm"
          value={minutesMeetingId ?? ''}
          onChange={(e) => setMinutesMeetingId(e.target.value ? Number(e.target.value) : null)}
        >
          <option value="">すべての会議</option>
          {searchMinutesMeetingOptions.map((meeting) => (
            <option key={meeting.id} value={meeting.id}>
              {meeting.section} / {formatMinutesMeetingBrowseTitle(meeting)}
            </option>
          ))}
        </select>
      </label>
      <label className="space-y-2 text-sm">
        <span className="font-semibold text-[#173f36]">年</span>
        <select className="h-11 w-full rounded-xl border bg-white px-3 text-sm" value={minutesSearchYear} onChange={(e) => setMinutesSearchYearRange(e.target.value)}>
          <option value="">すべての年</option>
          {minutesBrowseFiscalYears.map((year) => (
            <option key={year} value={String(year)}>{calendarYearLabel(year)}</option>
          ))}
        </select>
      </label>
      <label className="space-y-2 text-sm">
        <span className="font-semibold text-[#173f36]">開始日</span>
        <input className="h-11 w-full rounded-xl border bg-white px-3 text-sm" type="date" value={minutesFromDate} onChange={(e) => { setMinutesSearchYear(''); setMinutesFromDate(e.target.value); }} />
      </label>
      <label className="space-y-2 text-sm">
        <span className="font-semibold text-[#173f36]">終了日</span>
        <input className="h-11 w-full rounded-xl border bg-white px-3 text-sm" type="date" value={minutesToDate} onChange={(e) => { setMinutesSearchYear(''); setMinutesToDate(e.target.value); }} />
      </label>
    </div>
  );

  const renderMinutesSearchActions = (): JSX.Element => (
    <div className="mt-6 flex flex-col gap-3 sm:flex-row">
      <button
        type="button"
        disabled={minutesSearching}
        onClick={() => void submitMinutesSearch()}
        className="inline-flex h-12 min-w-40 items-center justify-center rounded-xl bg-[#2f765e] px-6 text-sm font-semibold text-white shadow-sm disabled:opacity-60"
      >
        {minutesSearching ? '検索中…' : '検索'}
      </button>
      <button
        type="button"
        onClick={clearMinutesSearch}
        className="inline-flex h-12 min-w-36 items-center justify-center rounded-xl border bg-white px-5 text-sm font-semibold text-[#37564d] hover:bg-[#edf6f0]"
      >
        条件クリア
      </button>
      {minutesResults.length > 0 ? (
        <button
          type="button"
          onClick={() => setMinutesPage('results')}
          className="inline-flex h-12 min-w-36 items-center justify-center rounded-xl border bg-white px-5 text-sm font-semibold text-[#37564d] hover:bg-[#edf6f0]"
        >
          前回の結果へ
        </button>
      ) : null}
    </div>
  );

  const renderMinutesResultCard = (result: MinutesSearchResult): JSX.Element => {
    const expanded = minutesExpandedResultIds.has(result.id);
    return (
      <article
        key={result.id}
        className={`rounded-2xl border bg-white p-4 transition hover:border-[#79b28d] ${
          selectedMinutesResult?.id === result.id ? 'border-[#2f765e] ring-2 ring-[#2f765e]/10' : ''
        } [content-visibility:auto] [contain-intrinsic-size:0_12rem]`}
      >
        <button type="button" onClick={() => selectMinutesResult(result)} className="w-full text-left">
          <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
            <div>
              <p className="text-sm font-semibold text-[#2f765e]">{result.meetingDate || '日付なし'} / {result.section}</p>
              <h4 className="mt-1 text-lg font-semibold leading-snug">{result.meetingName || result.dayTitle}</h4>
              <p className="mt-2 text-sm text-muted-foreground">発言{result.order} / {result.speakerTitle} {result.speakerName} / p.{result.pageStart}-{result.pageEnd}</p>
            </div>
            <span className={`w-fit rounded-full border px-3 py-1 text-xs font-semibold ${minutesRoleClass(result.speakerRole)}`}>
              {minutesRoleLabel(result.speakerRole)}
            </span>
          </div>
          <p className="mt-4 line-clamp-3 text-sm leading-7 text-muted-foreground">
            {renderHighlightedText(result.snippet, minutesExactHighlightTerms(result), minutesRelatedHighlightTerms(result))}
          </p>
        </button>
        <div className="mt-4 flex flex-wrap items-center justify-between gap-2">
          <button
            type="button"
            onClick={() => toggleMinutesResultExpanded(result.id)}
            className="rounded-lg border bg-[#fbfdfb] px-3 py-1.5 text-xs font-semibold text-[#37564d] hover:bg-[#edf6f0]"
          >
            {expanded ? '発言本文を閉じる' : '発言本文を確認'}
          </button>
          <button
            type="button"
            onClick={() => selectMinutesResult(result)}
            className="rounded-lg bg-[#173f36] px-3 py-1.5 text-xs font-semibold text-white"
          >
            本文閲覧へ
          </button>
        </div>
        {expanded ? (
          <div className="mt-4 rounded-2xl border bg-[#f8fbf8] p-4 text-sm leading-7">
            {renderMinutesText(result.text, 'text-sm leading-7', minutesExactHighlightTerms(result), minutesRelatedHighlightTerms(result))}
          </div>
        ) : null}
      </article>
    );
  };

  const renderMinutesCollectionResultsPage = (): JSX.Element => (
    <div className="space-y-5 p-6">
      <div className="rounded-3xl border bg-white p-5">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div className="min-w-0">
            <button type="button" onClick={() => setMinutesPage('collection')} className="mb-3 inline-flex items-center gap-2 text-sm font-semibold text-[#2f765e] hover:underline">
              <ChevronLeft className="size-4" />
              発言集作成へ戻る
            </button>
            <p className="text-sm font-semibold text-[#2f765e]">発言集</p>
            <h3 className="mt-1 text-2xl font-semibold leading-tight">{minutesSpeaker || '発言者未指定'}の発言集</h3>
            <p className="mt-2 text-sm text-muted-foreground">
              {minutesTotal.toLocaleString()}件ヒット / 表示発言 {minutesCollectionItemCount.toLocaleString()}件
              {minutesIncludeReplies ? ' / 関連する質問・答弁を含む' : ' / 指定発言者のみ'}
              {minutesIncludeChair ? ' / 議事進行を含む' : ''}
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => setMinutesIncludeReplies((prev) => !prev)}
              className="inline-flex h-10 items-center justify-center rounded-xl border px-3 text-sm font-semibold hover:bg-[#edf6f0]"
            >
              {minutesIncludeReplies ? '関連発言を非表示' : '関連発言も表示'}
            </button>
            <button
              type="button"
              onClick={() => setMinutesIncludeChair((prev) => !prev)}
              className="inline-flex h-10 items-center justify-center rounded-xl border px-3 text-sm font-semibold hover:bg-[#edf6f0]"
            >
              {minutesIncludeChair ? '議事進行を非表示' : '議事進行も表示'}
            </button>
            <button
              type="button"
              onClick={() => window.print()}
              className="inline-flex h-10 items-center justify-center rounded-xl bg-[#173f36] px-4 text-sm font-semibold text-white"
            >
              印刷
            </button>
          </div>
        </div>
      </div>

      <div className="rounded-3xl border bg-white p-5 sm:p-8">
        {minutesSearching ? (
          <p className="text-sm text-muted-foreground">発言集を作成中です…</p>
        ) : minutesResults.length === 0 ? (
          <p className="text-sm text-muted-foreground">発言が見つかりませんでした。発言者や期間を変更してください。</p>
        ) : minutesCollectionGroups.length === 0 ? (
          <p className="text-sm text-muted-foreground">表示できる発言がありません。議事進行のみの場合は発言集から除外されます。</p>
        ) : (
          <div className="mx-auto max-w-6xl">
            {minutesCollectionGroups.map((group) => (
              <section key={group.dayId} className="border-b border-dashed py-6 first:pt-0 last:border-b-0 last:pb-0">
                <div className="mb-5">
                  <p className="text-sm font-semibold text-[#2f765e]">
                    {calendarYearLabelFromDate(group.meetingDate)} / {group.meetingDate || '日付なし'} / {group.section}
                  </p>
                  <h4 className="mt-1 text-xl font-semibold leading-tight">{group.meetingName || group.dayTitle}</h4>
                </div>
                <div className="space-y-5">
                  {group.items.map((item) => (
                    <article
                      key={`${group.dayId}-${item.id}`}
                      className={`min-w-0 border-l-4 py-1 pl-4 ${
                        item.isTargetSpeaker ? 'border-[#2f765e]' : 'border-[#c7ddd3]'
                      } [content-visibility:auto] [contain-intrinsic-size:0_12rem]`}
                    >
                      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                        <div className="min-w-0">
                          <p className="min-w-0 break-words text-base font-semibold [overflow-wrap:anywhere]">
                            {item.speakerTitle} {item.speakerName}
                          </p>
                          <p className="mt-0.5 text-xs text-muted-foreground">発言{item.order} / p.{item.pageStart}-{item.pageEnd}</p>
                        </div>
                        <span className={`w-fit rounded-full border px-2 py-0.5 text-xs ${minutesRoleClass(item.speakerRole)}`}>
                          {minutesRoleLabel(item.speakerRole)}
                        </span>
                      </div>
                      {renderMinutesText(
                        item.text,
                        'mt-2 text-[15px] leading-8',
                        item.isHit ? minutesExactHighlightTerms(item) : [],
                        item.isHit ? minutesRelatedHighlightTerms(item) : [],
                      )}
                    </article>
                  ))}
                </div>
              </section>
            ))}
          </div>
        )}
      </div>
    </div>
  );

  const renderMinutesResultsPage = (): JSX.Element => (
    <div className="space-y-5 p-6">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={() => setMinutesPage(minutesSearchReturnPage)}
            className="inline-flex items-center gap-2 rounded-xl border bg-white px-3 py-2 text-sm font-semibold text-[#37564d] hover:bg-[#edf6f0]"
          >
            <ChevronLeft className="size-4" />
            検索条件へ戻る
          </button>
          <div>
            <p className="text-sm font-semibold text-[#2f765e]">検索結果</p>
            <h3 className="text-2xl font-semibold">{minutesTotal.toLocaleString()}件</h3>
            {minutesResults.length > 0 && minutesVisibleResultCount < sortedMinutesResults.length ? (
              <p className="mt-1 text-xs text-muted-foreground">
                表示中 {Math.min(minutesVisibleResultCount, sortedMinutesResults.length).toLocaleString()} / {sortedMinutesResults.length.toLocaleString()}件
              </p>
            ) : null}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="grid grid-cols-3 rounded-2xl border bg-[#f5f8f5] p-1 text-xs font-semibold">
            {([
              ['utterance', '発言'],
              ['meeting', '会議'],
              ['table', '表'],
            ] as const).map(([mode, label]) => (
              <button
                key={mode}
                type="button"
                onClick={() => setMinutesResultMode(mode)}
                className={`rounded-xl px-4 py-2 transition ${minutesResultMode === mode ? 'bg-[#173f36] text-white shadow-sm' : 'text-[#4d685f] hover:bg-white'}`}
              >
                {label}
              </button>
            ))}
          </div>
          <select
            className="h-10 rounded-xl border bg-white px-3 text-sm"
            value={minutesSortOrder}
            onChange={(e) => setMinutesSortOrder(e.target.value as 'new' | 'old')}
          >
            <option value="new">新しい順</option>
            <option value="old">古い順</option>
          </select>
        </div>
      </div>

      <div className="rounded-3xl border bg-[#f8fbf8] p-5">
        {minutesSearching ? (
          <p className="rounded-2xl border bg-white p-6 text-muted-foreground">検索中…</p>
        ) : minutesResults.length === 0 ? (
          <p className="rounded-2xl border bg-white p-6 text-muted-foreground">検索結果がありません。検索方法へ戻り、条件を変更してください。</p>
        ) : minutesResultMode === 'meeting' ? (
          <div className="space-y-3">
            {meetingGroupedMinutesResults.map((group) => (
              <button
                key={group.dayId}
                type="button"
                onClick={() => selectMinutesResult(group.first)}
                className="w-full rounded-2xl border bg-white px-4 py-4 text-left transition hover:border-[#79b28d] hover:bg-[#edf7ef]"
              >
                <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                  <div className="min-w-0">
                    <p className="text-sm font-semibold text-[#2f765e]">
                      {calendarYearLabelFromDate(group.meetingDate)} / {group.meetingDate || '日付なし'} / {group.section}
                    </p>
                    <h4 className="mt-1 text-lg font-semibold leading-snug text-blue-700 underline-offset-2 hover:underline">{group.title}</h4>
                  </div>
                  <span className="w-fit rounded-full bg-[#e3f0e8] px-3 py-1 text-sm font-semibold text-[#2f765e]">
                    {group.count.toLocaleString()}件
                    <span className="ml-2 text-xs font-medium text-muted-foreground">発言者{group.speakers.size}人</span>
                  </span>
                </div>
              </button>
            ))}
          </div>
        ) : minutesResultMode === 'table' ? (
          <div className="rounded-2xl border bg-white p-6 text-sm text-muted-foreground">
            表は本文閲覧画面の「資料」タブで、該当会議日の抽出表として確認できます。
          </div>
        ) : (
          <div className="space-y-5">
            {groupedVisibleMinutesResults.map((group) => (
              <section key={group.dayId} className="overflow-hidden rounded-3xl border bg-white shadow-sm">
                <div className="border-b bg-[#e8f3ed] px-5 py-4">
                  <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                    <div className="min-w-0">
                      <p className="text-sm font-semibold text-[#2f765e]">
                        {calendarYearLabelFromDate(group.meetingDate)} / {group.meetingDate || '日付なし'} / {group.section}
                      </p>
                      <h4 className="mt-1 text-lg font-semibold leading-snug text-[#173f36]">{group.title}</h4>
                    </div>
                    <span className="w-fit rounded-full bg-white px-3 py-1 text-sm font-semibold text-[#2f765e]">
                      {group.items.length.toLocaleString()}件
                    </span>
                  </div>
                </div>
                <div className="space-y-3 p-4">
                  {group.items.map(renderMinutesResultCard)}
                </div>
              </section>
            ))}
            {minutesVisibleResultCount < sortedMinutesResults.length ? (
              <div className="flex justify-center">
                <button
                  type="button"
                  onClick={() => setMinutesVisibleResultCount((current) => Math.min(current + MINUTES_RENDER_BATCH_SIZE, sortedMinutesResults.length))}
                  className="rounded-2xl border bg-white px-6 py-3 text-sm font-semibold text-[#37564d] shadow-sm hover:border-[#79b28d] hover:bg-[#edf6f0]"
                >
                  さらに{Math.min(MINUTES_RENDER_BATCH_SIZE, sortedMinutesResults.length - minutesVisibleResultCount).toLocaleString()}件表示
                </button>
              </div>
            ) : null}
          </div>
        )}
      </div>
    </div>
  );

  const renderMinutesDetailPage = (): JSX.Element => (
    <div className="space-y-5 p-6">
      {!selectedMinutesResult ? (
        <div className="rounded-3xl border bg-white p-8 text-center">
          <p className="font-semibold text-[#173f36]">発言が選択されていません。</p>
          <button type="button" onClick={() => setMinutesPage('results')} className="mt-4 rounded-xl border px-4 py-2 text-sm font-semibold">検索結果へ戻る</button>
        </div>
      ) : (
        <>
          <div className="rounded-3xl border bg-white p-5">
            <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
              <div className="min-w-0">
                <button type="button" onClick={() => setMinutesPage('results')} className="mb-3 inline-flex items-center gap-2 text-sm font-semibold text-[#2f765e] hover:underline">
                  <ChevronLeft className="size-4" />
                  検索結果へ戻る
                </button>
                <p className="text-sm font-semibold text-[#2f765e]">{selectedMinutesResult.section} / {selectedMinutesResult.meetingDate || '日付なし'}</p>
                <h3 className="mt-1 text-2xl font-semibold leading-tight">{selectedMinutesResult.meetingName || selectedMinutesResult.dayTitle}</h3>
                <p className="mt-2 text-sm text-muted-foreground">
                  発言{selectedMinutesResult.order} / {selectedMinutesResult.speakerTitle} {selectedMinutesResult.speakerName} / p.{selectedMinutesResult.pageStart}
                </p>
              </div>
              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  disabled={selectedMinutesUtteranceIndex <= 0}
                  onClick={() => moveSelectedMinutesUtterance(-1)}
                  className="inline-flex h-10 items-center justify-center rounded-xl border px-3 text-sm font-semibold hover:bg-[#edf6f0] disabled:opacity-40"
                >
                  前の発言
                </button>
                <button
                  type="button"
                  disabled={selectedMinutesUtteranceIndex < 0 || selectedMinutesUtteranceIndex >= currentDayUtterances.length - 1}
                  onClick={() => moveSelectedMinutesUtterance(1)}
                  className="inline-flex h-10 items-center justify-center rounded-xl border px-3 text-sm font-semibold hover:bg-[#edf6f0] disabled:opacity-40"
                >
                  次の発言
                </button>
                <button
                  type="button"
                  onClick={() => {
                    resetMinutesSearchState('meeting');
                    setMinutesSpeaker(selectedMinutesResult.speakerName);
                    setMinutesRole(selectedMinutesResult.speakerRole);
                    setMinutesPage('speaker');
                  }}
                  className="inline-flex h-10 items-center justify-center rounded-xl border px-3 text-sm font-semibold hover:bg-[#edf6f0]"
                >
                  この発言者で検索
                </button>
                <a
                  href={selectedMinutesResult.pdfUrl}
                  rel="noreferrer"
                  target="_blank"
                  className="inline-flex h-10 items-center justify-center rounded-xl bg-[#173f36] px-4 text-sm font-semibold text-white"
                >
                  PDF原文
                </a>
              </div>
            </div>
          </div>

          <div className="grid min-w-0 gap-5 xl:grid-cols-[minmax(0,1fr)_22rem]">
            <div className="min-w-0 overflow-hidden rounded-3xl border bg-white">
              <div className="sticky top-0 z-10 rounded-t-3xl border-b bg-white/95 p-4 backdrop-blur">
                <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
                  <div>
                    <p className="text-sm font-semibold text-[#173f36]">会議録閲覧</p>
                    <p className="text-xs text-muted-foreground">本文を広く表示し、発言単位・一覧・会議録・目次・資料を切り替えます。</p>
                  </div>
                  <div className="grid grid-cols-5 rounded-xl border bg-[#f5f8f5] p-1 text-xs font-semibold">
                    {([
                      ['unit', '発言単位'],
                      ['list', '発言一覧'],
                      ['full', '会議録'],
                      ['toc', '目次'],
                      ['materials', '資料'],
                    ] as const).map(([mode, label]) => (
                      <button
                        key={mode}
                        type="button"
                        onClick={() => setMinutesReaderMode(mode)}
                        className={`rounded-lg px-2 py-1.5 transition ${minutesReaderMode === mode ? 'bg-[#173f36] text-white' : 'text-[#4d685f] hover:bg-white'}`}
                      >
                        {label}
                      </button>
                    ))}
                  </div>
                </div>
              </div>
              <div ref={minutesReaderScrollRef} className="max-h-[72vh] min-w-0 overflow-auto p-4 [overflow-anchor:none] sm:p-5">
                {minutesReaderMode === 'unit' ? (
                  selectedMinutesUnitItems.map((item) => (
                    <article
                      id={`minutes-utterance-${item.id}`}
                      key={item.id}
                      className={`mb-4 min-w-0 rounded-2xl border p-4 last:mb-0 sm:p-5 ${
                        item.id === selectedMinutesResult.id ? 'border-[#2f765e] bg-[#edf7ef]' : 'bg-[#fbfdfb]'
                      } ${
                        item.id === selectedMinutesResult.id ? '' : '[content-visibility:auto] [contain-intrinsic-size:0_14rem]'
                      }`}
                    >
                      <div className="flex items-start justify-between gap-3">
                        <div>
                          <p className="text-base font-semibold">{item.speakerTitle} {item.speakerName}</p>
                          <p className="text-xs text-muted-foreground">発言番号 {item.order} / p.{item.pageStart}-{item.pageEnd}</p>
                        </div>
                        <span className={`rounded-full border px-2 py-0.5 text-xs ${minutesRoleClass(item.speakerRole)}`}>
                          {minutesRoleLabel(item.speakerRole)}
                        </span>
                      </div>
                      {renderMinutesText(item.text, 'mt-4 text-base leading-8', minutesExactHighlightTerms(item), minutesRelatedHighlightTerms(item))}
                    </article>
                  ))
                ) : null}
                {minutesReaderMode === 'list' ? (
                  currentDayUtterances.length > 0 ? (
                    <div className="flex flex-col gap-2">
                      {currentDayUtterances.map((item) => (
                        <button
                          id={`minutes-utterance-${item.id}`}
                          key={item.id}
                          type="button"
                          onClick={() => {
                            if (!selectedMinutesResult || !minutesDayDetail) return;
                            const index = minutesDayDetail.utterances.findIndex((u) => u.id === item.id);
                            setSelectedMinutesResult({
                              ...selectedMinutesResult,
                              id: item.id,
                              order: item.order,
                              speakerName: item.speakerName,
                              speakerTitle: item.speakerTitle,
                              speakerRole: item.speakerRole,
                              speechType: item.speechType,
                              text: item.text,
                              pageStart: item.pageStart,
                              pageEnd: item.pageEnd,
                              snippet: item.text.slice(0, 180),
                              exchange: minutesDayDetail.utterances.slice(Math.max(0, index - 2), index + 5),
                              highlightTerms: selectedDayMinutesHitTermMap.get(item.id)?.highlightTerms || selectedMinutesResult.highlightTerms || [],
                              relatedHighlightTerms: selectedDayMinutesHitTermMap.get(item.id)?.relatedHighlightTerms || selectedMinutesResult.relatedHighlightTerms || [],
                            });
                            setMinutesReaderMode('unit');
                          }}
                          className={`block w-full rounded-xl border px-4 py-3 text-left text-sm hover:border-[#79b28d] ${
                            item.id === selectedMinutesResult.id
                              ? 'border-[#2f765e] bg-[#edf7ef] ring-2 ring-[#2f765e]/10'
                              : selectedDayMinutesHitIds.has(item.id)
                                ? 'border-[#79b28d] bg-[#f0faf3]'
                                : 'bg-[#fbfdfb]'
                          } ${
                            item.id === selectedMinutesResult.id ? '' : '[content-visibility:auto] [contain-intrinsic-size:0_5rem]'
                          }`}
                        >
                          <div className="flex items-center justify-between gap-2">
                            <span className="font-semibold">{item.speakerTitle} {item.speakerName}</span>
                            <span className={`rounded-full border px-2 py-0.5 text-xs ${minutesRoleClass(item.speakerRole)}`}>{minutesRoleLabel(item.speakerRole)}</span>
                          </div>
                          <p className="mt-1 truncate text-xs leading-5 text-muted-foreground">
                            {renderHighlightedText(previewText(item.text), minutesExactHighlightTerms(item), minutesRelatedHighlightTerms(item))}
                          </p>
                        </button>
                      ))}
                    </div>
                  ) : <p className="text-sm text-muted-foreground">発言一覧を読み込み中です。</p>
                ) : null}
                {minutesReaderMode === 'full' ? (
                  <div className="space-y-6">
                    {currentDayContentItems.map((contentItem) => {
                      if (contentItem.type === 'table') {
                        return renderMinutesTableCard(contentItem.table);
                      }
                      const item = contentItem.utterance;
                      const isHit = selectedDayMinutesHitIds.has(item.id);
                      return (
                        <article
                          id={`minutes-utterance-${item.id}`}
                          key={`utterance-${item.id}`}
                          className={`rounded-2xl border p-4 ${
                            item.id === selectedMinutesResult.id
                              ? 'border-[#2f765e] bg-[#edf7ef] ring-2 ring-[#2f765e]/10'
                              : isHit
                                ? 'border-[#79b28d] bg-[#f0faf3]'
                                : 'border-transparent'
                          } ${
                            item.id === selectedMinutesResult.id ? '' : '[content-visibility:auto] [contain-intrinsic-size:0_16rem]'
                          }`}
                        >
                          <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                            <h4 className="min-w-0 break-words text-base font-semibold [overflow-wrap:anywhere]">{item.speakerTitle} {item.speakerName}</h4>
                            {isHit ? <span className="w-fit rounded-full bg-[#dff2e5] px-2 py-0.5 text-xs font-semibold text-[#2f765e]">検索ヒット</span> : null}
                          </div>
                          {renderMinutesText(item.text, 'mt-3 text-base leading-8', minutesExactHighlightTerms(item), minutesRelatedHighlightTerms(item))}
                        </article>
                      );
                    })}
                  </div>
                ) : null}
                {minutesReaderMode === 'toc' ? (
                  <div className="grid gap-3 md:grid-cols-2">
                    {selectedMinutesSpeakerNames.length > 0 ? selectedMinutesSpeakerNames.map(([name, count]) => (
                      <button
                        key={name}
                        type="button"
                        onClick={() => {
                          const speakerName = name.replace(/^[^\s]+\s+/, '');
                          resetMinutesSearchState('meeting');
                          setMinutesSpeaker(speakerName);
                          setMinutesPage('speaker');
                        }}
                        className="rounded-xl border bg-[#fbfdfb] px-4 py-3 text-left text-sm hover:border-[#79b28d]"
                      >
                        <p className="font-semibold">{name}</p>
                        <p className="mt-1 text-xs text-muted-foreground">{count}発言</p>
                      </button>
                    )) : <p className="text-sm text-muted-foreground">目次情報を生成できませんでした。</p>}
                  </div>
                ) : null}
                {minutesReaderMode === 'materials' ? (
                  minutesDayDetail?.tables.length ? (
                    <div className="space-y-4">
                      {minutesDayDetail.tables.map((table) => (
                        renderMinutesTableCard(table)
                      ))}
                    </div>
                  ) : <p className="text-sm text-muted-foreground">この会議日に資料・表はありません。</p>
                ) : null}
              </div>
            </div>

            <aside className="rounded-3xl border bg-white p-4">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <p className="text-sm font-semibold text-[#173f36]">検索ヒット箇所</p>
                  <p className="mt-1 text-xs text-muted-foreground">該当発言を選択して本文位置を切り替えます。</p>
                </div>
                <span className="w-fit rounded-full bg-[#e3f0e8] px-3 py-1 text-xs font-semibold text-[#2f765e]">
                  {selectedDayMinutesHits.length.toLocaleString()}件
                </span>
              </div>
              <div className="mt-4 flex max-h-[64vh] flex-col gap-2 overflow-auto pr-1">
                {selectedDayMinutesHits.map((hit) => (
                  <button
                    key={hit.id}
                    type="button"
                    onClick={() => {
                      selectMinutesHit(hit);
                    }}
                    className={`block w-full rounded-2xl border px-3 py-2 text-left transition hover:border-[#79b28d] ${
                      hit.id === selectedMinutesResult.id ? 'border-[#2f765e] bg-[#edf7ef]' : 'bg-[#fbfdfb]'
                    }`}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <p className="min-w-0 truncate text-sm font-semibold text-[#2f765e]">発言{hit.order} / p.{hit.pageStart}-{hit.pageEnd}</p>
                      <span className={`shrink-0 rounded-full border px-2 py-0.5 text-[11px] ${minutesRoleClass(hit.speakerRole)}`}>
                        {minutesRoleLabel(hit.speakerRole)}
                      </span>
                    </div>
                    <p className="mt-1 truncate text-xs leading-5 text-muted-foreground">
                      <span className="font-semibold text-foreground">{hit.speakerTitle} {hit.speakerName}</span>
                      <span className="mx-1 text-muted-foreground">/</span>
                      {renderHighlightedText(firstContentLine(hit.snippet || hit.text), minutesExactHighlightTerms(hit), minutesRelatedHighlightTerms(hit))}
                    </p>
                  </button>
                ))}
              </div>
            </aside>
          </div>
        </>
      )}
    </div>
  );

  const renderMinutesMeetingDetailPage = (): JSX.Element => {
    const detail = selectedMinutesMeetingDetail;
    const totalDays = detail?.days.length ?? 0;
    const totalUtterances = detail?.days.reduce((sum, day) => sum + day.utterances.length, 0) ?? 0;
    const totalTables = detail?.days.reduce((sum, day) => sum + day.tables.length, 0) ?? 0;
    const selectedDay = detail?.days.find((day) => day.id === selectedMinutesMeetingDayId) || detail?.days[0] || null;
    const selectedDayContentItems = selectedDay?.contentItems?.length
      ? selectedDay.contentItems
      : selectedDay?.utterances.map((utterance) => ({ type: 'utterance' as const, utterance })) || [];
    return (
      <div className="space-y-5 p-6">
        <div className="rounded-3xl border bg-white p-5">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
            <div>
              <button type="button" onClick={() => setMinutesPage('browse')} className="mb-3 inline-flex items-center gap-2 text-sm font-semibold text-[#2f765e] hover:underline">
                <ChevronLeft className="size-4" />
                会議録一覧へ戻る
              </button>
              <p className="text-sm font-semibold text-[#2f765e]">{detail?.section || '会議録'}</p>
              <h3 className="mt-1 text-2xl font-semibold leading-tight">{detail ? formatMinutesMeetingBrowseTitle(detail) : '会議録を読み込み中'}</h3>
              <p className="mt-2 text-sm text-muted-foreground">
                {minutesMeetingDetailLoading ? '会議録を読み込んでいます。' : `${totalDays}日程 / ${totalUtterances.toLocaleString()}発言 / 表${totalTables.toLocaleString()}件`}
                {selectedDay ? ` / 表示中: ${selectedDay.meetingDate || selectedDay.title || `日程${selectedDay.id}`}` : ''}
              </p>
            </div>
            {detail?.sourceUrl ? (
              <a
                href={detail.sourceUrl}
                rel="noreferrer"
                target="_blank"
                className="inline-flex h-10 items-center justify-center rounded-xl bg-[#173f36] px-4 text-sm font-semibold text-white"
              >
                元ページ
              </a>
            ) : null}
          </div>
        </div>

        {minutesMeetingDetailLoading ? (
          <div className="rounded-3xl border bg-white p-8 text-center text-muted-foreground">会議録を読み込み中です…</div>
        ) : !detail ? (
          <div className="rounded-3xl border bg-white p-8 text-center text-muted-foreground">会議録を取得できませんでした。</div>
        ) : (
          <div className="grid min-w-0 gap-5 xl:grid-cols-[minmax(0,1fr)_20rem]">
            <div className="min-w-0 overflow-hidden rounded-3xl border bg-white">
              <div className="sticky top-0 z-10 rounded-t-3xl border-b bg-white/95 p-4 backdrop-blur">
                <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
                  <div>
                    <p className="text-sm font-semibold text-[#173f36]">会議録全文</p>
                    <p className="text-xs text-muted-foreground">日程を選択して、1日単位で会議録全文を閲覧します。</p>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {detail.days.map((day) => (
                      <button
                        key={day.id}
                        type="button"
                        onClick={() => setSelectedMinutesMeetingDayId(day.id)}
                        className={`rounded-lg border px-3 py-1.5 text-xs font-semibold ${
                          selectedDay?.id === day.id ? 'border-[#173f36] bg-[#173f36] text-white' : 'bg-[#f5f8f5] text-[#37564d] hover:bg-[#edf6f0]'
                        }`}
                      >
                        {day.meetingDate || day.title || `日程${day.id}`}
                      </button>
                    ))}
                  </div>
                </div>
              </div>
              <div className="max-h-[76vh] min-w-0 overflow-auto p-4 sm:p-6">
                {!selectedDay ? (
                  <p className="text-sm text-muted-foreground">表示できる日程がありません。</p>
                ) : (
                  <section key={selectedDay.id} className="pb-2">
                    <div className="mb-5 flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
                      <div>
                        <p className="text-sm font-semibold text-[#2f765e]">{selectedDay.meetingDate || '日付なし'}</p>
                        <h4 className="mt-1 text-xl font-semibold">{selectedDay.title || detail.meetingName}</h4>
                        <p className="mt-1 text-sm text-muted-foreground">{selectedDay.utterances.length.toLocaleString()}発言 / p.{selectedDay.pageCount || '-'}</p>
                      </div>
                      {selectedDay.pdfUrl ? (
                        <a href={selectedDay.pdfUrl} target="_blank" rel="noreferrer" className="rounded-xl border px-3 py-2 text-sm font-semibold hover:bg-[#edf6f0]">
                          PDF原文
                        </a>
                      ) : null}
                    </div>
                    <div className="space-y-7">
                      {selectedDayContentItems.map((contentItem) => {
                        if (contentItem.type === 'table') {
                          return renderMinutesTableCard(contentItem.table);
                        }
                        const item = contentItem.utterance;
                        return (
                          <article key={`utterance-${item.id}`} className="min-w-0 border-b border-dashed pb-5 last:border-b-0 [content-visibility:auto] [contain-intrinsic-size:0_16rem]">
                            <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                              <h5 className="min-w-0 break-words text-base font-semibold [overflow-wrap:anywhere]">{item.speakerTitle} {item.speakerName}</h5>
                              <span className={`w-fit rounded-full border px-2 py-0.5 text-xs ${minutesRoleClass(item.speakerRole)}`}>{minutesRoleLabel(item.speakerRole)}</span>
                            </div>
                            <p className="mt-1 text-xs text-muted-foreground">p.{item.pageStart}-{item.pageEnd}</p>
                            {renderMinutesText(item.text)}
                          </article>
                        );
                      })}
                    </div>
                  </section>
                )}
              </div>
            </div>

            <aside className="space-y-4">
              <div className="rounded-3xl border bg-white p-4">
                <p className="text-sm font-semibold text-[#173f36]">会議内情報</p>
                <dl className="mt-3 space-y-2 text-sm">
                  <div className="flex justify-between gap-3"><dt className="text-muted-foreground">会議種別</dt><dd>{detail.section}</dd></div>
                  <div className="flex justify-between gap-3"><dt className="text-muted-foreground">日程</dt><dd>{totalDays}日</dd></div>
                  <div className="flex justify-between gap-3"><dt className="text-muted-foreground">発言</dt><dd>{totalUtterances.toLocaleString()}件</dd></div>
                  <div className="flex justify-between gap-3"><dt className="text-muted-foreground">表</dt><dd>{totalTables.toLocaleString()}件</dd></div>
                </dl>
              </div>
              <div className="rounded-3xl border bg-white p-4">
                <p className="text-sm font-semibold text-[#173f36]">日程</p>
                <div className="mt-3 space-y-2">
                  {detail.days.map((day) => (
                    <button
                      key={day.id}
                      type="button"
                      onClick={() => setSelectedMinutesMeetingDayId(day.id)}
                      className={`block w-full rounded-xl border px-3 py-2 text-left text-sm hover:border-[#79b28d] ${
                        selectedDay?.id === day.id ? 'border-[#2f765e] bg-[#edf7ef]' : 'bg-[#fbfdfb]'
                      }`}
                    >
                      <span className="font-semibold">{day.meetingDate || '日付なし'}</span>
                      <span className="mt-1 block text-xs text-muted-foreground">{day.utterances.length.toLocaleString()}発言 / 表{day.tables.length}</span>
                    </button>
                  ))}
                </div>
              </div>
            </aside>
          </div>
        )}
      </div>
    );
  };

  const renderMinutesWorkspace = (): JSX.Element => {
    if (minutesPage === 'collectionResults') return <section className="overflow-hidden rounded-[2rem] border bg-[#eef5f0] shadow-sm">{renderMinutesTopBar()}{renderMinutesCollectionResultsPage()}</section>;
    if (minutesPage === 'results') return <section className="overflow-hidden rounded-[2rem] border bg-[#eef5f0] shadow-sm">{renderMinutesTopBar()}{renderMinutesResultsPage()}</section>;
    if (minutesPage === 'detail') return <section className="overflow-hidden rounded-[2rem] border bg-[#eef5f0] shadow-sm">{renderMinutesTopBar()}{renderMinutesDetailPage()}</section>;
    if (minutesPage === 'meetingDetail') return <section className="overflow-hidden rounded-[2rem] border bg-[#eef5f0] shadow-sm">{renderMinutesTopBar()}{renderMinutesMeetingDetailPage()}</section>;

    return (
      <section className="overflow-hidden rounded-[2rem] border bg-[#eef5f0] shadow-sm">
        {renderMinutesTopBar()}
        <div className="p-6">
          {minutesPage === 'home' ? (
            <div className="space-y-6">
              <div className="grid gap-4 lg:grid-cols-4">
                {[
                  { page: 'browse' as const, title: '会議録の閲覧', desc: '年、会議名、会議種別から会議録を閲覧します。', icon: BookOpen },
                  { page: 'keyword' as const, title: '言葉から検索', desc: '調べたい言葉を指定して発言本文を検索します。', icon: Search },
                  { page: 'speaker' as const, title: '発言者から検索', desc: '発言者の氏名や役職から発言を探します。', icon: FileSearch },
                  { page: 'collection' as const, title: '発言集作成', desc: '指定した発言者の発言だけを抽出し、関連する質問や答弁も必要に応じて表示します。', icon: BookMarked },
                ].map((item) => {
                  const Icon = item.icon;
                  return (
                    <button
                      key={item.page}
                      type="button"
                      onClick={() => goToMinutesSearchMethod(item.page)}
                      className="group min-h-44 rounded-3xl border bg-white p-5 text-left shadow-sm transition hover:-translate-y-0.5 hover:border-[#79b28d] hover:shadow-md"
                    >
                      <div className="mb-4 inline-flex size-12 items-center justify-center rounded-2xl bg-[#e3f0e8] text-[#173f36] group-hover:bg-[#173f36] group-hover:text-white">
                        <Icon className="size-6" />
                      </div>
                      <h3 className="text-xl font-semibold text-[#173f36]">{item.title}</h3>
                      <p className="mt-3 text-sm leading-6 text-muted-foreground">{item.desc}</p>
                    </button>
                  );
                })}
              </div>

              <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_22rem]">
                <div className="rounded-3xl border bg-white p-5">
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <p className="text-sm font-semibold text-[#173f36]">データ更新</p>
                      <p className="mt-1 text-xs text-muted-foreground">直近1年分の会議録PDFから発言・表を抽出します。</p>
                    </div>
                    <button
                      type="button"
                      disabled={minutesSyncing || Boolean(runningMinutesRun)}
                      onClick={() => void triggerMinutesSync()}
                      className="rounded-xl border px-4 py-2 text-sm font-semibold text-[#2f765e] hover:bg-[#edf6f0] disabled:opacity-60"
                    >
                      {minutesSyncing || runningMinutesRun ? '同期中' : '直近1年同期'}
                    </button>
                  </div>
                  <ProgressMeter title="会議録同期の進捗" run={runningMinutesRun} />
                  {minutesStatus.latestRun?.finishedAt ? <p className="mt-3 text-xs text-muted-foreground">最終同期: {formatDateTime(minutesStatus.latestRun.finishedAt)}</p> : null}
                </div>
                <div className="rounded-3xl border bg-white p-5">
                  <div className="flex items-center justify-between gap-3">
                    <p className="text-sm font-semibold text-[#173f36]">検索履歴</p>
                    {minutesHistory.length > 0 ? (
                      <button type="button" onClick={() => setMinutesPage('history')} className="text-xs font-semibold text-[#2f765e] hover:underline">一覧</button>
                    ) : null}
                  </div>
                  {minutesHistory.length > 0 ? (
                    <button type="button" onClick={() => applyMinutesHistory(minutesHistory[0])} className="mt-3 w-full rounded-2xl border bg-[#fbfdfb] px-3 py-3 text-left text-sm hover:border-[#79b28d]">
                      <p className="font-semibold">{minutesHistory[0].label}</p>
                      <p className="mt-1 text-xs text-muted-foreground">{formatDateTime(minutesHistory[0].createdAt)} / {minutesHistory[0].matchMode === 'exact' ? '完全一致' : '関連語'} / {minutesHistory[0].op}</p>
                    </button>
                  ) : (
                    <p className="mt-3 text-sm text-muted-foreground">検索履歴はまだありません。</p>
                  )}
                </div>
              </div>
            </div>
          ) : null}

          {minutesPage === 'keyword' ? (
            <div className="mx-auto max-w-5xl space-y-5">
              {renderMinutesBackButton()}
              <div className="rounded-3xl border bg-white p-6">
                <h3 className="text-2xl font-semibold text-[#173f36]">言葉から検索</h3>
                <p className="mt-2 text-sm text-muted-foreground">調べたい語句を入力し、完全一致または関連語検索を選んでください。</p>
                <div className="mt-6 space-y-5">
                  {renderMinutesCommonFilters(true, false)}
                  {renderMinutesSearchOptions()}
                  {renderMinutesSearchActions()}
                </div>
              </div>
            </div>
          ) : null}

          {minutesPage === 'speaker' ? (
            <div className="mx-auto max-w-6xl space-y-5">
              {renderMinutesBackButton()}
              <div className="overflow-hidden rounded-3xl border bg-white shadow-sm">
                <div className="border-b bg-[#5f8f8f] px-5 py-4 text-white">
                  <h3 className="text-center text-xl font-semibold">質問者や答弁者から会議録を探します。</h3>
                </div>

                <div className="border-b bg-[#f3f7f5] p-5">
                  <p className="text-sm font-semibold text-[#173f36]">発言者</p>
                  <div className="mt-4 grid gap-4 lg:grid-cols-[12rem_minmax(0,1fr)] lg:items-end">
                    <label className="space-y-2 text-sm">
                      <span className="font-semibold text-[#173f36]">発言区分</span>
                      <select
                        className="h-11 w-full rounded-xl border bg-white px-3 text-sm"
                        value={minutesRole}
                        onChange={(e) => {
                          setMinutesRole(e.target.value);
                          setMinutesSpeaker('');
                        }}
                      >
                        <option value="all">すべて</option>
                        <option value="questioner">質問者</option>
                        <option value="answerer">答弁者</option>
                        <option value="chair">議事進行</option>
                        <option value="secretariat">事務局</option>
                        <option value="report">報告</option>
                        <option value="unknown">未分類</option>
                        <optgroup label="執行部の所属・役職">
                          {minutesExecutiveTitleFilters.map((item) => (
                            <option key={item.title} value={`title:${item.title}`}>
                              {item.title}{item.count != null ? `（${item.count.toLocaleString()}発言）` : ''}
                            </option>
                          ))}
                        </optgroup>
                      </select>
                    </label>
                    <label className="space-y-2 text-sm">
                      <span className="font-semibold text-[#173f36]">発言者名</span>
                      <select
                        className="h-11 w-full rounded-xl border bg-white px-3 text-sm"
                        value={minutesSpeaker}
                        onChange={(e) => {
                          const nextSpeaker = e.target.value;
                          if (nextSpeaker) {
                            runMinutesSpeakerSearch(nextSpeaker);
                          } else {
                            setMinutesSpeaker('');
                          }
                        }}
                      >
                        <option value="">発言者を選択</option>
                        {groupedMinutesSpeakers.slice(0, 300).map((speaker) => (
                          <option key={speaker.displayName} value={speaker.displayName}>
                            {speaker.displayName}{speaker.title ? ` / ${speaker.title}` : ''}（{speaker.roleSummary}・{speaker.utteranceCount.toLocaleString()}発言）
                          </option>
                        ))}
                      </select>
                    </label>
                  </div>

                  <div className="mt-5 grid gap-4 md:grid-cols-3">
                    <label className="space-y-2 text-sm">
                      <span className="font-semibold text-[#173f36]">年</span>
                      <select
                        className="h-11 w-full rounded-xl border bg-white px-3 text-sm"
                        value={minutesSearchYear}
                        onChange={(e) => {
                          setMinutesSearchYearRange(e.target.value);
                          setMinutesSpeaker('');
                        }}
                      >
                        <option value="">すべての年</option>
                        {minutesBrowseFiscalYears.map((year) => (
                          <option key={year} value={String(year)}>{calendarYearLabel(year)}</option>
                        ))}
                      </select>
                    </label>
                    <label className="space-y-2 text-sm">
                      <span className="font-semibold text-[#173f36]">会議種別</span>
                      <select
                        className="h-11 w-full rounded-xl border bg-white px-3 text-sm"
                        value={minutesSection}
                        onChange={(e) => {
                          setMinutesSection(e.target.value);
                          setMinutesMeetingId(null);
                          setMinutesSpeaker('');
                        }}
                      >
                        <option value="all">すべて</option>
                        <option value="本会議">本会議</option>
                        <option value="常任委員会">常任委員会</option>
                        <option value="特別委員会">特別委員会</option>
                      </select>
                    </label>
                    <label className="space-y-2 text-sm">
                      <span className="font-semibold text-[#173f36]">会議名</span>
                      <select
                        className="h-11 w-full rounded-xl border bg-white px-3 text-sm"
                        value={minutesMeetingId ?? ''}
                        onChange={(e) => {
                          setMinutesMeetingId(e.target.value ? Number(e.target.value) : null);
                          setMinutesSpeaker('');
                        }}
                      >
                        <option value="">すべての会議</option>
                        {searchMinutesMeetingOptions.map((meeting) => (
                          <option key={meeting.id} value={meeting.id}>
                            {meeting.section} / {formatMinutesMeetingBrowseTitle(meeting)}
                          </option>
                        ))}
                      </select>
                    </label>
                  </div>

                  {renderMinutesLimitSelector()}

                  <div className="mt-5 rounded-2xl border bg-white p-4">
                    <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                      <div>
                        <p className="text-lg font-semibold text-[#173f36]">発言者候補</p>
                        <p className="mt-1 text-sm text-muted-foreground">条件に対応する発言者を選択して検索します。</p>
                      </div>
                      <span className="w-fit rounded-full bg-[#e3f0e8] px-3 py-1 text-xs font-semibold text-[#2f765e]">
                        {groupedMinutesSpeakers.length.toLocaleString()}人
                      </span>
                    </div>
                    <div className="mt-4 flex flex-wrap gap-2">
                      {groupedMinutesSpeakers.slice(0, 18).map((speaker) => (
                        <button
                          key={speaker.displayName}
                          type="button"
                          disabled={minutesSearching}
                          onClick={() => runMinutesSpeakerSearch(speaker.displayName)}
                          className={`rounded-full border px-3 py-1.5 text-sm font-semibold transition ${
                            minutesSpeaker === speaker.displayName ? 'border-[#2f765e] bg-[#dff2e5] text-[#173f36]' : 'bg-[#fbfdfb] text-[#37564d] hover:border-[#79b28d]'
                          }`}
                        >
                          {speaker.displayName || '氏名なし'}
                          <span className="ml-2 text-xs font-medium text-muted-foreground">{speaker.utteranceCount.toLocaleString()}</span>
                        </button>
                      ))}
                      {groupedMinutesSpeakers.length > 18 ? (
                        <span className="rounded-full border bg-white px-3 py-1.5 text-sm text-muted-foreground">ほか{(groupedMinutesSpeakers.length - 18).toLocaleString()}人はプルダウンから選択</span>
                      ) : null}
                    </div>
                  </div>

                  <div className="mt-5 flex flex-col gap-3 border-t pt-4 sm:flex-row sm:items-center sm:justify-center">
                    <button
                      type="button"
                      onClick={clearMinutesSpeakerSearch}
                      className="inline-flex h-11 min-w-36 items-center justify-center rounded-xl border bg-white px-5 text-sm font-semibold text-[#37564d] hover:bg-[#edf6f0]"
                    >
                      クリア
                    </button>
                  </div>
                </div>
              </div>
            </div>
          ) : null}

          {minutesPage === 'browse' ? (
            <div className="space-y-5">
              {renderMinutesBackButton()}
              <div className="overflow-hidden rounded-3xl border bg-white">
                <div className="border-b bg-[#5f8f8f] px-5 py-4 text-white">
                  <div className="flex flex-col gap-4 xl:flex-row xl:items-end xl:justify-between">
                    <div>
                      <h3 className="text-2xl font-semibold">会議録の閲覧</h3>
                      <p className="mt-1 text-sm text-white/80">年、会議の種類、時系列で会議録を選択し、会議録全文を表示します。</p>
                    </div>
                    <div className="grid gap-3 sm:grid-cols-2">
                      <label className="space-y-1 text-sm font-semibold">
                        <span>年</span>
                        <select
                          className="h-10 min-w-44 rounded-lg border border-white/30 bg-white px-3 text-sm text-[#173f36]"
                          value={effectiveMinutesBrowseFiscalYear}
                          onChange={(e) => {
                            setMinutesBrowseFiscalYear(e.target.value);
                            setMinutesBrowseSection('all');
                          }}
                        >
                          <option value="all">すべての年</option>
                          {minutesBrowseFiscalYears.map((year) => (
                            <option key={year} value={String(year)}>{calendarYearLabel(year)}</option>
                          ))}
                        </select>
                      </label>
                      <label className="space-y-1 text-sm font-semibold">
                        <span>会議の種類</span>
                        <select
                          className="h-10 min-w-52 rounded-lg border border-white/30 bg-white px-3 text-sm text-[#173f36]"
                          value={minutesBrowseSection}
                          onChange={(e) => setMinutesBrowseSection(e.target.value)}
                        >
                          <option value="all">すべて</option>
                          {minutesBrowseSections.map((item) => (
                            <option key={item.section} value={item.section}>{item.section}</option>
                          ))}
                        </select>
                      </label>
                    </div>
                  </div>
                </div>

                <div className="grid min-h-[34rem] lg:grid-cols-[13rem_17rem_minmax(0,1fr)]">
                  <aside className="border-r bg-[#f9fbfb]">
                    <div className="border-b bg-[#d8e8e6] px-4 py-3 text-sm font-semibold text-[#173f36]">年</div>
                    <div className="space-y-1 p-3">
                      {minutesBrowseFiscalYears.map((year) => {
                        const active = effectiveMinutesBrowseFiscalYear === String(year);
                        const count = minutesMeetings.filter((meeting) => calendarYearFromDate(meeting.fromDate || meeting.toDate) === year).length;
                        return (
                          <button
                            key={year}
                            type="button"
                            onClick={() => {
                              setMinutesBrowseFiscalYear(String(year));
                              setMinutesBrowseSection('all');
                            }}
                            className={`w-full rounded-xl px-3 py-2 text-left text-sm font-semibold transition ${
                              active ? 'bg-[#173f36] text-white' : 'hover:bg-[#e7f0ed]'
                            }`}
                          >
                            <span>{calendarYearLabel(year)}</span>
                            <span className={`ml-2 text-xs ${active ? 'text-white/70' : 'text-muted-foreground'}`}>{count}件</span>
                          </button>
                        );
                      })}
                    </div>
                  </aside>

                  <aside className="border-r bg-[#f3f6f5]">
                    <div className="border-b bg-[#d8e8e6] px-4 py-3 text-sm font-semibold text-[#173f36]">会議の種類</div>
                    <div className="space-y-2 p-3">
                      <button
                        type="button"
                        onClick={() => setMinutesBrowseSection('all')}
                        className={`w-full rounded-xl border px-3 py-3 text-left text-sm font-semibold transition ${
                          minutesBrowseSection === 'all' ? 'border-[#173f36] bg-[#173f36] text-white' : 'bg-white hover:border-[#79b28d]'
                        }`}
                      >
                        すべて
                        <span className={`ml-2 text-xs ${minutesBrowseSection === 'all' ? 'text-white/70' : 'text-muted-foreground'}`}>{browsedMinutesMeetings.length}件</span>
                      </button>
                      {minutesBrowseSections.map((item) => (
                        <button
                          key={item.section}
                          type="button"
                          onClick={() => setMinutesBrowseSection(item.section)}
                          className={`w-full rounded-xl border px-3 py-3 text-left text-sm font-semibold transition ${
                            minutesBrowseSection === item.section ? 'border-[#2f765e] bg-[#d7f0dd] text-[#173f36]' : 'bg-white hover:border-[#79b28d]'
                          }`}
                        >
                          <span>{item.section}</span>
                          <span className="ml-2 text-xs text-muted-foreground">{item.count}件</span>
                        </button>
                      ))}
                    </div>
                  </aside>

                  <div className="bg-white">
                    <div className="border-b bg-[#d8e8e6] px-4 py-3 text-sm font-semibold text-[#173f36]">
                      会議一覧（会議録表示） / {effectiveMinutesBrowseFiscalYear === 'all' ? 'すべての年' : calendarYearLabel(Number(effectiveMinutesBrowseFiscalYear))}
                    </div>
                    {browsedMinutesMeetings.length === 0 ? (
                      <div className="p-6 text-sm text-muted-foreground">該当する会議録はありません。</div>
                    ) : (
                      <div className="max-h-[62vh] overflow-auto">
                        {browsedMinutesMeetingsBySection.map(([section, meetings]) => (
                          <section key={section} className="border-b last:border-b-0">
                            <div className={`px-4 py-2 text-sm font-semibold ${section === '本会議' ? 'bg-[#d9d3ef]' : section.includes('委員') ? 'bg-[#ffd3df]' : 'bg-[#edf3ef]'}`}>
                              {section}
                            </div>
                            <div className="divide-y">
                              {meetings.map((meeting) => (
                                <button
                                  key={meeting.id}
                                  type="button"
                                  onClick={() => void openMinutesMeeting(meeting)}
                                  className={`grid w-full gap-2 px-4 py-3 text-left transition hover:bg-[#edf7ef] md:grid-cols-[minmax(0,1fr)_12rem] ${
                                    minutesMeetingId === meeting.id ? 'bg-[#edf7ef]' : 'bg-white'
                                  }`}
                                >
                                  <span className="min-w-0 text-base font-semibold text-blue-700 underline-offset-2 hover:underline">{formatMinutesMeetingBrowseTitle(meeting)}</span>
                                  <span className="text-sm text-muted-foreground md:text-right">{meeting.dayCount}日程 / {meeting.utteranceCount.toLocaleString()}発言 / 表{meeting.tableCount}</span>
                                </button>
                              ))}
                            </div>
                          </section>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              </div>
            </div>
          ) : null}

          {minutesPage === 'collection' ? (
            <div className="mx-auto max-w-5xl space-y-5">
              {renderMinutesBackButton()}
              <div className="rounded-3xl border bg-white p-6">
                <h3 className="text-2xl font-semibold text-[#173f36]">発言集作成</h3>
                <p className="mt-2 text-sm text-muted-foreground">指定した発言者の発言のみを抽出します。必要に応じて、関連する質問や答弁も本文閲覧に含めます。</p>
                <div className="mt-6 space-y-5">
                  <div className="rounded-2xl border bg-[#f8fbf8] p-4">
                    <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                      <div>
                        <p className="text-lg font-semibold text-[#173f36]">発言者候補</p>
                        <p className="mt-1 text-sm text-muted-foreground">候補を選択してから発言集を作成します。</p>
                      </div>
                      <span className="w-fit rounded-full bg-[#e3f0e8] px-3 py-1 text-xs font-semibold text-[#2f765e]">
                        {groupedMinutesSpeakers.length.toLocaleString()}人
                      </span>
                    </div>
                    <div className="mt-4 flex flex-wrap gap-2">
                      {groupedMinutesSpeakers.slice(0, 24).map((speaker) => (
                        <button
                          key={speaker.displayName}
                          type="button"
                          onClick={() => setMinutesSpeaker(speaker.displayName)}
                          className={`rounded-full border px-3 py-1.5 text-sm font-semibold transition ${
                            minutesSpeaker === speaker.displayName ? 'border-[#2f765e] bg-[#dff2e5] text-[#173f36]' : 'bg-white text-[#37564d] hover:border-[#79b28d]'
                          }`}
                        >
                          {speaker.displayName || '氏名なし'}
                          <span className="ml-2 text-xs font-medium text-muted-foreground">{speaker.utteranceCount.toLocaleString()}</span>
                        </button>
                      ))}
                      {groupedMinutesSpeakers.length > 24 ? (
                        <span className="rounded-full border bg-white px-3 py-1.5 text-sm text-muted-foreground">ほか{(groupedMinutesSpeakers.length - 24).toLocaleString()}人は入力欄から選択</span>
                      ) : null}
                    </div>
                  </div>
                  {renderMinutesCommonFilters(false, true, submitMinutesCollection)}
                  <div className="rounded-2xl border bg-[#f8fbf8] p-5">
                    <p className="text-sm font-semibold text-[#173f36]">表示オプション</p>
                    <label className="mt-3 flex items-center gap-2 rounded-xl bg-white px-3 py-2 text-sm">
                      <input type="checkbox" checked={minutesIncludeReplies} onChange={(e) => setMinutesIncludeReplies(e.target.checked)} />
                      関連する答弁や質問も本文閲覧に表示
                    </label>
                    <label className="mt-3 flex items-center gap-2 rounded-xl bg-white px-3 py-2 text-sm">
                      <input type="checkbox" checked={minutesIncludeChair} onChange={(e) => setMinutesIncludeChair(e.target.checked)} />
                      議事進行を表示
                    </label>
                    {renderMinutesLimitSelector()}
                  </div>
                  <div className="mt-6 flex flex-col gap-3 sm:flex-row">
                    <button
                      type="button"
                      disabled={minutesSearching}
                      onClick={submitMinutesCollection}
                      className="inline-flex h-12 min-w-44 items-center justify-center rounded-xl bg-[#2f765e] px-6 text-sm font-semibold text-white shadow-sm disabled:opacity-60"
                    >
                      {minutesSearching ? '作成中…' : '発言集を作成'}
                    </button>
                    <button
                      type="button"
                      onClick={clearMinutesCollectionSearch}
                      className="inline-flex h-12 min-w-36 items-center justify-center rounded-xl border bg-white px-5 text-sm font-semibold text-[#37564d] hover:bg-[#edf6f0]"
                    >
                      条件クリア
                    </button>
                    {minutesResults.length > 0 ? (
                      <button
                        type="button"
                        onClick={() => setMinutesPage('collectionResults')}
                        className="inline-flex h-12 min-w-36 items-center justify-center rounded-xl border bg-white px-5 text-sm font-semibold text-[#37564d] hover:bg-[#edf6f0]"
                      >
                        前回の結果へ
                      </button>
                    ) : null}
                  </div>
                </div>
              </div>
            </div>
          ) : null}

          {minutesPage === 'history' ? (
            <div className="mx-auto max-w-4xl space-y-5">
              {renderMinutesBackButton()}
              <div className="rounded-3xl border bg-white p-6">
                <div className="flex items-center justify-between gap-3">
                  <h3 className="text-2xl font-semibold text-[#173f36]">検索履歴</h3>
                  <button
                    type="button"
                    onClick={() => {
                      setMinutesHistory([]);
                      saveMinutesSearchHistory([]);
                    }}
                    className="rounded-xl border px-3 py-2 text-sm font-semibold text-muted-foreground hover:text-red-600"
                  >
                    クリア
                  </button>
                </div>
                <div className="mt-5 space-y-3">
                  {minutesHistory.length > 0 ? minutesHistory.map((item) => (
                    <button
                      key={item.id}
                      type="button"
                      onClick={() => {
                        applyMinutesHistory(item);
                        setMinutesPage(item.speaker && !item.query ? 'collection' : 'keyword');
                      }}
                      className="w-full rounded-2xl border bg-[#fbfdfb] px-4 py-3 text-left hover:border-[#79b28d]"
                    >
                      <p className="font-semibold">{item.label}</p>
                      <p className="mt-1 text-sm text-muted-foreground">{formatDateTime(item.createdAt)} / {item.matchMode === 'exact' ? '完全一致' : '関連語'} / {item.op}</p>
                    </button>
                  )) : <p className="text-sm text-muted-foreground">検索履歴はありません。</p>}
                </div>
              </div>
            </div>
          ) : null}
        </div>
      </section>
    );
  };

  if (loading) {
    return <div className="min-h-screen bg-background p-8 text-muted-foreground">読み込み中…</div>;
  }
  if (authEnabled && !user) {
    return <LoginCard onLogin={handleLogin} />;
  }

  return (
    <div className="min-h-screen bg-background text-foreground">
      <PortalHeader
        title="美祢市例規・法令・会議録DB"
        subtitle="美祢市例規・地方自治法・地方公務員法・美祢市議会会議録を横断検索し、他システムから参照できるようにします。"
        syncStatusText={syncBadgeText(syncStatus)}
        syncStatusTone={toneForStatus(syncStatus)}
        onOpenSettings={() => setTab('settings')}
        user={user}
        onLogout={handleLogout}
        authEnabled={Boolean(authEnabled)}
      />
      {user?.isGuest ? (
        <div className="mx-auto mt-4 max-w-7xl px-4">
          <div className="rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
            ゲストログイン中です。例規データの閲覧と印刷はできますが、同期設定や辞書更新はできません。
          </div>
        </div>
      ) : null}
      <main className="mx-auto max-w-7xl space-y-6 px-4 py-6 sm:px-6 lg:px-8">
        <section className="rounded-3xl border bg-card p-3 shadow-sm">
          <div className="grid gap-2 md:grid-cols-4">
            {TABS.map((item) => {
              const Icon = item.icon;
              const active = tab === item.id;
              return (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => setTab(item.id)}
                  className={`inline-flex h-12 items-center justify-center gap-2 rounded-2xl border px-4 text-sm font-semibold transition ${
                    active ? 'border-primary bg-primary text-primary-foreground shadow-sm' : 'border-border bg-background text-foreground hover:bg-accent'
                  }`}
                >
                  <Icon className="size-4" />
                  <span>{item.label}</span>
                </button>
              );
            })}
          </div>
        </section>

        {tab === 'dashboard' ? (
          <div className="grid gap-4 md:grid-cols-3">
            {statCards.map((card) => (
              <section key={card.label} className="rounded-3xl border bg-card p-5 shadow-sm">
                <p className="text-sm text-muted-foreground">{card.label}</p>
                <p className="mt-1 text-3xl font-semibold">{card.total}</p>
                <dl className="mt-3 space-y-1 border-t pt-3">
                  {card.rows.map((row) => (
                    <div key={row.label} className="flex items-center justify-between gap-2 text-sm">
                      <dt className="text-muted-foreground">{row.label}</dt>
                      <dd className="font-medium">{row.value}</dd>
                    </div>
                  ))}
                </dl>
              </section>
            ))}
          </div>
        ) : null}

        {globalError ? <div className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{globalError}</div> : null}

        {tab === 'dashboard' ? (
          <div className="space-y-6">
            <section className="grid gap-6 lg:grid-cols-[1.4fr_1fr]">
              <div className="rounded-3xl border bg-card p-6 shadow-sm">
                <div className="flex items-center gap-2">
                  <BookMarked className="size-5 text-primary" />
                  <h2 className="text-xl font-semibold">システム概要</h2>
                </div>
                <div className="mt-4 space-y-4 text-sm leading-7 text-muted-foreground">
                  <p>このシステムは、地方自治法・地方公務員法・美祢市例規を条文単位で保存し、美祢市議会会議録を発言単位で保存して、全文検索と簡易質問応答で参照できるようにするモジュールです。</p>
                  <p>他アプリからは API 経由で検索・条文参照が可能です。まずは候補条文を提示する方式で安全に運用し、その後に高度な要約や引用補助を追加できます。</p>
                  <p>更新は公開ページからの同期で行います。月次設定を有効化すると、設定した日時を過ぎた時点で定期チェックが走り、最新データを取り込みます。</p>
                </div>
              </div>
              <div className="rounded-3xl border bg-card p-6 shadow-sm">
                <h2 className="text-xl font-semibold">直近の同期</h2>
                <div className="mt-4 space-y-3">
                  {syncRuns.length === 0 ? (
                    <p className="text-sm text-muted-foreground">まだ同期履歴はありません。</p>
                  ) : (
                    syncRuns.slice(0, 3).map((run) => (
                      <div key={run.id} className="rounded-2xl border bg-background p-4 text-sm">
                        <div className="flex items-center justify-between gap-2">
                          <span className="font-medium">{run.runType === 'scheduled' ? '定期同期' : '手動同期'}</span>
                          <span className={run.status === 'failed' ? 'text-red-600' : 'text-emerald-700'}>{run.status}</span>
                        </div>
                        <p className="mt-2 text-muted-foreground">開始: {formatDateTime(run.startedAt)}</p>
                        <p className="text-muted-foreground">終了: {formatDateTime(run.finishedAt)}</p>
                        {run.errorText ? <p className="mt-2 text-red-600">{run.errorText}</p> : null}
                      </div>
                    ))
                  )}
                </div>
              </div>
            </section>
            <section className="grid gap-6 lg:grid-cols-3">
              <RevisionPanel title="美祢市例規 — 最近の改定" items={syncStatus.mineCityLatestRevisions} />
              <RevisionPanel title="地方自治法 — 最近の改定" items={syncStatus.egovLatestRevisions} />
              <RevisionPanel title="地方公務員法 — 最近の改定" items={syncStatus.localPublicServiceLatestRevisions} />
            </section>
            {analyticsData ? (
              <section className="rounded-3xl border bg-card p-6 shadow-sm">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="flex items-center gap-2">
                    <BarChart2 className="size-5 text-primary" />
                    <h2 className="text-xl font-semibold">利用統計</h2>
                  </div>
                  {analyticsData.latestUsedAt ? (
                    <p className="text-sm text-muted-foreground">最終利用: {analyticsData.latestUsedAt}</p>
                  ) : null}
                </div>
                <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                  {[
                    { label: '総利用回数', value: analyticsData.totalUsageEvents.toLocaleString() },
                    { label: '例規・法令検索', value: analyticsData.lawSearchCount.toLocaleString() },
                    { label: '会議録検索', value: analyticsData.minutesSearchCount.toLocaleString() },
                    { label: '質問', value: analyticsData.askCount.toLocaleString() },
                  ].map((s) => (
                    <div key={s.label} className="rounded-2xl border bg-background p-4">
                      <p className="text-xs text-muted-foreground">{s.label}</p>
                      <p className="mt-1 text-2xl font-semibold">{s.value}</p>
                    </div>
                  ))}
                </div>
                <div className="mt-4 grid gap-4 lg:grid-cols-3">
                  {[
                    { title: '例規・法令検索ランキング', items: analyticsData.topLawSearchQueries },
                    { title: '会議録検索ランキング', items: analyticsData.topMinutesSearchQueries },
                    { title: '質問ランキング', items: analyticsData.topUsageAskQueries },
                  ].map((group) => (
                    <div key={group.title}>
                      <p className="mb-2 text-sm font-semibold">{group.title}</p>
                      {group.items.length > 0 ? (
                        <ol className="space-y-1">
                          {group.items.slice(0, 5).map((q, i) => (
                            <li key={`${q.query}-${i}`} className="flex items-center justify-between gap-3 rounded-xl border bg-background px-3 py-2 text-sm">
                              <span className="min-w-0 truncate text-muted-foreground">{i + 1}. {q.query}</span>
                              <span className="shrink-0 font-medium">{q.hits}回</span>
                            </li>
                          ))}
                        </ol>
                      ) : (
                        <div className="rounded-xl border bg-background px-3 py-2 text-sm text-muted-foreground">まだ利用記録がありません</div>
                      )}
                    </div>
                  ))}
                </div>
                <p className="mt-3 text-xs text-muted-foreground">
                  キャッシュ: 検索 {analyticsData.searchCacheEntries.toLocaleString()}件 / 質問 {analyticsData.askCacheEntries.toLocaleString()}件
                </p>
              </section>
            ) : null}
          </div>
        ) : null}

        {tab === 'browse' ? (
          <section className="grid gap-6 lg:grid-cols-[minmax(0,0.88fr)_minmax(0,1.52fr)]">
            <div className="space-y-4 rounded-3xl border bg-card p-6 shadow-sm">
              <div className="flex items-center justify-between gap-3">
                <h2 className="text-xl font-semibold">体系別閲覧</h2>
                <div className="flex items-center gap-2">
                  <div className="inline-flex rounded-2xl border bg-background p-1">
                    {([
                      { value: 'mine-city', label: '美祢市例規集' },
                      { value: 'egov', label: '地方自治法' },
                      { value: 'local-public-service', label: '地方公務員法' },
                    ] as const).map((option) => (
                      <button
                        key={option.value}
                        type="button"
                        onClick={() => {
                          setBrowseSource(option.value);
                          void loadBrowseList(option.value);
                        }}
                        className={`rounded-xl px-3 py-1.5 text-sm font-medium transition ${
                          browseSource === option.value
                            ? 'bg-primary text-primary-foreground shadow-sm'
                            : 'text-muted-foreground hover:bg-accent'
                        }`}
                      >
                        {option.label}
                      </button>
                    ))}
                  </div>
                  <a
                    href={buildDocumentsCsvUrl(browseSource)}
                    download
                    className="inline-flex h-9 items-center gap-1 rounded-xl border bg-background px-3 text-sm font-medium hover:bg-accent"
                    title="一覧をCSVでダウンロード"
                  >
                    <Download className="size-3.5" />
                    CSV
                  </a>
                </div>
              </div>
              {browseLoading ? (
                <p className="text-sm text-muted-foreground">読み込み中…</p>
              ) : browseList.length === 0 ? (
                <p className="text-sm text-muted-foreground">データがありません。同期設定からデータを取得してください。</p>
              ) : (
                <div className="max-h-[70vh] overflow-auto space-y-4">
                  <div className="rounded-2xl border bg-muted/20 px-4 py-3 text-sm text-muted-foreground">
                    {browseSource === 'mine-city'
                      ? '美祢市例規集の体系に沿って、分類ごとに番号順で閲覧できます。'
                      : browseSource === 'egov'
                        ? '地方自治法を条文番号順で閲覧できます。'
                        : '地方公務員法を条文番号順で閲覧できます。'}
                  </div>
                  {renderBrowseTree(browseTree)}
                  {browseSource !== 'mine-city' && browseDoc ? (
                    <div className="rounded-2xl border bg-muted/20 p-3">
                      <div className="mb-3 flex items-center justify-between gap-2">
                        <p className="text-sm font-semibold">章・条文一覧</p>
                        <span className="text-xs text-muted-foreground">{browseDoc.articles.length.toLocaleString()}条</span>
                      </div>
                      {renderArticleNavTree(browseDocArticleTree, 'barticle')}
                    </div>
                  ) : null}
                </div>
              )}
            </div>
            <div className="rounded-3xl border bg-card p-6 shadow-sm">
              {browseDocLoading ? (
                <p className="text-sm text-muted-foreground">読み込み中…</p>
              ) : !browseDoc ? (
                <p className="text-sm text-muted-foreground">左の一覧から例規を選択すると、全文を表示します。</p>
              ) : (
                <>
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <p className="text-sm text-muted-foreground">{sourceLabel(browseDoc.source)} / {browseDoc.lawType || '例規'}</p>
                      <h2 className="mt-1 text-2xl font-semibold">{browseDoc.title}</h2>
                      <div className="mt-2 flex items-center gap-2">
                        <button
                          type="button"
                          onClick={() => toggleBookmark(browseDoc.id)}
                          className={`inline-flex items-center gap-1 rounded-lg border px-2 py-1 text-xs transition hover:bg-accent ${bookmarkIds.includes(browseDoc.id) ? 'border-amber-400 text-amber-600' : ''}`}
                        >
                          <Star className={`size-3 ${bookmarkIds.includes(browseDoc.id) ? 'fill-amber-400' : ''}`} />
                          {bookmarkIds.includes(browseDoc.id) ? 'ブックマーク済み' : 'ブックマーク'}
                        </button>
                        <button
                          type="button"
                          onClick={() => void openHistory(browseDoc.id, browseDoc.title, browseDoc.fullText)}
                          className="inline-flex items-center gap-1 rounded-lg border px-2 py-1 text-xs hover:bg-accent"
                        >
                          <Clock className="size-3" />
                          変更履歴
                        </button>
                        <button
                          type="button"
                          onClick={() => window.print()}
                          className="inline-flex items-center gap-1 rounded-lg border px-2 py-1 text-xs hover:bg-accent"
                        >
                          <Printer className="size-3" />
                          印刷
                        </button>
                      </div>
                      <p className="mt-2 text-sm text-muted-foreground">{browseDoc.lawNumber || '法令番号なし'} / {browseDoc.categoryPath || '分類なし'}</p>
                      {(browseDoc.promulgatedAt || browseDoc.effectiveAt) ? (
                        <p className="mt-1 text-sm text-muted-foreground">
                          {browseDoc.promulgatedAt ? `公布: ${browseDoc.promulgatedAt}` : ''}
                          {browseDoc.promulgatedAt && browseDoc.effectiveAt ? '　' : ''}
                          {browseDoc.effectiveAt ? `施行: ${browseDoc.effectiveAt}` : ''}
                        </p>
                      ) : null}
                    </div>
                    <div className="flex shrink-0 flex-col gap-2">
                      <a className="inline-flex h-10 items-center justify-center rounded-2xl border px-4 text-sm font-medium hover:bg-accent" href={browseDoc.sourceUrl} rel="noreferrer" target="_blank">
                        原文を開く
                      </a>
                      <button
                        type="button"
                        disabled={browseReturnScrollTop == null}
                        onClick={returnBrowseLinkPosition}
                        className="inline-flex h-9 items-center justify-center gap-1 rounded-2xl border px-3 text-sm font-medium hover:bg-accent disabled:cursor-not-allowed disabled:opacity-40"
                      >
                        <ChevronLeft className="size-4" />
                        リンク元に戻る
                      </button>
                    </div>
                  </div>
                  <div className="mt-6">
                    <div ref={browseArticleScrollRef} className="max-h-[65vh] overflow-auto rounded-2xl border bg-background p-5 print:max-h-none">
                      {browseSource === 'mine-city' ? (
                        <div className="mb-5 rounded-2xl border bg-muted/20 p-3">
                          <p className="mb-3 text-sm font-semibold">条文一覧</p>
                          {renderArticleNavTree(browseDocArticleTree, 'barticle')}
                        </div>
                      ) : null}
                      <div className="space-y-7">
                        {browseDoc.articles.length > 0 ? (
                          renderArticleBodyTree(
                            browseDocArticleTree,
                            'barticle',
                            [],
                            [],
                            buildArticleLinkMap(browseDoc.articles, 'barticle'),
                            buildSourceAnchorLinkMap(browseDoc, 'barticle'),
                            buildSourceDocumentLinkMap(browseDoc),
                            browseDoc.sourceUrl,
                            openBrowseSourceDocument,
                            rememberBrowseReturnPosition,
                            null,
                          )
                        ) : (
                          <ArticleContent text={browseDoc.fullText} />
                        )}
                      </div>
                    </div>
                  </div>
                </>
              )}
            </div>
          </section>
        ) : null}

        {tab === 'search' ? (
          <section className="grid gap-6 lg:grid-cols-[minmax(0,0.58fr)_minmax(0,1.92fr)]">
            <div className="space-y-4 rounded-3xl border bg-card p-6 shadow-sm">
              <h2 className="text-xl font-semibold">例規検索</h2>
              <div className="flex items-center justify-between gap-2">
                <p className="text-xs text-muted-foreground">デフォルトは完全一致検索です。関連語を含める場合は詳細絞り込みで曖昧検索を有効にしてください。</p>
                <button type="button" onClick={() => setShowAdvancedFilter((v) => !v)} className="text-xs text-primary underline">
                  {showAdvancedFilter ? '▲ 絞り込みを閉じる' : '▼ 詳細絞り込み'}
                </button>
              </div>
              {showAdvancedFilter ? (
                <div className="rounded-2xl border bg-accent/30 p-3 space-y-2">
                  <div className="grid gap-2 sm:grid-cols-3">
                    <label className="space-y-1 text-xs">
                      <span className="font-medium text-muted-foreground">法令種別</span>
                      <select
                        className="h-8 w-full rounded-lg border bg-input-background px-2 text-sm"
                        value={searchLawType}
                        onChange={(e) => setSearchLawType(e.target.value)}
                      >
                        <option value="">すべて</option>
                        {lawTypeOptions.map((t) => <option key={t} value={t}>{t}</option>)}
                      </select>
                    </label>
                    <label className="space-y-1 text-xs">
                      <span className="font-medium text-muted-foreground">公布日（開始）</span>
                      <input type="date" className="h-8 w-full rounded-lg border bg-input-background px-2 text-sm" value={searchFromDate} onChange={(e) => setSearchFromDate(e.target.value)} />
                    </label>
                    <label className="space-y-1 text-xs">
                      <span className="font-medium text-muted-foreground">公布日（終了）</span>
                      <input type="date" className="h-8 w-full rounded-lg border bg-input-background px-2 text-sm" value={searchToDate} onChange={(e) => setSearchToDate(e.target.value)} />
                    </label>
                  </div>
                  <label className="flex items-center gap-2 text-xs text-muted-foreground">
                    <input type="checkbox" checked={searchFuzzy} onChange={(e) => setSearchFuzzy(e.target.checked)} />
                    曖昧検索（関連語検索）を有効にする
                  </label>
                  {(searchLawType || searchFromDate || searchToDate || searchFuzzy) ? (
                    <button type="button" onClick={() => { setSearchLawType(''); setSearchFromDate(''); setSearchToDate(''); setSearchFuzzy(false); }} className="text-xs text-muted-foreground underline">フィルタをクリア</button>
                  ) : null}
                </div>
              ) : null}
              <div className="space-y-2">
                {searchFields.map((field, idx) => (
                  <div key={idx} className="flex items-center gap-2">
                    {idx === 0 ? (
                      <span className="w-14 shrink-0 text-center text-xs font-semibold text-muted-foreground">検索語</span>
                    ) : (
                      <button
                        type="button"
                        onClick={() =>
                          setSearchFields((prev) =>
                            prev.map((f, i) => (i === idx ? { ...f, op: f.op === 'AND' ? 'OR' : 'AND' } : f)),
                          )
                        }
                        className={`w-14 shrink-0 rounded-lg border py-1 text-xs font-bold transition ${
                          field.op === 'AND'
                            ? 'border-primary bg-primary text-primary-foreground'
                            : 'border-amber-400 bg-amber-50 text-amber-700'
                        }`}
                      >
                        {field.op}
                      </button>
                    )}
                    <div className="relative flex-1">
                      <input
                        className="h-10 w-full rounded-xl border bg-input-background px-3 text-sm"
                        placeholder={idx === 0 ? '例: 会計年度 任用職員' : `キーワード ${idx + 1}（任意）`}
                        value={field.q}
                        onChange={(e) => {
                          setSearchFields((prev) => prev.map((f, i) => (i === idx ? { ...f, q: e.target.value } : f)));
                          if (idx === 0) {
                            const v = e.target.value;
                            const hist = searchHistoryRef.current;
                            setSearchSuggest(v ? hist.filter((h) => h.includes(v)).slice(0, 5) : hist.slice(0, 5));
                            setShowSuggest(true);
                          }
                        }}
                        onFocus={() => {
                          if (idx === 0) {
                            setSearchSuggest(searchHistoryRef.current.slice(0, 5));
                            setShowSuggest(true);
                          }
                        }}
                        onBlur={() => setTimeout(() => setShowSuggest(false), 150)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') { setShowSuggest(false); void submitSearch(0); }
                          if (e.key === 'Escape') setShowSuggest(false);
                        }}
                      />
                      {idx === 0 && showSuggest && searchSuggest.length > 0 ? (
                        <div className="absolute left-0 top-full z-10 mt-1 w-full rounded-xl border bg-card shadow-lg">
                          {searchSuggest.map((s) => (
                            <button
                              key={s}
                              type="button"
                              className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-accent"
                              onMouseDown={() => {
                                setSearchFields((prev) => prev.map((f, i) => (i === 0 ? { ...f, q: s } : f)));
                                setShowSuggest(false);
                              }}
                            >
                              <Search className="size-3 text-muted-foreground" />
                              {s}
                            </button>
                          ))}
                        </div>
                      ) : null}
                    </div>
                  </div>
                ))}
              </div>
              <div className="flex gap-3">
                <select className="h-10 rounded-xl border bg-input-background px-3 text-sm" value={searchSource} onChange={(e) => setSearchSource(e.target.value as any)}>
                  <option value="all">全ソース</option>
                  <option value="mine-city">美祢市例規</option>
                  <option value="egov">地方自治法</option>
                  <option value="local-public-service">地方公務員法</option>
                </select>
                <button className="inline-flex h-10 flex-1 items-center justify-center rounded-xl bg-primary px-4 font-semibold text-primary-foreground disabled:opacity-60" disabled={searching} onClick={() => void submitSearch(0)}>
                  {searching ? '検索中…' : '検索'}
                </button>
              </div>
              <div className="space-y-3">
                {results.length === 0 ? (
                  <p className="text-sm text-muted-foreground">キーワードを入力して検索してください。</p>
                ) : (
                  <>
                    <div className="flex items-center justify-between gap-3">
                      <p className="text-sm text-muted-foreground">
                        全{searchTotal}件中 {searchPage * 20 + 1}〜{Math.min(searchTotal, searchPage * 20 + results.length)}件
                        {groupedResults.length !== results.length ? ` / ${groupedResults.length}文書に集約` : ''}
                      </p>
                      {searchTotal >= 20 ? (
                        <p className="text-sm text-amber-600">件数が多いため、キーワードを追加して絞り込んでください。</p>
                      ) : null}
                    </div>
                    {groupedResults.map((group) => {
                      const firstHit = group.hits[0];
                      const isActiveGroup = selectedDocId === group.documentId;
                      return (
                        <div
                          key={`group-${group.documentId}`}
                          className={`rounded-2xl border p-4 transition ${isActiveGroup ? 'border-primary bg-primary/5' : 'bg-background'}`}
                        >
                          <div className="flex items-start justify-between gap-3">
                            <div>
                              <p className="text-xs text-muted-foreground">{sourceLabel(group.source)} / {group.lawType || '例規'}</p>
                              <button
                                type="button"
                                onClick={() => openSearchResult(firstHit)}
                                className="mt-1 text-left font-semibold hover:text-primary"
                              >
                                {group.title}
                              </button>
                              {group.lawNumber ? <p className="mt-1 text-xs text-muted-foreground">{group.lawNumber}</p> : null}
                            </div>
                            <div className="flex shrink-0 flex-col items-end gap-1">
                              <span className="rounded-full bg-primary/10 px-2 py-1 text-xs font-semibold text-primary">{group.hits.length}件</span>
                              <span className="rounded-full bg-accent px-2 py-1 text-xs text-muted-foreground">score {group.maxScore}</span>
                            </div>
                          </div>
                          <div className="mt-3 space-y-1.5">
                            {group.hits.map((item, idx) => {
                              const isActiveHit = selectedDocId === item.documentId
                                && (item.articleId == null || (
                                  activeSelectedArticleHit?.documentId === item.documentId
                                  && activeSelectedArticleHit.articleId === item.articleId
                                ));
                              return (
                                <button
                                  key={`${item.documentId}-${item.articleId ?? 'doc'}-${idx}`}
                                  type="button"
                                  onClick={() => openSearchResult(item)}
                                  className={`w-full rounded-xl px-3 py-1.5 text-left transition ${isActiveHit ? 'bg-primary/10 ring-1 ring-primary/30' : 'bg-muted/35 hover:bg-accent/50'}`}
                                >
                                  <div className="flex flex-wrap items-center gap-2">
                                    <span className="rounded-full bg-primary/10 px-2.5 py-1 text-sm font-semibold text-primary">
                                      {item.articleNumber ? `ヒット条文: ${item.articleNumber}${item.articleTitle ? ` ${item.articleTitle}` : ''}` : '文書全体'}
                                    </span>
                                    {item.matchReasons && item.matchReasons.length > 0 ? (
                                      <span className="flex flex-wrap items-center gap-1">
                                        <span className="text-xs font-medium text-muted-foreground">ヒット箇所:</span>
                                        {item.matchReasons.map((r) => (
                                          <span key={`${item.documentId}-${item.articleId ?? 'doc'}-${r}`} className="rounded-full bg-primary/10 px-2 py-0.5 text-xs text-primary">{r}</span>
                                        ))}
                                      </span>
                                    ) : null}
                                  </div>
                                </button>
                              );
                            })}
                          </div>
                          <p className="mt-3 text-sm text-muted-foreground">{group.categoryPath || '分類なし'}</p>
                        </div>
                      );
                    })}
                    {searchTotal > 20 ? (
                      <div className="flex items-center justify-center gap-2 pt-2">
                        <button
                          type="button"
                          disabled={searchPage === 0}
                          onClick={() => void submitSearch(searchPage - 1)}
                          className="inline-flex h-8 items-center gap-1 rounded-lg border px-2 text-sm disabled:opacity-40 hover:bg-accent"
                        >
                          <ChevronLeft className="size-4" />前
                        </button>
                        <span className="text-sm text-muted-foreground">{searchPage + 1} / {Math.ceil(searchTotal / 20)}</span>
                        <button
                          type="button"
                          disabled={(searchPage + 1) * 20 >= searchTotal}
                          onClick={() => void submitSearch(searchPage + 1)}
                          className="inline-flex h-8 items-center gap-1 rounded-lg border px-2 text-sm disabled:opacity-40 hover:bg-accent"
                        >
                          次<ChevronRight className="size-4" />
                        </button>
                      </div>
                    ) : null}
                  </>
                )}
              </div>
            </div>
            <div className="rounded-3xl border bg-card p-6 shadow-sm">
              {!selectedDoc ? (
                <p className="text-sm text-muted-foreground">検索結果または質問候補から条文を選択してください。</p>
              ) : (
                <>
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <p className="text-sm text-muted-foreground">{sourceLabel(selectedDoc.source)} / {selectedDoc.lawType || '例規'}</p>
                      <h2 className="mt-1 text-2xl font-semibold">{selectedDoc.title}</h2>
                      <p className="mt-2 text-sm text-muted-foreground">{selectedDoc.lawNumber || '法令番号なし'} / {selectedDoc.categoryPath || '分類なし'}</p>
                      <div className="mt-2 flex items-center gap-2">
                        <button type="button" onClick={() => toggleBookmark(selectedDoc.id)} className={`inline-flex items-center gap-1 rounded-lg border px-2 py-1 text-xs transition hover:bg-accent ${bookmarkIds.includes(selectedDoc.id) ? 'border-amber-400 text-amber-600' : ''}`}>
                          <Star className={`size-3 ${bookmarkIds.includes(selectedDoc.id) ? 'fill-amber-400' : ''}`} />
                          {bookmarkIds.includes(selectedDoc.id) ? 'ブックマーク済み' : 'ブックマーク'}
                        </button>
                        <button type="button" onClick={() => void openHistory(selectedDoc.id, selectedDoc.title, selectedDoc.fullText)} className="inline-flex items-center gap-1 rounded-lg border px-2 py-1 text-xs hover:bg-accent">
                          <Clock className="size-3" />変更履歴
                        </button>
                        <button type="button" onClick={() => window.print()} className="inline-flex items-center gap-1 rounded-lg border px-2 py-1 text-xs hover:bg-accent">
                          <Printer className="size-3" />印刷
                        </button>
                      </div>
                    </div>
                    <div className="flex shrink-0 flex-col gap-2">
                      <a className="inline-flex h-10 items-center justify-center rounded-2xl border px-4 text-sm font-medium hover:bg-accent" href={selectedDoc.sourceUrl} rel="noreferrer" target="_blank">
                        原文を開く
                      </a>
                      <button
                        type="button"
                        disabled={selectedReturnScrollTop == null}
                        onClick={returnSelectedLinkPosition}
                        className="inline-flex h-9 items-center justify-center gap-1 rounded-2xl border px-3 text-sm font-medium hover:bg-accent disabled:cursor-not-allowed disabled:opacity-40"
                      >
                        <ChevronLeft className="size-4" />
                        リンク元に戻る
                      </button>
                    </div>
                  </div>
                  <div className="mt-6 grid gap-4 xl:grid-cols-[15rem_minmax(0,1fr)]">
                    <div className="max-h-[70vh] overflow-auto rounded-2xl border bg-muted/20 p-3">
                      <p className="mb-3 text-sm font-semibold">条文一覧</p>
                      {renderArticleNavTree(selectedDocArticleTree, 'article')}
                    </div>
                    <div ref={selectedArticleScrollRef} className="max-h-[70vh] overflow-auto rounded-2xl border bg-background p-5">
                      <div className="space-y-6">
                        {selectedDoc.articles.length > 0 ? (
                          renderArticleBodyTree(
                            selectedDocArticleTree,
                            'article',
                            selectedSearchHighlightTerms,
                            selectedSearchRelatedHighlightTerms,
                            buildArticleLinkMap(selectedDoc.articles, 'article'),
                            buildSourceAnchorLinkMap(selectedDoc, 'article'),
                            buildSourceDocumentLinkMap(selectedDoc),
                            selectedDoc.sourceUrl,
                            openSelectedSourceDocument,
                            rememberSelectedReturnPosition,
                            activeSelectedArticleHit?.documentId === selectedDoc.id ? activeSelectedArticleHit.articleId : null,
                          )
                        ) : (
                          <ArticleContent text={selectedDoc.fullText} keywords={selectedSearchHighlightTerms} relatedKeywords={selectedSearchRelatedHighlightTerms} />
                        )}
                      </div>
                    </div>
                  </div>
                  {/* 関連条文 */}
                  {(relatedLoading || relatedResults.length > 0) ? (
                    <div className="mt-6 border-t pt-4">
                      <p className="mb-3 text-sm font-semibold text-muted-foreground">関連条文</p>
                      {relatedLoading ? (
                        <p className="text-xs text-muted-foreground">読み込み中…</p>
                      ) : (
                        <div className="space-y-2">
                          {relatedResults.map((r) => (
                            <button
                              key={`${r.documentId}-${r.articleId}`}
                              type="button"
                              onClick={() => openSearchResult(r)}
                              className="w-full rounded-xl border bg-background px-3 py-2 text-left text-sm hover:bg-accent/40"
                            >
                              <p className="text-xs text-muted-foreground">{sourceLabel(r.source)} / {r.lawType || '例規'}</p>
                              <p className="font-medium leading-snug">{r.title}</p>
                              {r.articleNumber ? <p className="text-xs text-primary">{r.articleNumber}</p> : null}
                            </button>
                          ))}
                        </div>
                      )}
                    </div>
                  ) : null}
                </>
              )}
            </div>
          </section>
        ) : null}

        {tab === 'minutes' ? renderMinutesWorkspace() : null}
        {tab === 'ask' ? (
          <section className="grid gap-6 lg:grid-cols-[1fr_1.6fr]">
            {/* 左パネル：質問入力 + 履歴 */}
            <div className="space-y-4 rounded-3xl border bg-card p-6 shadow-sm">
              <h2 className="text-xl font-semibold">条文照会</h2>
              <p className="text-sm text-muted-foreground">解釈を断定せず、関連条文の候補を提示します。必ず原文を確認してください。</p>
              <textarea
                className="min-h-36 w-full rounded-2xl border bg-input-background p-4 text-sm"
                placeholder="例: 会計年度任用職員が育児休業を取得できる要件は何ですか"
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) void submitQuestion();
                }}
              />
              <button
                className="inline-flex h-11 w-full items-center justify-center rounded-2xl bg-primary font-semibold text-primary-foreground disabled:opacity-60"
                disabled={asking}
                onClick={() => void submitQuestion()}
              >
                {asking ? '照会中…' : '条文を照会（Ctrl+Enter）'}
              </button>
              <div>
                <p className="mb-2 text-xs font-semibold text-muted-foreground">よくある質問テンプレート</p>
                <div className="flex flex-wrap gap-2">
                  {[
                    '会計年度任用職員の育児休業取得要件は？',
                    '住民票の交付申請に必要な書類は？',
                    '議会の定例会はいつ開催されますか？',
                    '情報公開請求の手続きを教えてください',
                    '条例違反の罰則規定はどこに定められていますか？',
                    '市長の権限と職務範囲は何ですか？',
                  ].map((tmpl) => (
                    <button
                      key={tmpl}
                      type="button"
                      onClick={() => { setQuestion(tmpl); }}
                      className="rounded-full border px-3 py-1 text-xs hover:bg-accent transition"
                    >
                      {tmpl}
                    </button>
                  ))}
                </div>
              </div>
              {questionHistory.length > 0 ? (
                <div>
                  <p className="mb-2 text-xs font-semibold text-muted-foreground">最近の質問</p>
                  <div className="space-y-1">
                    {questionHistory.map((h) => (
                      <button
                        key={h}
                        type="button"
                        onClick={() => void submitQuestion(h)}
                        className="w-full rounded-xl border bg-background px-3 py-2 text-left text-xs text-muted-foreground hover:bg-accent hover:text-foreground"
                      >
                        {h}
                      </button>
                    ))}
                  </div>
                </div>
              ) : null}
            </div>
            {/* 右パネル：回答 */}
            <div className="rounded-3xl border bg-card p-6 shadow-sm">
              {!answer ? (
                <p className="text-sm text-muted-foreground">質問すると、関連条文を文書ごとにまとめて表示します。</p>
              ) : (
                <div className="space-y-5">
                  {/* サマリ */}
                  <div className="rounded-2xl border bg-background p-4">
                    <div className="flex items-start justify-between gap-3">
                      <p className="font-medium">{answer.query}</p>
                      <span className="shrink-0 rounded-full border px-2 py-0.5 text-xs text-muted-foreground">{answer.questionTypeLabel}</span>
                    </div>
                    <p className="mt-3 text-sm leading-6">{answer.answerLead}</p>
                    {answer.keywords.length > 0 ? (
                      <div className="mt-3 flex flex-wrap gap-2">
                        {answer.keywords.map((kw) => (
                          <span key={kw} className="rounded-full border px-2 py-0.5 text-xs text-muted-foreground">{kw}</span>
                        ))}
                      </div>
                    ) : null}
                  </div>
                  {/* 文書グループ */}
                  <div className="max-h-[65vh] space-y-4 overflow-auto">
                    {(answer.candidateGroups ?? []).map((group: AskCandidateGroup) => (
                      <div key={group.documentId} className="rounded-2xl border bg-background">
                        {/* 文書ヘッダ */}
                        <div className="flex items-start justify-between gap-3 border-b p-4">
                          <div>
                            <p className="text-xs text-muted-foreground">{sourceLabel(group.source)} / {group.lawType || '例規'}</p>
                            <p className="mt-1 font-semibold">{group.title}</p>
                            {group.lawNumber ? <p className="mt-0.5 text-xs text-muted-foreground">{group.lawNumber}</p> : null}
                          </div>
                          <a href={group.sourceUrl} target="_blank" rel="noreferrer" className="shrink-0 rounded-lg border px-2 py-1 text-xs hover:bg-accent">原文</a>
                        </div>
                        {/* 条文リスト */}
                        <div className="divide-y">
                          {group.articles.map((art, aidx) => {
                            const key = `${group.documentId}-${art.articleId ?? aidx}`;
                            const expanded = expandedArticleKeys.has(key);
                            return (
                              <div key={key}>
                                <button
                                  type="button"
                                  className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left hover:bg-accent/40"
                                  onClick={() =>
                                    setExpandedArticleKeys((prev) => {
                                      const next = new Set(prev);
                                      expanded ? next.delete(key) : next.add(key);
                                      return next;
                                    })
                                  }
                                >
                                  <div>
                                    {art.articleNumber ? (
                                      <span className="text-sm font-medium text-primary">{art.articleNumber}{art.articleTitle ? `　${art.articleTitle}` : ''}</span>
                                    ) : (
                                      <span className="text-sm text-muted-foreground">（全文）</span>
                                    )}
                                  </div>
                                  <span className="text-xs text-muted-foreground">{expanded ? '▲ 閉じる' : '▼ 条文を見る'}</span>
                                </button>
                                {expanded ? (
                                  <div className="border-t bg-accent/20 px-4 py-3">
                                    <ArticleContent text={art.articleText || '（本文なし）'} keywords={answer?.keywords ?? []} />
                                  </div>
                                ) : null}
                              </div>
                            );
                          })}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </section>
        ) : null}

        {tab === 'settings' ? (
          <section className="grid gap-6 lg:grid-cols-[0.95fr_1.05fr]">
            <div className="rounded-3xl border bg-card p-6 shadow-sm">
              <h2 className="text-xl font-semibold">月次同期設定</h2>
              <p className="mt-2 text-sm text-muted-foreground">毎月指定日時を過ぎたタイミングで更新確認を実行します。サーバー側では定期実行 CLI を 1 時間ごとに起動し、DB設定を見て実行可否を判断します。</p>
              <div className="mt-6 space-y-4">
                <label className="flex items-center gap-3 text-sm font-medium">
                  <input type="checkbox" checked={syncForm.enabled} onChange={(e) => setSyncForm((prev) => ({ ...prev, enabled: e.target.checked }))} />
                  月次更新を有効にする
                </label>
                <div className="grid gap-4 sm:grid-cols-3">
                  <label className="space-y-2 text-sm">
                    <span className="font-medium">日</span>
                    <input className="h-11 w-full rounded-2xl border bg-input-background px-3" min={1} max={31} type="number" value={syncForm.dayOfMonth} onChange={(e) => setSyncForm((prev) => ({ ...prev, dayOfMonth: Number(e.target.value) || 1 }))} />
                  </label>
                  <label className="space-y-2 text-sm">
                    <span className="font-medium">時</span>
                    <input className="h-11 w-full rounded-2xl border bg-input-background px-3" min={0} max={23} type="number" value={syncForm.hour} onChange={(e) => setSyncForm((prev) => ({ ...prev, hour: Number(e.target.value) || 0 }))} />
                  </label>
                  <label className="space-y-2 text-sm">
                    <span className="font-medium">分</span>
                    <input className="h-11 w-full rounded-2xl border bg-input-background px-3" min={0} max={59} type="number" value={syncForm.minute} onChange={(e) => setSyncForm((prev) => ({ ...prev, minute: Number(e.target.value) || 0 }))} />
                  </label>
                </div>
                <label className="space-y-2 text-sm">
                  <span className="font-medium">更新対象</span>
                  <select className="h-11 w-full rounded-2xl border bg-input-background px-3" value={syncForm.sourceScope} onChange={(e) => setSyncForm((prev) => ({ ...prev, sourceScope: e.target.value as any }))}>
                    <option value="all">全ソース</option>
                    <option value="mine-city">美祢市例規</option>
                    <option value="egov">地方自治法</option>
                    <option value="local-public-service">地方公務員法</option>
                  </select>
                </label>
                <button className="inline-flex h-11 items-center justify-center rounded-2xl bg-primary px-4 font-semibold text-primary-foreground disabled:opacity-60" disabled={busy} onClick={() => void saveSyncSettings()}>
                  {busy ? '保存中…' : '設定を保存'}
                </button>
              </div>
            </div>
            <div className="space-y-6">
              <div className="rounded-3xl border bg-card p-6 shadow-sm">
                <div className="flex items-center gap-2">
                  <RefreshCw className="size-5 text-primary" />
                  <h2 className="text-xl font-semibold">手動同期</h2>
                </div>
                <p className="mt-2 text-sm text-muted-foreground">
                  会議録のみ差分同期は、元データWebページからPDF一覧を収集し、追加または内容変更されたPDFだけを抽出し直します。
                </p>
                <div className="mt-4 grid gap-4 lg:grid-cols-[minmax(0,1fr)_15rem]">
                  <div className="rounded-3xl border bg-background/70 p-3">
                    <div className="flex items-center justify-between gap-3 px-1">
                      <p className="text-sm font-semibold">個別同期</p>
                      <p className="text-xs text-muted-foreground">必要なデータだけ更新</p>
                    </div>
                    <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
                      <button className="inline-flex h-14 flex-col items-center justify-center rounded-2xl border bg-card px-4 text-sm font-semibold leading-tight hover:border-primary/40 hover:bg-accent disabled:opacity-60" disabled={busy} onClick={() => void triggerSync('mine-city')}>
                        <span>美祢市例規</span>
                        <span className="mt-0.5 text-[11px] font-medium text-muted-foreground">のみ</span>
                      </button>
                      <button className="inline-flex h-14 flex-col items-center justify-center rounded-2xl border bg-card px-4 text-sm font-semibold leading-tight hover:border-primary/40 hover:bg-accent disabled:opacity-60" disabled={busy} onClick={() => void triggerSync('egov')}>
                        <span>地方自治法</span>
                        <span className="mt-0.5 text-[11px] font-medium text-muted-foreground">のみ</span>
                      </button>
                      <button className="inline-flex h-14 flex-col items-center justify-center rounded-2xl border bg-card px-4 text-sm font-semibold leading-tight hover:border-primary/40 hover:bg-accent disabled:opacity-60" disabled={busy} onClick={() => void triggerSync('local-public-service')}>
                        <span>地方公務員法</span>
                        <span className="mt-0.5 text-[11px] font-medium text-muted-foreground">のみ</span>
                      </button>
                      <button
                        className="inline-flex h-14 flex-col items-center justify-center rounded-2xl border border-primary/30 bg-primary/5 px-4 text-sm font-semibold leading-tight text-primary hover:bg-primary/10 disabled:opacity-60"
                        disabled={busy || minutesSyncing || Boolean(runningMinutesRun)}
                        onClick={() => void triggerMinutesSync(0)}
                      >
                        <span>会議録</span>
                        <span className="mt-0.5 text-[11px] font-semibold text-primary/80">差分同期</span>
                      </button>
                    </div>
                  </div>
                  <div className="rounded-3xl border border-primary/20 bg-primary/5 p-3">
                    <p className="px-1 text-sm font-semibold text-primary">一括同期</p>
                    <button className="mt-3 inline-flex h-14 w-full flex-col items-center justify-center rounded-2xl bg-primary px-4 text-sm font-semibold leading-tight text-primary-foreground shadow-sm shadow-primary/20 hover:bg-primary/90 disabled:opacity-60" disabled={busy} onClick={() => void triggerSync('all')}>
                      <span>すべて</span>
                      <span className="mt-0.5 text-[11px] font-semibold text-primary-foreground/85">同期</span>
                    </button>
                  </div>
                </div>
                <dl className="mt-5 space-y-2 text-sm text-muted-foreground">
                  <div className="flex justify-between gap-4"><dt>最終開始</dt><dd>{formatDateTime(syncStatus.lastStartedAt)}</dd></div>
                  <div className="flex justify-between gap-4"><dt>最終完了</dt><dd>{formatDateTime(syncStatus.lastFinishedAt)}</dd></div>
                  <div className="flex justify-between gap-4"><dt>最終成功</dt><dd>{formatDateTime(syncStatus.lastSuccessAt)}</dd></div>
                  <div className="flex justify-between gap-4"><dt>タイムゾーン</dt><dd>{syncStatus.timezone}</dd></div>
                </dl>
                <ProgressMeter title="手動同期の進捗" run={runningSyncRun} />
                <ProgressMeter title="会議録差分同期の進捗" run={runningMinutesRun} />
                {syncStatus.lastError ? <p className="mt-4 rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{syncStatus.lastError}</p> : null}
              </div>
              <div className="rounded-3xl border bg-card p-6 shadow-sm">
                <div className="flex items-center gap-2">
                  <RefreshCw className="size-5 text-primary" />
                  <h2 className="text-xl font-semibold">検索再構築</h2>
                </div>
                <p className="mt-2 text-sm text-muted-foreground">既存の全例規を対象に検索インデックスを張り直します。検索ロジック更新後の反映や、取りこぼし調整後の再構築に使用します。</p>
                <div className="mt-4 flex flex-wrap gap-3">
                  <button className="inline-flex h-11 items-center justify-center rounded-2xl bg-primary px-4 font-semibold text-primary-foreground disabled:opacity-60" disabled={busy} onClick={() => void triggerReindex()}>
                    全件再索引を実行
                  </button>
                </div>
                <ProgressMeter title="検索再構築の進捗" run={runningReindexRun} />
              </div>
              <div className="rounded-3xl border bg-card p-6 shadow-sm">
                <div className="flex items-center gap-2">
                  <RefreshCw className="size-5 text-primary" />
                  <h2 className="text-xl font-semibold">関連語辞書更新</h2>
                </div>
                <p className="mt-2 text-sm text-muted-foreground">日本語 WordNet の最新版取得と、既存DB（例規・法令・会議録）からの関連語再作成を実行します。検索・質問・会議録の関連語検索で共通利用します。</p>
                <div className="mt-4 flex flex-wrap gap-3">
                  <button
                    className="inline-flex h-11 items-center justify-center rounded-2xl bg-primary px-4 font-semibold text-primary-foreground disabled:opacity-60"
                    disabled={busy || Boolean(runningDictionaryRun) || Boolean(runningMinutesDictionaryRun)}
                    onClick={() => void triggerDictionaryUpdate()}
                  >
                    最新辞書を取得して再作成
                  </button>
                  <button
                    className="inline-flex h-11 items-center justify-center rounded-2xl border border-primary/30 bg-background px-4 font-semibold text-primary hover:bg-accent disabled:opacity-60"
                    disabled={busy || Boolean(runningDictionaryRun) || Boolean(runningMinutesDictionaryRun)}
                    onClick={() => void triggerMinutesDictionaryUpdate()}
                  >
                    会議録から増分作成
                  </button>
                  <button
                    className="inline-flex h-11 items-center justify-center rounded-2xl border bg-background px-4 font-medium hover:bg-accent"
                    disabled={synonymLoading}
                    onClick={() => void loadSynonyms()}
                  >
                    件数を再読み込み
                  </button>
                </div>
                <div className="mt-4 grid gap-2 text-sm sm:grid-cols-2">
                  {synonymStats.length === 0 ? (
                    <p className="text-muted-foreground">辞書統計はまだ取得されていません。</p>
                  ) : synonymStats.map((item) => (
                    <div key={`${item.sourceType}-${item.sourceVersion}`} className="flex justify-between gap-3 rounded-2xl border bg-background px-3 py-2">
                      <span className="text-muted-foreground">{item.sourceType}{item.sourceVersion ? ` / ${item.sourceVersion}` : ''}</span>
                      <span className="font-semibold">{item.count.toLocaleString()}件</span>
                    </div>
                  ))}
                </div>
                <ProgressMeter title="関連語辞書更新の進捗" run={runningDictionaryRun} />
                <ProgressMeter title="会議録辞書作成の進捗" run={runningMinutesDictionaryRun} />
              </div>
              <div className="rounded-3xl border bg-card p-6 shadow-sm">
                <h2 className="text-xl font-semibold">同期履歴</h2>
                <div className="mt-4 space-y-3">
                  {syncRuns.length === 0 ? (
                    <p className="text-sm text-muted-foreground">履歴はありません。</p>
                  ) : (
                    syncRuns.slice(0, 3).map((run) => (
                      <div key={run.id} className="rounded-2xl border bg-background p-4 text-sm">
                        <div className="flex items-center justify-between gap-3">
                          <span className="font-medium">{syncRunLabel(run)}</span>
                          <span className={run.status === 'failed' ? 'text-red-600' : run.status === 'success' ? 'text-emerald-700' : 'text-amber-700'}>{run.status}</span>
                        </div>
                        <p className="mt-2 text-muted-foreground">開始: {formatDateTime(run.startedAt)}</p>
                        <p className="text-muted-foreground">終了: {formatDateTime(run.finishedAt)}</p>
                        {run.summary && Object.keys(run.summary).length > 0 ? (
                          <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
                            {run.summary.reindexed != null ? <span className="text-primary">再索引 {run.summary.reindexed}件</span> : null}
                            {run.summary.inserted != null ? <span className="text-primary">辞書登録 {Number(run.summary.inserted).toLocaleString()}件</span> : null}
                            {run.summary.wordnetPairs != null ? <span>WordNet {Number(run.summary.wordnetPairs).toLocaleString()}件</span> : null}
                            {run.summary.domainPairs != null ? <span>既存DB {Number(run.summary.domainPairs).toLocaleString()}件</span> : null}
                            {run.summary.minutesPairs != null ? <span>会議録候補 {Number(run.summary.minutesPairs).toLocaleString()}件</span> : null}
                            {run.summary.processed != null ? <span>処理 {Number(run.summary.processed).toLocaleString()}件</span> : null}
                            {run.summary.skipped != null ? <span>スキップ {Number(run.summary.skipped).toLocaleString()}件</span> : null}
                            {run.summary.added != null ? <span className="text-emerald-700">追加 {run.summary.added}件</span> : null}
                            {run.summary.updated != null ? <span className="text-amber-700">更新 {run.summary.updated}件</span> : null}
                            {run.summary.unchanged != null ? <span>変更なし {run.summary.unchanged}件</span> : null}
                            {run.summary.articles != null ? <span>条文合計 {run.summary.articles}件</span> : null}
                          </div>
                        ) : null}
                        {run.errorText ? <p className="mt-2 text-red-600">{run.errorText}</p> : null}
                      </div>
                    ))
                  )}
                </div>
              </div>
            </div>
            {/* キャッシュ管理 */}
            <div className="rounded-3xl border bg-card p-6 shadow-sm">
              <h2 className="text-xl font-semibold">キャッシュ管理</h2>
              <p className="mt-2 text-sm text-muted-foreground">検索・質問キャッシュを手動でクリアします。同義語変更後などに使用してください。</p>
              <div className="mt-4 flex flex-wrap gap-3">
                <button
                  className="inline-flex h-9 items-center gap-1 rounded-xl border bg-background px-4 text-sm font-medium hover:bg-accent disabled:opacity-60"
                  disabled={busy}
                  onClick={() => void handleClearCache('search')}
                >
                  検索キャッシュをクリア
                </button>
                <button
                  className="inline-flex h-9 items-center gap-1 rounded-xl border bg-background px-4 text-sm font-medium hover:bg-accent disabled:opacity-60"
                  disabled={busy}
                  onClick={() => void handleClearCache('ask')}
                >
                  質問キャッシュをクリア
                </button>
                <button
                  className="inline-flex h-9 items-center gap-1 rounded-xl bg-red-50 border border-red-200 px-4 text-sm font-medium text-red-700 hover:bg-red-100 disabled:opacity-60"
                  disabled={busy}
                  onClick={() => void handleClearCache('all')}
                >
                  すべてクリア
                </button>
              </div>
            </div>

            {/* 同義語管理 */}
            <div className="rounded-3xl border bg-card p-6 shadow-sm">
              <h2 className="text-xl font-semibold">同義語管理</h2>
              <p className="mt-2 text-sm text-muted-foreground">検索時に展開される同義語ペアを管理します。追加・削除するとキャッシュがリセットされます。</p>
              <div className="mt-4 flex gap-2">
                <input
                  className="h-9 flex-1 rounded-xl border bg-input-background px-3 text-sm"
                  placeholder="正規語（例: 地方自治法）"
                  value={newSynonymCanonical}
                  onChange={(e) => setNewSynonymCanonical(e.target.value)}
                />
                <input
                  className="h-9 flex-1 rounded-xl border bg-input-background px-3 text-sm"
                  placeholder="同義語（例: 自治法）"
                  value={newSynonymTerm}
                  onChange={(e) => setNewSynonymTerm(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter') void handleAddSynonym(); }}
                />
                <button
                  className="h-9 rounded-xl bg-primary px-4 text-sm font-semibold text-primary-foreground disabled:opacity-60"
                  disabled={!newSynonymCanonical.trim() || !newSynonymTerm.trim()}
                  onClick={() => void handleAddSynonym()}
                >
                  追加
                </button>
              </div>
              {synonymLoading ? (
                <p className="mt-4 text-sm text-muted-foreground">読み込み中…</p>
              ) : (
                <div className="mt-4 max-h-72 overflow-auto space-y-1">
                  {synonymItems.length === 0 ? (
                    <p className="text-sm text-muted-foreground">同義語はありません。</p>
                  ) : (
                    synonymItems.map((s) => (
                      <div key={s.id} className="flex items-center justify-between gap-3 rounded-xl border bg-background px-3 py-2 text-sm">
                        <span>
                          <span className="font-medium">{s.canonicalTerm}</span> ↔ <span>{s.synonymTerm}</span>
                          <span className="ml-2 rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground">{s.sourceType || 'manual'}</span>
                        </span>
                        <button type="button" onClick={() => void handleDeleteSynonym(s.id)} className="text-muted-foreground hover:text-red-600">
                          <Trash2 className="size-4" />
                        </button>
                      </div>
                    ))
                  )}
                </div>
              )}
            </div>
          </section>
        ) : null}

        {tab === 'bookmarks' ? (
          <section className="space-y-4">
            <div className="rounded-3xl border bg-card p-6 shadow-sm">
              <div className="flex items-center gap-2">
                <Bookmark className="size-5 text-primary" />
                <h2 className="text-xl font-semibold">ブックマーク</h2>
              </div>
              {bookmarksLoading ? (
                <p className="mt-4 text-sm text-muted-foreground">読み込み中…</p>
              ) : bookmarkIds.length === 0 ? (
                <p className="mt-4 text-sm text-muted-foreground">ブックマークはありません。閲覧・検索画面の★ボタンで追加できます。</p>
              ) : (
                <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                  {bookmarkDocs.map((doc) => (
                    <div key={doc.id} className="rounded-2xl border bg-background p-4 text-sm">
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <p className="text-xs text-muted-foreground">{sourceLabel(doc.source)} / {doc.lawType || '例規'}</p>
                          <p className="mt-1 font-semibold leading-snug">{doc.title}</p>
                          {doc.lawNumber ? <p className="mt-0.5 text-xs text-muted-foreground">{doc.lawNumber}</p> : null}
                        </div>
                        <button type="button" onClick={() => toggleBookmark(doc.id)} className="text-amber-500 hover:text-muted-foreground shrink-0">
                          <Star className="size-4 fill-amber-400" />
                        </button>
                      </div>
                      <div className="mt-3 flex gap-2">
                        <button
                          type="button"
                          className="rounded-lg border px-2 py-1 text-xs hover:bg-accent"
                          onClick={() => { setSelectedDocId(doc.id); setTab('search'); }}
                        >
                          検索で開く
                        </button>
                        <button
                          type="button"
                          className="rounded-lg border px-2 py-1 text-xs hover:bg-accent"
                          onClick={() => { setBrowseDocId(doc.id); setTab('browse'); }}
                        >
                          閲覧で開く
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </section>
        ) : null}

        {/* 変更履歴モーダル */}
        {historyDoc ? (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={() => { setHistoryDoc(null); setDiffEntry(null); }}>
            <div className="w-full max-w-3xl rounded-3xl border bg-card p-6 shadow-xl" onClick={(e) => e.stopPropagation()}>
              <div className="flex items-center justify-between gap-3">
                <h2 className="text-lg font-semibold">変更履歴: {historyDoc.title}</h2>
                <button type="button" onClick={() => { setHistoryDoc(null); setDiffEntry(null); }} className="text-muted-foreground hover:text-foreground">
                  <X className="size-5" />
                </button>
              </div>
              <div className={`mt-4 space-y-2 overflow-auto ${diffEntry ? 'max-h-48' : 'max-h-96'}`}>
                {historyLoading ? (
                  <p className="text-sm text-muted-foreground">読み込み中…</p>
                ) : docHistory.length === 0 ? (
                  <p className="text-sm text-muted-foreground">変更履歴がありません。同期実行後に記録されます。</p>
                ) : (
                  docHistory.map((h) => (
                    <div key={h.id} className={`rounded-2xl border bg-background p-3 text-sm transition ${diffEntry?.id === h.id ? 'border-primary' : ''}`}>
                      <div className="flex items-center justify-between gap-3">
                        <div className="flex items-center gap-3">
                          <span className="font-medium">{h.changedAt ? h.changedAt.slice(0, 16).replace('T', ' ') : '日時不明'}</span>
                          <span className="text-xs font-mono text-muted-foreground">{h.contentHash.slice(0, 8)}</span>
                        </div>
                        <button
                          type="button"
                          disabled={diffLoading}
                          onClick={() => {
                            if (diffEntry?.id === h.id) { setDiffEntry(null); return; }
                            void openDiffView(h, historyDoc.id);
                          }}
                          className="inline-flex items-center gap-1 rounded-lg border px-2 py-1 text-xs hover:bg-accent disabled:opacity-50"
                        >
                          {diffLoading && diffEntry == null ? '読込中…' : diffEntry?.id === h.id ? '▲ 閉じる' : '全文を見る'}
                        </button>
                      </div>
                      {h.lawNumber ? <p className="mt-1 text-muted-foreground">{h.lawNumber}</p> : null}
                      {h.updatedAtSource ? <p className="mt-0.5 text-xs text-muted-foreground">原文更新: {h.updatedAtSource}</p> : null}
                    </div>
                  ))
                )}
              </div>
              {/* 差分ビュー */}
              {diffEntry ? (
                <div className="mt-4 border-t pt-4">
                  <div className="mb-2 flex items-center justify-between gap-3">
                    <p className="text-sm font-semibold">
                      {historyDoc.currentFullText ? '差分ビュー（旧版 → 現在）' : '全文（この版）'}
                      <span className="ml-2 text-xs font-normal text-muted-foreground">{diffEntry.changedAt ? diffEntry.changedAt.slice(0, 16).replace('T', ' ') : ''}</span>
                    </p>
                    {historyDoc.currentFullText ? (
                      <div className="flex items-center gap-3 text-xs">
                        <span className="inline-flex items-center gap-1"><span className="inline-block h-3 w-3 rounded-sm bg-red-100 border border-red-300" />削除</span>
                        <span className="inline-flex items-center gap-1"><span className="inline-block h-3 w-3 rounded-sm bg-emerald-100 border border-emerald-300" />追加</span>
                      </div>
                    ) : null}
                  </div>
                  <div className="max-h-72 overflow-auto rounded-2xl border bg-background p-3 font-mono text-xs leading-6">
                    {historyDoc.currentFullText ? (
                      (() => {
                        const diffLines = computeDiff(diffEntry.fullText || '', historyDoc.currentFullText);
                        const hasChanges = diffLines.some((l) => l.type !== 'same');
                        if (!hasChanges) {
                          return <p className="text-muted-foreground italic">現在版と同じ内容です。</p>;
                        }
                        return diffLines.map((line, idx) => {
                          if (line.type === 'same' && !line.text.trim()) return null;
                          return (
                            <div
                              key={idx}
                              className={
                                line.type === 'del'
                                  ? 'bg-red-50 text-red-700 px-2 rounded'
                                  : line.type === 'add'
                                  ? 'bg-emerald-50 text-emerald-700 px-2 rounded'
                                  : 'px-2 text-muted-foreground'
                              }
                            >
                              <span className="mr-2 select-none opacity-50">{line.type === 'del' ? '−' : line.type === 'add' ? '+' : ' '}</span>
                              {line.text || <span className="opacity-30">（空行）</span>}
                            </div>
                          );
                        });
                      })()
                    ) : (
                      <pre className="whitespace-pre-wrap text-foreground">{diffEntry.fullText || '（全文なし）'}</pre>
                    )}
                  </div>
                </div>
              ) : null}
            </div>
          </div>
        ) : null}
      </main>

      {/* 印刷用スタイル */}
      <style>{`
        @media print {
          .no-print, nav, header { display: none !important; }
          .print\\:max-h-none { max-height: none !important; }
          body { background: white; }
        }
      `}</style>
    </div>
  );
}

export default function App() {
  return <AppShell />;
}
