import { useEffect, useMemo, useRef, useState } from 'react';
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
  fetchSyncRuns,
  fetchSyncStatus,
  fetchSynonyms,
  runSync,
  searchLaws,
  searchLawsForRelated,
  updateSyncSettings,
} from './api';
import { fetchAuthConfig, fetchMe, login, logout } from './authApi';
import type { AnalyticsData, AskCandidateGroup, AskResponse, AuthUser, DocHistoryItem, DocumentDetail, DocumentSummary, RevisionItem, SearchField, SearchResult, SyncRun, SyncStatus, SynonymItem } from './types';
import { ArticleContent } from './ArticleContent';

const TABS = [
  { id: 'dashboard', label: 'ダッシュボード', icon: Database },
  { id: 'browse', label: '閲覧', icon: BookOpen },
  { id: 'search', label: '例規検索', icon: Search },
  { id: 'ask', label: '質問', icon: FileSearch },
  { id: 'bookmarks', label: 'ブックマーク', icon: Bookmark },
  { id: 'settings', label: '同期設定', icon: Settings2 },
] as const;

const SEARCH_HISTORY_KEY = 'reiki_search_history';
const BOOKMARKS_KEY = 'reiki_bookmarks';
type BrowseSource = 'mine-city' | 'egov';
type BrowseTreeNode = {
  key: string;
  label: string;
  children: BrowseTreeNode[];
  docs: DocumentSummary[];
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

type TabId = (typeof TABS)[number]['id'];

type SyncForm = {
  enabled: boolean;
  dayOfMonth: number;
  hour: number;
  minute: number;
  sourceScope: 'all' | 'mine-city' | 'egov';
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
  mineCityLatestRevisions: [],
  egovLatestRevisions: [],
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

function sourceLabel(source: string): string {
  if (source === 'mine-city') return '美祢市例規';
  if (source === 'egov') return '地方自治法';
  return '全ソース';
}

function normalizeOrderText(value: string | null | undefined): string {
  return (value || '').normalize('NFKC').replace(/\s+/g, ' ').trim();
}

function compareOrderText(a: string | null | undefined, b: string | null | undefined): number {
  return ORDER_COLLATOR.compare(normalizeOrderText(a), normalizeOrderText(b));
}

function compareDocumentSummary(a: DocumentSummary, b: DocumentSummary): number {
  const byLawNumber = compareOrderText(a.lawNumber, b.lawNumber);
  if (byLawNumber !== 0) return byLawNumber;
  const byTitle = compareOrderText(a.title, b.title);
  if (byTitle !== 0) return byTitle;
  return a.id - b.id;
}

function buildBrowseTree(source: BrowseSource, docs: DocumentSummary[]): BrowseTreeNode[] {
  if (source === 'egov') {
    return [
      {
        key: 'egov-root',
        label: '地方自治法',
        children: [],
        docs: [...docs].sort(compareDocumentSummary),
      },
    ];
  }

  const root: BrowseTreeNode = { key: 'root', label: 'root', children: [], docs: [] };
  const childIndex = new Map<string, BrowseTreeNode>();

  for (const doc of docs) {
    const parts = (doc.categoryPath || '未分類')
      .split(/\s*\/\s*/)
      .map((part) => part.trim())
      .filter(Boolean);
    let current = root;
    let currentPath = '';

    for (const part of parts) {
      currentPath = currentPath ? `${currentPath}/${part}` : part;
      const key = `${current.key}>${part}`;
      let next = childIndex.get(key);
      if (!next) {
        next = { key: currentPath, label: part, children: [], docs: [] };
        current.children.push(next);
        childIndex.set(key, next);
      }
      current = next;
    }
    current.docs.push(doc);
  }

  const sortTree = (nodes: BrowseTreeNode[]) => {
    nodes.sort((a, b) => compareOrderText(a.label, b.label));
    for (const node of nodes) {
      node.docs.sort(compareDocumentSummary);
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
  const [searchSource, setSearchSource] = useState<'all' | 'mine-city' | 'egov'>('all');
  const [searching, setSearching] = useState(false);
  const [results, setResults] = useState<SearchResult[]>([]);
  const [searchTotal, setSearchTotal] = useState(0);
  const [searchPage, setSearchPage] = useState(0);
  const [selectedDoc, setSelectedDoc] = useState<DocumentDetail | null>(null);
  const [selectedDocId, setSelectedDocId] = useState<number | null>(null);

  const [searchLawType, setSearchLawType] = useState('');
  const [searchFromDate, setSearchFromDate] = useState('');
  const [searchToDate, setSearchToDate] = useState('');
  const [lawTypeOptions, setLawTypeOptions] = useState<string[]>([]);
  const [showAdvancedFilter, setShowAdvancedFilter] = useState(false);

  const [relatedResults, setRelatedResults] = useState<SearchResult[]>([]);
  const [relatedLoading, setRelatedLoading] = useState(false);

  const [bookmarkIds, setBookmarkIds] = useState<number[]>(() => loadBookmarks());
  const [bookmarkDocs, setBookmarkDocs] = useState<DocumentDetail[]>([]);
  const [bookmarksLoading, setBookmarksLoading] = useState(false);

  const [analyticsData, setAnalyticsData] = useState<AnalyticsData | null>(null);
  const [synonymItems, setSynonymItems] = useState<SynonymItem[]>([]);
  const [synonymLoading, setSynonymLoading] = useState(false);
  const [newSynonymCanonical, setNewSynonymCanonical] = useState('');
  const [newSynonymTerm, setNewSynonymTerm] = useState('');

  const [historyDoc, setHistoryDoc] = useState<{ id: number; title: string; currentFullText?: string } | null>(null);
  const [docHistory, setDocHistory] = useState<DocHistoryItem[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [diffEntry, setDiffEntry] = useState<DocHistoryItem | null>(null);
  const [diffLoading, setDiffLoading] = useState(false);

  const searchHistoryRef = useRef<string[]>(loadSearchHistory());
  const [searchSuggest, setSearchSuggest] = useState<string[]>([]);
  const [showSuggest, setShowSuggest] = useState(false);

  const [browseSource, setBrowseSource] = useState<BrowseSource>('mine-city');
  const [browseList, setBrowseList] = useState<DocumentSummary[]>([]);
  const [browseLoading, setBrowseLoading] = useState(false);
  const [browseDocId, setBrowseDocId] = useState<number | null>(null);
  const [browseDoc, setBrowseDoc] = useState<DocumentDetail | null>(null);
  const [browseDocLoading, setBrowseDocLoading] = useState(false);

  const [question, setQuestion] = useState('');
  const [asking, setAsking] = useState(false);
  const [answer, setAnswer] = useState<AskResponse | null>(null);
  const [questionHistory, setQuestionHistory] = useState<string[]>([]);
  const [expandedArticleKeys, setExpandedArticleKeys] = useState<Set<string>>(new Set());

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
      const items = await fetchDocumentList(source);
      setBrowseList(items);
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
      const [status, runs] = await Promise.all([fetchSyncStatus(), fetchSyncRuns()]);
      setSyncStatus(status);
      setSyncRuns(runs);
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
  }, [tab]);

  useEffect(() => {
    if (selectedDocId == null) return;
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
      const items = await fetchSynonyms();
      setSynonymItems(items);
    } catch (err) {
      setGlobalError(err instanceof Error ? err.message : '同義語取得に失敗しました。');
    } finally {
      setSynonymLoading(false);
    }
  }

  async function handleAddSynonym() {
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

  async function handleDeleteSynonym(id: number) {
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
      });
      setResults(resp.items);
      setSearchTotal(resp.total);
      if (resp.items.length > 0) setSelectedDocId(resp.items[0].documentId);
      else {
        setSelectedDoc(null);
        setSelectedDocId(null);
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

  async function triggerSync(scope: 'all' | 'mine-city' | 'egov') {
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

  const statCards = useMemo(
    () => [
      {
        label: '例規数',
        total: `${syncStatus.documentCount.toLocaleString()}件`,
        rows: [
          { label: '美祢市例規', value: `${syncStatus.mineCityDocumentCount.toLocaleString()}件` },
          { label: '地方自治法', value: `${syncStatus.egovDocumentCount.toLocaleString()}件` },
        ],
      },
      {
        label: '条文数',
        total: `${syncStatus.articleCount.toLocaleString()}条`,
        rows: [
          { label: '美祢市例規', value: `${syncStatus.mineCityArticleCount.toLocaleString()}条` },
          { label: '地方自治法', value: `${syncStatus.egovArticleCount.toLocaleString()}条` },
        ],
      },
      {
        label: '同期実行',
        total: `${syncStatus.runCount.toLocaleString()}回`,
        rows: [{ label: '最終成功', value: formatDateTime(syncStatus.lastSuccessAt) }],
      },
    ],
    [syncStatus],
  );

  const browseTree = useMemo(() => buildBrowseTree(browseSource, browseList), [browseList, browseSource]);
  const renderBrowseTree = (nodes: BrowseTreeNode[], depth = 0): JSX.Element => (
    <div className={depth === 0 ? 'space-y-3' : 'mt-2 space-y-2'}>
      {nodes.map((node) => (
        <details
          key={node.key}
          open
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

  if (loading) {
    return <div className="min-h-screen bg-background p-8 text-muted-foreground">読み込み中…</div>;
  }
  if (authEnabled && !user) {
    return <LoginCard onLogin={handleLogin} />;
  }

  return (
    <div className="min-h-screen bg-background text-foreground">
      <PortalHeader
        title="美祢市例規・自治法DB"
        subtitle="地方自治法と美祢市例規を横断検索し、他システムから参照できるようにします。"
        syncStatusText={syncBadgeText(syncStatus)}
        syncStatusTone={toneForStatus(syncStatus)}
        onOpenSettings={() => setTab('settings')}
        user={user}
        onLogout={handleLogout}
        authEnabled={Boolean(authEnabled)}
      />
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
                  <p>このシステムは、地方自治法と美祢市例規を条文単位で保存し、全文検索と簡易質問応答で参照できるようにするモジュールです。</p>
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
                    syncRuns.slice(0, 5).map((run) => (
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
            <section className="grid gap-6 lg:grid-cols-2">
              <RevisionPanel title="美祢市例規 — 最近の改定" items={syncStatus.mineCityLatestRevisions} />
              <RevisionPanel title="地方自治法 — 最近の改定" items={syncStatus.egovLatestRevisions} />
            </section>
            {analyticsData ? (
              <section className="rounded-3xl border bg-card p-6 shadow-sm">
                <div className="flex items-center gap-2">
                  <BarChart2 className="size-5 text-primary" />
                  <h2 className="text-xl font-semibold">利用統計</h2>
                </div>
                <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                  {[
                    { label: '検索キャッシュヒット', value: analyticsData.searchCacheHits.toLocaleString() },
                    { label: '検索キャッシュ件数', value: analyticsData.searchCacheEntries.toLocaleString() },
                    { label: '質問キャッシュヒット', value: analyticsData.askCacheHits.toLocaleString() },
                    { label: '質問キャッシュ件数', value: analyticsData.askCacheEntries.toLocaleString() },
                  ].map((s) => (
                    <div key={s.label} className="rounded-2xl border bg-background p-4">
                      <p className="text-xs text-muted-foreground">{s.label}</p>
                      <p className="mt-1 text-2xl font-semibold">{s.value}</p>
                    </div>
                  ))}
                </div>
                {analyticsData.topSearchQueries.length > 0 ? (
                  <div className="mt-4 grid gap-4 lg:grid-cols-2">
                    <div>
                      <p className="mb-2 text-sm font-semibold">検索ランキング</p>
                      <ol className="space-y-1">
                        {analyticsData.topSearchQueries.slice(0, 5).map((q, i) => (
                          <li key={i} className="flex items-center justify-between rounded-xl border bg-background px-3 py-2 text-sm">
                            <span className="text-muted-foreground">{i + 1}. {q.query}</span>
                            <span className="font-medium">{q.hits}回</span>
                          </li>
                        ))}
                      </ol>
                    </div>
                    {analyticsData.topAskQueries.length > 0 ? (
                      <div>
                        <p className="mb-2 text-sm font-semibold">質問ランキング</p>
                        <ol className="space-y-1">
                          {analyticsData.topAskQueries.slice(0, 5).map((q, i) => (
                            <li key={i} className="flex items-center justify-between rounded-xl border bg-background px-3 py-2 text-sm">
                              <span className="text-muted-foreground">{i + 1}. {q.query}</span>
                              <span className="font-medium">{q.hits}回</span>
                            </li>
                          ))}
                        </ol>
                      </div>
                    ) : null}
                  </div>
                ) : null}
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
                      : '地方自治法を条文番号順で閲覧できます。'}
                  </div>
                  {renderBrowseTree(browseTree)}
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
                    <a className="inline-flex h-10 shrink-0 items-center rounded-2xl border px-4 text-sm font-medium hover:bg-accent" href={browseDoc.sourceUrl} rel="noreferrer" target="_blank">
                      原文を開く
                    </a>
                  </div>
                  <div className="mt-6 grid gap-4 xl:grid-cols-[13rem_minmax(0,1fr)]">
                    <div className="max-h-[65vh] overflow-auto rounded-2xl border bg-background p-3">
                      <p className="mb-3 text-sm font-semibold">条文一覧</p>
                      <div className="space-y-1">
                        {browseDoc.articles.map((article) => (
                          <a key={article.id} className="block rounded-xl px-3 py-2 text-sm hover:bg-accent" href={`#barticle-${article.id}`}>
                            {article.articleNumber}{article.articleTitle ? `　${article.articleTitle}` : ''}
                          </a>
                        ))}
                      </div>
                    </div>
                    <div className="max-h-[65vh] overflow-auto rounded-2xl border bg-background p-5 print:max-h-none">
                      <div className="space-y-6">
                        {browseDoc.articles.length > 0 ? (
                          browseDoc.articles.map((article) => (
                            <article key={article.id} id={`barticle-${article.id}`} className="border-b pb-5 last:border-b-0">
                              <h3 className="text-lg font-semibold">{article.articleNumber}{article.articleTitle ? `　${article.articleTitle}` : ''}</h3>
                              <div className="mt-3"><ArticleContent text={article.text} /></div>
                            </article>
                          ))
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
                <p className="text-xs text-muted-foreground">スペース区切りで AND 検索。フィールド間は AND / OR で切り替え。</p>
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
                  {(searchLawType || searchFromDate || searchToDate) ? (
                    <button type="button" onClick={() => { setSearchLawType(''); setSearchFromDate(''); setSearchToDate(''); }} className="text-xs text-muted-foreground underline">フィルタをクリア</button>
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
                      <p className="text-sm text-muted-foreground">全{searchTotal}件中 {searchPage * 20 + 1}〜{Math.min(searchTotal, searchPage * 20 + results.length)}件</p>
                      {searchTotal >= 20 ? (
                        <p className="text-sm text-amber-600">件数が多いため、キーワードを追加して絞り込んでください。</p>
                      ) : null}
                    </div>
                    {results.map((item, idx) => (
                      <button
                        key={`${item.documentId}-${item.articleId ?? 'doc'}-${idx}`}
                        type="button"
                        onClick={() => setSelectedDocId(item.documentId)}
                        className={`w-full rounded-2xl border p-4 text-left transition ${selectedDocId === item.documentId ? 'border-primary bg-primary/5' : 'bg-background hover:bg-accent/40'}`}
                      >
                        <div className="flex items-start justify-between gap-3">
                          <div>
                            <p className="text-xs text-muted-foreground">{sourceLabel(item.source)} / {item.lawType || '例規'}</p>
                            <p className="mt-1 font-semibold">{item.title}</p>
                            {item.articleNumber ? <p className="mt-1 text-sm text-primary">{item.articleNumber}{item.articleTitle ? ` ${item.articleTitle}` : ''}</p> : null}
                          </div>
                          <span className="rounded-full bg-accent px-2 py-1 text-xs text-muted-foreground">score {item.score}</span>
                        </div>
                        {item.matchReasons && item.matchReasons.length > 0 ? (
                          <div className="mt-2 flex flex-wrap gap-1">
                            {item.matchReasons.map((r) => (
                              <span key={r} className="rounded-full bg-primary/10 px-2 py-0.5 text-xs text-primary">📌 {r}</span>
                            ))}
                          </div>
                        ) : null}
                        <p className="mt-1 text-sm text-muted-foreground">{item.categoryPath || '分類なし'}</p>
                      </button>
                    ))}
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
                    <a className="inline-flex h-10 items-center rounded-2xl border px-4 text-sm font-medium hover:bg-accent" href={selectedDoc.sourceUrl} rel="noreferrer" target="_blank">
                      原文を開く
                    </a>
                  </div>
                  <div className="mt-6 grid gap-4 xl:grid-cols-[12rem_minmax(0,1fr)]">
                    <div className="max-h-[70vh] overflow-auto rounded-2xl border bg-background p-3">
                      <p className="mb-3 text-sm font-semibold">条文一覧</p>
                      <div className="space-y-2">
                        {selectedDoc.articles.map((article) => (
                          <a key={article.id} className="block rounded-xl px-3 py-2 text-sm hover:bg-accent" href={`#article-${article.id}`}>
                            {article.articleNumber}
                          </a>
                        ))}
                      </div>
                    </div>
                    <div className="max-h-[70vh] overflow-auto rounded-2xl border bg-background p-5">
                      <div className="space-y-6">
                        {selectedDoc.articles.length > 0 ? (
                          selectedDoc.articles.map((article) => (
                            <article key={article.id} id={`article-${article.id}`} className="border-b pb-5 last:border-b-0">
                              <h3 className="text-lg font-semibold">{article.articleNumber}{article.articleTitle ? ` ${article.articleTitle}` : ''}</h3>
                              <div className="mt-3"><ArticleContent text={article.text} keywords={searchFields.flatMap((f) => f.q.trim() ? f.q.trim().split(/\s+/) : [])} /></div>
                            </article>
                          ))
                        ) : (
                          <ArticleContent text={selectedDoc.fullText} />
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
                              onClick={() => setSelectedDocId(r.documentId)}
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
              <h2 className="text-xl font-semibold">月次更新設定</h2>
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
                <div className="mt-4 grid gap-3 sm:grid-cols-3">
                  <button className="inline-flex h-11 items-center justify-center rounded-2xl border bg-background px-4 font-medium hover:bg-accent" disabled={busy} onClick={() => void triggerSync('mine-city')}>美祢市例規のみ</button>
                  <button className="inline-flex h-11 items-center justify-center rounded-2xl border bg-background px-4 font-medium hover:bg-accent" disabled={busy} onClick={() => void triggerSync('egov')}>地方自治法のみ</button>
                  <button className="inline-flex h-11 items-center justify-center rounded-2xl bg-primary px-4 font-semibold text-primary-foreground disabled:opacity-60" disabled={busy} onClick={() => void triggerSync('all')}>すべて同期</button>
                </div>
                <dl className="mt-5 space-y-2 text-sm text-muted-foreground">
                  <div className="flex justify-between gap-4"><dt>最終開始</dt><dd>{formatDateTime(syncStatus.lastStartedAt)}</dd></div>
                  <div className="flex justify-between gap-4"><dt>最終完了</dt><dd>{formatDateTime(syncStatus.lastFinishedAt)}</dd></div>
                  <div className="flex justify-between gap-4"><dt>最終成功</dt><dd>{formatDateTime(syncStatus.lastSuccessAt)}</dd></div>
                  <div className="flex justify-between gap-4"><dt>タイムゾーン</dt><dd>{syncStatus.timezone}</dd></div>
                </dl>
                {syncStatus.lastError ? <p className="mt-4 rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{syncStatus.lastError}</p> : null}
              </div>
              <div className="rounded-3xl border bg-card p-6 shadow-sm">
                <h2 className="text-xl font-semibold">同期履歴</h2>
                <div className="mt-4 space-y-3">
                  {syncRuns.length === 0 ? (
                    <p className="text-sm text-muted-foreground">履歴はありません。</p>
                  ) : (
                    syncRuns.map((run) => (
                      <div key={run.id} className="rounded-2xl border bg-background p-4 text-sm">
                        <div className="flex items-center justify-between gap-3">
                          <span className="font-medium">{run.runType === 'scheduled' ? '定期同期' : '手動同期'}</span>
                          <span className={run.status === 'failed' ? 'text-red-600' : run.status === 'success' ? 'text-emerald-700' : 'text-amber-700'}>{run.status}</span>
                        </div>
                        <p className="mt-2 text-muted-foreground">開始: {formatDateTime(run.startedAt)}</p>
                        <p className="text-muted-foreground">終了: {formatDateTime(run.finishedAt)}</p>
                        {run.summary && Object.keys(run.summary).length > 0 ? (
                          <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
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
                        <span><span className="font-medium">{s.canonicalTerm}</span> ↔ <span>{s.synonymTerm}</span></span>
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
