"""Mathematics Stack Exchange via the official public API (no scraping).

API terms: https://api.stackexchange.com/docs — 300 requests/day per IP without an app key.
Content is licensed CC BY-SA (version per post); we keep the link, author and license, and only
store a short excerpt.
"""
from __future__ import annotations

import html
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

API_BASE = "https://api.stackexchange.com/2.3"


class StackExchangeError(RuntimeError):
    pass


@dataclass
class SEPost:
    question_id: int
    title: str
    link: str
    score: int
    view_count: int | None
    answer_count: int | None
    created_at: datetime
    tags: list[str]
    excerpt: str
    author: str | None
    license: str | None

    @property
    def platform_id(self) -> str:
        return f"math.se:{self.question_id}"


@dataclass
class SearchResult:
    posts: list[SEPost] = field(default_factory=list)
    quota_remaining: int | None = None


def html_to_text(body: str, limit: int) -> str:
    """Strip HTML, keep LaTeX as written, collapse whitespace, truncate at a word boundary."""
    text = re.sub(r"<(pre|code)[^>]*>.*?</\1>", " [code] ", body, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", html.unescape(text)).strip()
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + " …"


class StackExchangeClient:
    def __init__(self, site: str = "math", *, http: httpx.Client | None = None,
                 request_delay: float = 0.5, app_key: str | None = None):
        self.site = site
        self.http = http or httpx.Client(timeout=20, headers={"User-Agent": "MathTutorStudio/0.2"})
        self.request_delay = request_delay
        self.app_key = app_key
        self.requests_made = 0
        self._next_allowed = 0.0

    def _wait_turn(self) -> None:
        delay = self._next_allowed - time.monotonic()
        if delay > 0:
            time.sleep(delay)

    def search(self, query: str, tag: str | None = None, *, from_date: datetime | None = None,
               pagesize: int = 30, excerpt_chars: int = 400) -> SearchResult:
        """Most relevant questions matching `query`, optionally restricted to one tag.
        (The API's `tagged` filter requires ALL tags given, so we only ever pass one.)"""
        params: dict[str, str | int] = {
            "site": self.site, "q": query, "sort": "relevance", "order": "desc",
            "pagesize": pagesize, "filter": "withbody",
        }
        if tag:
            params["tagged"] = tag
        if from_date:
            params["fromdate"] = int(from_date.timestamp())
        if self.app_key:
            params["key"] = self.app_key

        self._wait_turn()
        try:
            resp = self.http.get(f"{API_BASE}/search/advanced", params=params)
        except httpx.HTTPError as e:
            raise StackExchangeError(f"network error: {e}") from e
        finally:
            self.requests_made += 1
            self._next_allowed = time.monotonic() + self.request_delay
        try:
            data = resp.json()
        except ValueError as e:
            raise StackExchangeError(f"HTTP {resp.status_code}: not JSON") from e
        if resp.status_code != 200 or "error_id" in data:
            raise StackExchangeError(
                f"HTTP {resp.status_code}: {data.get('error_name', '')} {data.get('error_message', '')}".strip()
            )
        if data.get("backoff"):  # API asks us to pause before the next request
            self._next_allowed = time.monotonic() + max(self.request_delay, float(data["backoff"]))

        posts = [
            SEPost(
                question_id=item["question_id"],
                title=html.unescape(item.get("title", "")),
                link=item.get("link", ""),
                score=item.get("score", 0),
                view_count=item.get("view_count"),
                answer_count=item.get("answer_count"),
                created_at=datetime.fromtimestamp(item.get("creation_date", 0), tz=timezone.utc),
                tags=item.get("tags", []),
                excerpt=html_to_text(item.get("body", ""), excerpt_chars),
                author=(item.get("owner") or {}).get("display_name"),
                license=item.get("content_license"),
            )
            for item in data.get("items", [])
        ]
        return SearchResult(posts=posts, quota_remaining=data.get("quota_remaining"))
