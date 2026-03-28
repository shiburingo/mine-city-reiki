import React from 'react';

const TABLE_START = '__TABLE_START__';
const TABLE_END = '__TABLE_END__';

type Part =
  | { type: 'text'; text: string }
  | { type: 'table'; rows: string[][] };

function parseArticleParts(text: string): Part[] {
  const parts: Part[] = [];
  const segments = text.split(TABLE_START);
  for (let i = 0; i < segments.length; i++) {
    const seg = segments[i];
    if (i === 0) {
      if (seg) parts.push({ type: 'text', text: seg });
      continue;
    }
    const endIdx = seg.indexOf(TABLE_END);
    if (endIdx === -1) {
      parts.push({ type: 'text', text: seg });
      continue;
    }
    const tableText = seg.slice(0, endIdx).trim();
    const after = seg.slice(endIdx + TABLE_END.length);
    const rows = tableText
      .split('\n')
      .map((line) => line.split('\t'))
      .filter((row) => row.some((cell) => cell.trim()));
    if (rows.length > 0) {
      parts.push({ type: 'table', rows });
    }
    if (after.trim()) {
      parts.push({ type: 'text', text: after });
    }
  }
  return parts;
}

// 第X条 / 第X項 / 第X号 形式の相互参照を検出してアンカーリンクにする
const ARTICLE_REF_RE = /(第[〇一二三四五六七八九十百千万\d]+条(?:の[〇一二三四五六七八九十百千万\d]+)*)/g;

function renderTextLine(line: string, keywords: string[]): React.ReactNode[] {
  // まず相互参照を分割
  const nodes: React.ReactNode[] = [];
  const refParts = line.split(ARTICLE_REF_RE);
  refParts.forEach((part, pi) => {
    if (ARTICLE_REF_RE.test(part)) {
      ARTICLE_REF_RE.lastIndex = 0;
      nodes.push(
        <span key={`ref-${pi}`} className="text-primary underline decoration-dotted cursor-pointer" title={`${part}を参照`}>
          {part}
        </span>,
      );
      return;
    }
    ARTICLE_REF_RE.lastIndex = 0;
    if (!keywords.length) {
      nodes.push(part);
      return;
    }
    // キーワードハイライト
    const escaped = keywords.map((k) => k.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|');
    const hlRe = new RegExp(`(${escaped})`, 'gi');
    const hlParts = part.split(hlRe);
    hlParts.forEach((hp, hi) => {
      if (hlRe.test(hp)) {
        hlRe.lastIndex = 0;
        nodes.push(
          <mark key={`hl-${pi}-${hi}`} className="bg-yellow-200 text-yellow-900 rounded px-0.5">
            {hp}
          </mark>,
        );
      } else {
        hlRe.lastIndex = 0;
        nodes.push(hp);
      }
    });
  });
  return nodes;
}

function ArticleTable({ rows }: { rows: string[][] }) {
  const hasHeader = rows.length > 1;
  const headerRow = hasHeader ? rows[0] : null;
  const bodyRows = hasHeader ? rows.slice(1) : rows;
  return (
    <div className="my-2 overflow-x-auto rounded-xl border">
      <table className="min-w-full text-sm">
        {headerRow && (
          <thead className="bg-muted/50">
            <tr>
              {headerRow.map((cell, ci) => (
                <th key={ci} className="px-3 py-2 text-left font-semibold border-b whitespace-nowrap">
                  {cell}
                </th>
              ))}
            </tr>
          </thead>
        )}
        <tbody>
          {bodyRows.map((row, ri) => (
            <tr key={ri} className="border-b last:border-b-0 hover:bg-muted/20">
              {row.map((cell, ci) => (
                <td key={ci} className="px-3 py-2 align-top whitespace-pre-wrap">
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ArticleContent({ text, keywords = [] }: { text: string; keywords?: string[] }) {
  const parts = parseArticleParts(text || '');
  return (
    <div className="space-y-1 text-sm leading-7">
      {parts.map((part, i) => {
        if (part.type === 'table') {
          return <ArticleTable key={i} rows={part.rows} />;
        }
        const lines = part.text.split('\n');
        return (
          <p key={i} className="whitespace-pre-wrap">
            {lines.map((line, li) => (
              <React.Fragment key={li}>
                {li > 0 ? '\n' : null}
                {renderTextLine(line, keywords)}
              </React.Fragment>
            ))}
          </p>
        );
      })}
    </div>
  );
}
