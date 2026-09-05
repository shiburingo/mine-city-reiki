import { useEffect, useState, type ReactNode } from 'react';
import { fetchMinutesUtteranceText } from './api';
import type { MinutesSearchResult } from './types';

export function MinutesResultBody({ result, renderText }: {
  result: MinutesSearchResult;
  renderText: (text: string) => ReactNode;
}) {
  const [body, setBody] = useState<{ text?: string; error?: string }>({});
  const [retry, setRetry] = useState(0);
  const { dayId, id, textIsPreview } = result;

  useEffect(() => {
    if (!textIsPreview) return;
    let current = true;
    setBody({});
    fetchMinutesUtteranceText(dayId, id).then(
      (text) => { if (current) setBody({ text }); },
      () => { if (current) setBody({ error: '本文を取得できませんでした。' }); },
    );
    return () => { current = false; };
  }, [dayId, id, textIsPreview, retry]);

  if (!textIsPreview) return <>{renderText(result.text)}</>;
  if (body.error) return <div role="alert">{body.error} <button type="button" className="underline" onClick={() => setRetry((value) => value + 1)}>再試行</button></div>;
  if (body.text === undefined) return <p role="status">発言本文を読み込んでいます…</p>;
  return <>{renderText(body.text)}</>;
}
