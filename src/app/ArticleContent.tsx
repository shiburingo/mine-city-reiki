import React from 'react';

const TABLE_START = '__TABLE_START__';
const TABLE_END = '__TABLE_END__';
const LINK_START = '__REIKI_LINK_START__';
const LINK_TEXT = '__REIKI_LINK_TEXT__';
const LINK_END = '__REIKI_LINK_END__';

type Part =
  | { type: 'text'; text: string }
  | { type: 'table'; rows: string[][] };

export type ArticleLinkMap = Record<string, string>;
export type SourceAnchorLinkMap = Record<string, string>;
export type SourceDocumentLinkMap = Record<string, number>;

type SourceDocumentLinkHandler = (documentId: number, sourceAnchorId?: string) => void;

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

// 第X条 / 別表第X / 様式第X 形式の相互参照を検出してアンカーリンクにする
const ARTICLE_REF_RE = /(第[〇一二三四五六七八九十百千万\d]+条(?:の[〇一二三四五六七八九十百千万\d]+)*|別表第[〇一二三四五六七八九十百千万\d]+|様式第[〇一二三四五六七八九十百千万\d]+号?)/g;
const LINK_MARKER_RE = /__REIKI_LINK_START__(.*?)__REIKI_LINK_TEXT__(.*?)__REIKI_LINK_END__/g;

function normalizeArticleRef(value: string): string {
  return value.replace(/\s+/g, '').trim();
}

function decodeMarkerValue(value: string): string {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}

function resolveSourceDocumentLink(href: string, sourceDocumentLinks: SourceDocumentLinkMap, sourceUrl?: string): { documentId: number; sourceAnchorId?: string } | null {
  if (!sourceUrl) return null;
  try {
    const url = new URL(href, sourceUrl);
    const externalId = url.pathname.split('/').pop()?.replace(/\.html$/i, '') || '';
    const documentId = sourceDocumentLinks[externalId];
    if (!documentId) return null;
    return { documentId, sourceAnchorId: url.searchParams.get('id') || undefined };
  } catch {
    return null;
  }
}

function resolveSourceHref(href: string, sourceAnchorLinks: SourceAnchorLinkMap, sourceUrl?: string): string {
  if (href.startsWith('#')) {
    return sourceAnchorLinks[href.slice(1)] || href;
  }
  if (/^https?:\/\//i.test(href)) {
    return href;
  }
  if (sourceUrl) {
    try {
      return new URL(href, sourceUrl).toString();
    } catch {
      return href;
    }
  }
  return href;
}

