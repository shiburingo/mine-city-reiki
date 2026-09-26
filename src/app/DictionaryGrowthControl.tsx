import { useEffect, useState } from 'react';
import { Pause, Play } from 'lucide-react';
import { fetchDictionaryGrowthSettings, updateDictionaryGrowthSettings } from './api';
import type { DictionaryGrowthSettings } from './types';

export function DictionaryGrowthControl({ onEnabledChange, readOnly = false }: {
  onEnabledChange: (enabled: boolean | null) => void;
  readOnly?: boolean;
}) {
  const [settings, setSettings] = useState<DictionaryGrowthSettings | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [reload, setReload] = useState(0);

  useEffect(() => {
    let active = true;
    onEnabledChange(null);
    setError('');
    void fetchDictionaryGrowthSettings().then((value) => {
      if (!active) return;
      setSettings(value);
      onEnabledChange(value.enabled);
    }).catch(() => {
      if (active) setError('増強設定を取得できませんでした。再読み込みしてください。');
    });
    return () => { active = false; };
  }, [onEnabledChange, reload]);

  async function toggleGrowth() {
    if (!settings || pending || readOnly) return;
    setPending(true);
    setError('');
    setNotice('');
    try {
      const value = await updateDictionaryGrowthSettings(!settings.enabled);
      setSettings(value);
      onEnabledChange(value.enabled);
      setNotice(value.enabled
        ? '増強を再開しました。次回の自動更新から収集します。今すぐ手動で取り込むこともできます。'
        : '中断を保存しました。実行中の収集は安全な区切りで停止します。');
    } catch (err) {
      setError(err instanceof Error ? err.message : '増強設定を保存できませんでした。');
    } finally {
      setPending(false);
    }
  }

  return (
    <section className="mt-5 rounded-2xl border bg-background p-4 sm:p-5" aria-labelledby="dictionary-growth-title">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h3 id="dictionary-growth-title" className="font-semibold">辞書の増強</h3>
          <p className="mt-1 text-sm text-muted-foreground">
            {settings ? settings.enabled ? '収集を許可しています' : '収集を中断しています' : '設定を確認しています'}
          </p>
        </div>
        <button type="button" onClick={() => void toggleGrowth()} disabled={!settings || pending || readOnly || Boolean(error)}
          className="inline-flex min-h-11 items-center justify-center gap-2 rounded-xl border border-primary/30 bg-accent px-5 font-semibold text-accent-foreground hover:bg-muted focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary disabled:opacity-60">
          {settings?.enabled ? <Pause className="size-4" aria-hidden="true" /> : <Play className="size-4" aria-hidden="true" />}
          {pending ? '保存中…' : !settings ? error ? '状態未確認' : '確認中…' : settings.enabled ? '増強を中断' : '増強を再開'}
        </button>
      </div>
      <p className="mt-3 text-sm text-muted-foreground">
        中断中も、登録済みの辞書で検索できます。自動収集と手動取り込みを停止し、保存済みの巡回位置は保持します。
        実行中の通信や保存済みデータのコンパイルが完了するまで、少し時間がかかる場合があります。
      </p>
      <div className="mt-4 grid gap-3 border-t pt-4 text-sm sm:grid-cols-3">
        <p><span className="block font-semibold">同義語・表記揺れ優先</span><span className="text-muted-foreground">管理済みの言い換えと辞書由来の別名を優先</span></p>
        <p><span className="block font-semibold">自治体業務を重点収集</span><span className="text-muted-foreground">福祉・防災・行政手続き・地域の用語を補強</span></p>
        <p><span className="block font-semibold">関連語は最大{settings?.maxSearchAlternatives ?? 5}語</span><span className="text-muted-foreground">入力語ごとの展開数を保ち、検索の広がりすぎを防止</span></p>
      </div>
      {notice ? <p role="status" className="mt-3 text-sm">{notice}</p> : null}
      {error ? <div role="alert" className="mt-3 text-sm text-destructive">{error}<button type="button" className="ml-3 min-h-11 underline" onClick={() => { setSettings(null); setReload((value) => value + 1); }}>再読み込み</button></div> : null}
    </section>
  );
}
