"""Automatic research runs. Every run is logged in research_runs, including failures."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable

import pandas as pd

from collectors.stackexchange import StackExchangeClient, StackExchangeError
from core.config import load_config
from db.repository import Repository

MIN_QUOTA = 5  # stop early rather than exhaust the daily API quota


@dataclass
class RunSummary:
    run_id: str
    status: str
    requests_made: int = 0
    items_seen: int = 0
    sources_added: int = 0
    sources_refreshed: int = 0
    quota_remaining: int | None = None
    log: list[str] = field(default_factory=list)


def clean_query(text: str) -> str:
    """Stack Exchange search matches words literally: dashes or symbols (e.g. 'Rank–nullity')
    make it return nothing, so keep only letters, digits and spaces."""
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", text)).strip()


def _subject_tag(subject: str) -> str:
    tag = load_config()["stackexchange"]["subject_tags"].get(subject)
    if isinstance(tag, list):  # older config format: a list of tags — use the first
        tag = tag[0] if tag else None
    return tag or ""


def search_plan(repo: Repository, topic_ids: list[str] | None = None) -> pd.DataFrame:
    """What each active topic will search for: the query text and the Stack Exchange tag."""
    topics = repo.topics_df(include_inactive=False)
    if topic_ids is not None:
        topics = topics[topics["topic_id"].isin(topic_ids)]
    query = topics["search_query"].where(topics["search_query"].fillna("").str.strip() != "", topics["name"])
    return pd.DataFrame({
        "topic_id": topics["topic_id"],
        "topic": topics["name"],
        "subject": topics["subject"],
        "query": query.map(clean_query),
        "tag": topics["subject"].map(_subject_tag),
    }).reset_index(drop=True)


def run_stackexchange(repo: Repository, *, topic_ids: list[str] | None = None,
                      client: StackExchangeClient | None = None,
                      progress: Callable[[float, str], None] | None = None) -> RunSummary:
    cfg = load_config()["stackexchange"]
    client = client or StackExchangeClient(cfg.get("site", "math"),
                                           request_delay=float(cfg.get("request_delay_seconds", 0.5)))
    plan = search_plan(repo, topic_ids)
    from_date = datetime.now(timezone.utc) - timedelta(days=365 * int(cfg.get("lookback_years", 8)))
    per_topic, min_score = int(cfg.get("results_per_topic", 5)), int(cfg.get("min_score", 0))

    summary = RunSummary(run_id=repo.start_run("stackexchange"), status="ok")
    failures = 0
    for i, row in enumerate(plan.itertuples(), start=1):
        if progress:
            progress((i - 1) / max(len(plan), 1), f"Searching: {row.topic}")
        if summary.quota_remaining is not None and summary.quota_remaining < MIN_QUOTA:
            summary.log.append(f"Stopped: API quota nearly used up ({summary.quota_remaining} left).")
            failures += len(plan) - i + 1
            break
        if not row.tag:
            summary.log.append(f"{row.topic}: no Stack Exchange tag configured for subject "
                               f"'{row.subject}' — searching without a tag filter.")
        try:
            result = client.search(row.query, row.tag or None, from_date=from_date, pagesize=30,
                                   excerpt_chars=int(cfg.get("excerpt_chars", 400)))
        except StackExchangeError as e:
            failures += 1
            summary.log.append(f"{row.topic}: FAILED — {e}")
            continue
        summary.quota_remaining = result.quota_remaining
        summary.items_seen += len(result.posts)
        kept = sorted((p for p in result.posts if p.score >= min_score), key=lambda p: p.score, reverse=True)
        kept = kept[:per_topic]
        added = 0
        for post in kept:
            _, created = repo.upsert_platform_source(
                platform_id=post.platform_id, topic_id=row.topic_id,
                source_type="stackexchange", title=post.title, url=post.link,
                published_at=post.created_at.replace(tzinfo=None), content_excerpt=post.excerpt,
                engagement_count=post.score, license=post.license or "CC BY-SA",
                collection_method="api",
                notes=f"Author: {post.author or 'unknown'} · tags: {', '.join(post.tags)} · "
                      f"answers: {post.answer_count} · views: {post.view_count}",
            )
            added += created
        summary.sources_added += added
        summary.sources_refreshed += len(kept) - added
        summary.log.append(f"{row.topic}: {len(result.posts)} found, {len(kept)} kept "
                           f"(score ≥ {min_score}), {added} new.")

    summary.requests_made = client.requests_made
    if failures and failures >= len(plan):
        summary.status = "failed"
    elif failures:
        summary.status = "partial"
    if not len(plan):
        summary.log.append("No active topics to search.")
    repo.finish_run(summary.run_id, status=summary.status, requests_made=summary.requests_made,
                    items_seen=summary.items_seen, sources_added=summary.sources_added,
                    quota_remaining=summary.quota_remaining, log="\n".join(summary.log))
    if progress:
        progress(1.0, "Done")
    return summary
