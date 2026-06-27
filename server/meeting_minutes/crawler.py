from __future__ import annotations

import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable

from bs4 import BeautifulSoup


BASE_URL = "https://www2.city.mine.lg.jp"
TOP_URL = "https://www2.city.mine.lg.jp/gyosei/shigikai/11159.html"
SECTION_URLS = {
    "本会議": "https://www2.city.mine.lg.jp/soshiki/gikai/shigikai/kaigiroku/honkaigi/index.html",
    "常任委員会": "https://www2.city.mine.lg.jp/soshiki/gikai/shigikai/kaigiroku/jouniniinkai/index.html",
    "特別委員会": "https://www2.city.mine.lg.jp/soshiki/gikai/shigikai/kaigiroku/tokubetuiinkai/index.html",
}
ERA_START = {"令和": 2018, "平成": 1988, "昭和": 1925}


@dataclass(frozen=True)
class MinutesPdfItem:
    section: str
    meeting_name: str
    title: str
    page_url: str
    pdf_url: str
    date_label: str
    meeting_date: date | None


def canonical_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))


def fetch_html(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "mine-city-reiki-minutes/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        raw = res.read()
    for encoding in ("utf-8", "cp932", "shift_jis"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "ignore")


def parse_japanese_date(text: str) -> date | None:
    normalized = re.sub(r"\s+", "", text or "")
    match = re.search(r"(令和|平成|昭和)(元|\d{1,2})年(\d{1,2})月(\d{1,2})日", normalized)
    if not match:
        return None
    era, year_text, month_text, day_text = match.groups()
    year = 1 if year_text == "元" else int(year_text)
    try:
        return date(ERA_START[era] + year, int(month_text), int(day_text))
    except ValueError:
        return None


def section_from_url(url: str) -> str | None:
    for section, section_url in SECTION_URLS.items():
        if canonical_url(url).startswith(canonical_url(section_url).rsplit("/", 1)[0]):
            return section
    return None


def page_title(soup: BeautifulSoup, fallback: str) -> str:
    for selector in ("h1", "h2", ".page_title", "title"):
        node = soup.select_one(selector)
        if node:
            text = node.get_text(" ", strip=True)
            if text:
                return text
    return fallback


def infer_meeting_name(page_label: str, link_label: str) -> str:
    candidates = [page_label, link_label]
    for text in candidates:
        match = re.search(r"(令和\s*[元\d]+\s*年[^\\n]*?(?:定例会|臨時会|委員会|部会))", text)
        if match:
            return re.sub(r"\s+", "", match.group(1))
    return re.sub(r"\s+", " ", page_label).strip()


def iter_internal_links(soup: BeautifulSoup, base_url: str, section: str) -> Iterable[str]:
    section_base = canonical_url(SECTION_URLS[section]).rsplit("/", 1)[0]
    for link in soup.find_all("a", href=True):
        url = canonical_url(urllib.parse.urljoin(base_url, link["href"]))
        if not url.startswith(BASE_URL):
            continue
        if url.lower().endswith(".pdf"):
            continue
        if url.startswith(section_base) and url.endswith((".html", "/")):
            yield url


def crawl_minutes_pdfs(recent_days: int | None = 365, today: date | None = None, max_pages_per_section: int = 300) -> list[MinutesPdfItem]:
    today = today or date.today()
    cutoff = today - timedelta(days=max(1, recent_days)) if recent_days and recent_days > 0 else None
    items: dict[str, MinutesPdfItem] = {}

    for section, start_url in SECTION_URLS.items():
        queue = [canonical_url(start_url)]
        seen: set[str] = set()
        while queue and len(seen) < max_pages_per_section:
            page_url = queue.pop(0)
            if page_url in seen:
                continue
            seen.add(page_url)
            try:
                soup = BeautifulSoup(fetch_html(page_url), "html.parser")
            except Exception:
                continue
            title = page_title(soup, section)
            for next_url in iter_internal_links(soup, page_url, section):
                if next_url not in seen and next_url not in queue:
                    queue.append(next_url)
            for link in soup.find_all("a", href=True):
                pdf_url = canonical_url(urllib.parse.urljoin(page_url, link["href"]))
                if not pdf_url.lower().endswith(".pdf"):
                    continue
                link_label = link.get_text(" ", strip=True) or title
                date_value = parse_japanese_date(link_label) or parse_japanese_date(title)
                if date_value is None or (cutoff is not None and date_value < cutoff) or date_value > today:
                    continue
                meeting_name = infer_meeting_name(title, link_label)
                item_title = re.sub(r"\s+", " ", link_label).strip() or meeting_name
                items[pdf_url] = MinutesPdfItem(
                    section=section,
                    meeting_name=meeting_name,
                    title=item_title,
                    page_url=page_url,
                    pdf_url=pdf_url,
                    date_label=item_title,
                    meeting_date=date_value,
                )
    return sorted(items.values(), key=lambda item: (item.meeting_date or date.min, item.section, item.title))
