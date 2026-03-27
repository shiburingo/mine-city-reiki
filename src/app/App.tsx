import { useEffect, useMemo, useState } from 'react';
import { BookMarked, Database, FileSearch, RefreshCw, Search, Settings2 } from 'lucide-react';
import { PortalHeader } from '@mine-troutfarm/ui';
import {
  askQuestion,
  fetchDocumentDetail,
  fetchSyncRuns,
  fetchSyncStatus,
  runSync,
  searchLaws,
  updateSyncSettings,
} from './api';
import { fetchAuthConfig, fetchMe, login, logout } from './authApi';
import type { AskResponse, AuthUser, DocumentDetail, SearchResult, SyncRun, SyncStatus } from './types';

const TABS = [
  { id: 'dashboard', label: 'ダッシュボード', icon: Database },
  { id: 'search', label: '例規検索', icon: Search },
  { id: 'ask', label: '質問', icon: FileSearch },
  { id: 'settings', label: '同期設定', icon: Settings2 },
] as const;

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

  const [searchText, setSearchText] = useState('');
  const [searchSource, setSearchSource] = useState<'all' | 'mine-city' | 'egov'>('all');
  const [searching, setSearching] = useState(false);
  const [results, setResults] = useState<SearchResult[]>([]);
  const [selectedDoc, setSelectedDoc] = useState<DocumentDetail | null>(null);
  const [selectedDocId, setSelectedDocId] = useState<number | null>(null);

  const [question, setQuestion] = useState('');
  const [asking, setAsking] = useState(false);
  const [answer, setAnswer] = useState<AskResponse | null>(null);

  const [syncForm, setSyncForm] = useState<SyncForm>({
    enabled: false,
    dayOfMonth: 1,
    hour: 3,
    minute: 0,
    sourceScope: 'all',
  });

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

  useEffect(() => {
    void bootstrap();
  }, []);

  useEffect(() => {
    if (selectedDocId == null) return;
    let cancelled = false;
    void (async () => {
      try {
        const detail = await fetchDocumentDetail(selectedDocId);
        if (!cancelled) setSelectedDoc(detail);
      } catch (err) {
        if (!cancelled) setGlobalError(err instanceof Error ? err.message : '条文取得に失敗しました。');
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [selectedDocId]);

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

  async function submitSearch() {
    if (!searchText.trim()) return;
    setSearching(true);
    setGlobalError(null);
    try {
      const items = await searchLaws({ q: searchText.trim(), source: searchSource, limit: 40 });
      setResults(items);
      if (items.length > 0) setSelectedDocId(items[0].documentId);
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

  async function submitQuestion() {
    if (!question.trim()) return;
    setAsking(true);
    setGlobalError(null);
    try {
      const resp = await askQuestion(question.trim());
      setAnswer(resp);
      if (resp.candidates[0]) setSelectedDocId(resp.candidates[0].documentId);
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
      { label: '例規数', value: `${syncStatus.documentCount.toLocaleString()}件`, note: '条例・規則・地方自治法を含む' },
      { label: '条文数', value: `${syncStatus.articleCount.toLocaleString()}条`, note: '条文単位で検索対象化' },
      { label: '同期実行', value: `${syncStatus.runCount.toLocaleString()}回`, note: `最終: ${formatDateTime(syncStatus.lastSuccessAt)}` },
    ],
    [syncStatus],
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
        <div className="grid gap-4 md:grid-cols-3">
          {statCards.map((card) => (
            <section key={card.label} className="rounded-3xl border bg-card p-5 shadow-sm">
              <p className="text-sm text-muted-foreground">{card.label}</p>
              <p className="mt-2 text-3xl font-semibold">{card.value}</p>
              <p className="mt-2 text-sm text-muted-foreground">{card.note}</p>
            </section>
          ))}
        </div>

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

        {globalError ? <div className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{globalError}</div> : null}

        {tab === 'dashboard' ? (
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
        ) : null}

        {tab === 'search' ? (
          <section className="grid gap-6 lg:grid-cols-[1fr_1.2fr]">
            <div className="space-y-4 rounded-3xl border bg-card p-6 shadow-sm">
              <h2 className="text-xl font-semibold">例規検索</h2>
              <div className="flex gap-3">
                <input
                  className="h-11 flex-1 rounded-2xl border bg-input-background px-4"
                  placeholder="例: 会計年度任用職員 休暇 条例"
                  value={searchText}
                  onChange={(e) => setSearchText(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') void submitSearch();
                  }}
                />
                <select className="h-11 rounded-2xl border bg-input-background px-3" value={searchSource} onChange={(e) => setSearchSource(e.target.value as any)}>
                  <option value="all">全ソース</option>
                  <option value="mine-city">美祢市例規</option>
                  <option value="egov">地方自治法</option>
                </select>
                <button className="inline-flex h-11 items-center justify-center rounded-2xl bg-primary px-4 font-semibold text-primary-foreground" disabled={searching} onClick={() => void submitSearch()}>
                  {searching ? '検索中…' : '検索'}
                </button>
              </div>
              <div className="space-y-3">
                {results.length === 0 ? (
                  <p className="text-sm text-muted-foreground">キーワードを入力して検索してください。</p>
                ) : (
                  results.map((item, idx) => (
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
                      <p className="mt-2 text-sm text-muted-foreground">{item.categoryPath || '分類なし'}</p>
                      <p className="mt-2 text-sm leading-6">{item.snippet}</p>
                    </button>
                  ))
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
                    </div>
                    <a className="inline-flex h-10 items-center rounded-2xl border px-4 text-sm font-medium hover:bg-accent" href={selectedDoc.sourceUrl} rel="noreferrer" target="_blank">
                      原文を開く
                    </a>
                  </div>
                  <div className="mt-6 grid gap-4 xl:grid-cols-[16rem_1fr]">
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
                              <p className="mt-3 whitespace-pre-wrap text-sm leading-7">{article.text}</p>
                            </article>
                          ))
                        ) : (
                          <p className="whitespace-pre-wrap text-sm leading-7">{selectedDoc.fullText}</p>
                        )}
                      </div>
                    </div>
                  </div>
                </>
              )}
            </div>
          </section>
        ) : null}

        {tab === 'ask' ? (
          <section className="grid gap-6 lg:grid-cols-[0.9fr_1.1fr]">
            <div className="rounded-3xl border bg-card p-6 shadow-sm">
              <h2 className="text-xl font-semibold">簡易質問</h2>
              <p className="mt-2 text-sm text-muted-foreground">解釈を断定せず、関連する条例・条文候補を提示します。</p>
              <textarea
                className="mt-4 min-h-40 w-full rounded-2xl border bg-input-background p-4"
                placeholder="例: 会計年度任用職員の休暇はどの条例を見るべきですか"
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
              />
              <button className="mt-4 inline-flex h-11 items-center justify-center rounded-2xl bg-primary px-4 font-semibold text-primary-foreground" disabled={asking} onClick={() => void submitQuestion()}>
                {asking ? '照会中…' : '候補を表示'}
              </button>
            </div>
            <div className="rounded-3xl border bg-card p-6 shadow-sm">
              {!answer ? (
                <p className="text-sm text-muted-foreground">質問すると、関連性の高い条文候補を表示します。</p>
              ) : (
                <div className="space-y-5">
                  <div className="rounded-2xl border bg-background p-4">
                    <p className="text-sm text-muted-foreground">質問</p>
                    <p className="mt-2 font-medium">{answer.query}</p>
                    <p className="mt-4 text-sm leading-6">{answer.answerLead}</p>
                    {answer.keywords.length > 0 ? (
                      <div className="mt-4 flex flex-wrap gap-2">
                        {answer.keywords.map((keyword) => (
                          <span key={keyword} className="rounded-full border px-3 py-1 text-xs text-muted-foreground">{keyword}</span>
                        ))}
                      </div>
                    ) : null}
                  </div>
                  <div className="space-y-3">
                    {answer.candidates.map((candidate, idx) => (
                      <button
                        key={`${candidate.documentId}-${candidate.articleId ?? 'doc'}-${idx}`}
                        type="button"
                        onClick={() => {
                          setTab('search');
                          setSelectedDocId(candidate.documentId);
                        }}
                        className="w-full rounded-2xl border bg-background p-4 text-left hover:bg-accent/40"
                      >
                        <p className="text-xs text-muted-foreground">{sourceLabel(candidate.source)}</p>
                        <p className="mt-1 font-semibold">{candidate.title}</p>
                        {candidate.articleNumber ? <p className="mt-1 text-sm text-primary">{candidate.articleNumber}</p> : null}
                        <p className="mt-2 text-sm text-muted-foreground">{candidate.snippet}</p>
                      </button>
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
                          <div className="mt-2 grid gap-1 text-muted-foreground sm:grid-cols-2">
                            {Object.entries(run.summary).map(([key, value]) => (
                              <div key={key} className="flex justify-between gap-3"><span>{key}</span><span>{String(value)}</span></div>
                            ))}
                          </div>
                        ) : null}
                        {run.errorText ? <p className="mt-2 text-red-600">{run.errorText}</p> : null}
                      </div>
                    ))
                  )}
                </div>
              </div>
            </div>
          </section>
        ) : null}
      </main>
    </div>
  );
}

export default function App() {
  return <AppShell />;
}