function scrollToInternalAnchor(event: React.MouseEvent<HTMLAnchorElement>, href: string): void {
  if (!href.startsWith('#')) return;
  const targetId = decodeURIComponent(href.slice(1));
  const target = document.getElementById(targetId);
  if (!target) return;
  event.preventDefault();

  let container = target.parentElement;
  while (container && container !== document.body) {
    const style = window.getComputedStyle(container);
    const canScroll = /(auto|scroll)/.test(`${style.overflowY}${style.overflow}`);
    if (canScroll && container.scrollHeight > container.clientHeight) {
      const targetRect = target.getBoundingClientRect();
      const containerRect = container.getBoundingClientRect();
      const top = targetRect.top - containerRect.top + container.scrollTop - 24;
      container.scrollTo({ top: Math.max(top, 0), behavior: 'smooth' });
      return;
    }
    container = container.parentElement;
  }
  target.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function renderPlainText(part: string, keywords: string[], articleLinks: ArticleLinkMap, keyPrefix: string): React.ReactNode[] {
  const nodes: React.ReactNode[] = [];
  const refParts = part.split(ARTICLE_REF_RE);
  refParts.forEach((part, pi) => {
    if (ARTICLE_REF_RE.test(part)) {
      ARTICLE_REF_RE.lastIndex = 0;
      const href = articleLinks[normalizeArticleRef(part)];
      if (href) {
        nodes.push(
          <a
            key={`${keyPrefix}-ref-${pi}`}
            className="text-primary underline decoration-dotted underline-offset-2 hover:decoration-solid"
            href={href}
            onClick={(event) => scrollToInternalAnchor(event, href)}
            title={`${part}へ移動`}
          >
            {part}
          </a>,
        );
        return;
      }
      nodes.push(
        <span key={`${keyPrefix}-ref-${pi}`} className="text-primary underline decoration-dotted" title={`${part}を参照`}>
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
          <mark key={`${keyPrefix}-hl-${pi}-${hi}`} className="bg-yellow-200 text-yellow-900 rounded px-0.5">
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

function renderTextLine(
  line: string,
  keywords: string[],
  articleLinks: ArticleLinkMap,
  sourceAnchorLinks: SourceAnchorLinkMap,
  sourceDocumentLinks: SourceDocumentLinkMap,
  sourceUrl?: string,
  onSourceDocumentLink?: SourceDocumentLinkHandler,
): React.ReactNode[] {
  const nodes: React.ReactNode[] = [];
  let cursor = 0;
  for (const match of line.matchAll(LINK_MARKER_RE)) {
    const index = match.index ?? 0;
    if (index > cursor) {
      nodes.push(...renderPlainText(line.slice(cursor, index), keywords, articleLinks, `plain-${cursor}`));
    }
    const rawHref = decodeMarkerValue(match[1] || '');
    const label = decodeMarkerValue(match[2] || '');
    const documentLink = resolveSourceDocumentLink(rawHref, sourceDocumentLinks, sourceUrl);
    const href = resolveSourceHref(rawHref, sourceAnchorLinks, sourceUrl);
    const isInternal = href.startsWith('#') || !!documentLink;
    nodes.push(
      <a
        key={`link-${index}`}
        className="text-primary underline decoration-dotted underline-offset-2 hover:decoration-solid"
        href={documentLink ? `#doc-${documentLink.documentId}` : href}
        onClick={documentLink && onSourceDocumentLink ? (event) => {
          event.preventDefault();
          onSourceDocumentLink(documentLink.documentId, documentLink.sourceAnchorId);
        } : href.startsWith('#') ? (event) => scrollToInternalAnchor(event, href) : undefined}
        rel={isInternal ? undefined : 'noreferrer'}
        target={isInternal ? undefined : '_blank'}
        title={isInternal ? `${label}へ移動` : `${label}を原文で開く`}
      >
        {renderPlainText(label, keywords, {}, `link-label-${index}`)}
      </a>,
    );
    cursor = index + match[0].length;
  }
  if (cursor < line.length) {
    nodes.push(...renderPlainText(line.slice(cursor), keywords, articleLinks, `plain-${cursor}`));
  }
  return nodes;
}

function ArticleTable({
  rows,
  keywords,
  articleLinks,
  sourceAnchorLinks,
  sourceDocumentLinks,
  sourceUrl,
  onSourceDocumentLink,
}: {
  rows: string[][];
  keywords: string[];
  articleLinks: ArticleLinkMap;
  sourceAnchorLinks: SourceAnchorLinkMap;
  sourceDocumentLinks: SourceDocumentLinkMap;
  sourceUrl?: string;
  onSourceDocumentLink?: SourceDocumentLinkHandler;
}) {
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
                  {renderTextLine(cell, keywords, articleLinks, sourceAnchorLinks, sourceDocumentLinks, sourceUrl, onSourceDocumentLink)}
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
                  {renderTextLine(cell, keywords, articleLinks, sourceAnchorLinks, sourceDocumentLinks, sourceUrl, onSourceDocumentLink)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ArticleContent({
  text,
  keywords = [],
  articleLinks = {},
  sourceAnchorLinks = {},
  sourceDocumentLinks = {},
  sourceUrl,
  onSourceDocumentLink,
}: {
  text: string;
  keywords?: string[];
  articleLinks?: ArticleLinkMap;
  sourceAnchorLinks?: SourceAnchorLinkMap;
  sourceDocumentLinks?: SourceDocumentLinkMap;
  sourceUrl?: string;
  onSourceDocumentLink?: SourceDocumentLinkHandler;
}) {
  const parts = parseArticleParts(text || '');
  return (
    <div className="space-y-1 text-sm leading-7">
      {parts.map((part, i) => {
        if (part.type === 'table') {
          return <ArticleTable key={i} rows={part.rows} keywords={keywords} articleLinks={articleLinks} sourceAnchorLinks={sourceAnchorLinks} sourceDocumentLinks={sourceDocumentLinks} sourceUrl={sourceUrl} onSourceDocumentLink={onSourceDocumentLink} />;
        }
        const lines = part.text.split('\n');
        return (
          <p key={i} className="whitespace-pre-wrap">
            {lines.map((line, li) => (
              <React.Fragment key={li}>
                {li > 0 ? '\n' : null}
                {renderTextLine(line, keywords, articleLinks, sourceAnchorLinks, sourceDocumentLinks, sourceUrl, onSourceDocumentLink)}
              </React.Fragment>
            ))}
          </p>
        );
      })}
    </div>
  );
}
