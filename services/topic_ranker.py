"""Rank topics for question generation.

    S = 100 * (wR*R + wD*D + wT*T + wQ*Q + wN*N) / (wR + wD + wT + wQ + wN)

R course relevance   – highest priority among linked active courses (priority / 5)
D observed difficulty – amount and engagement of linked, non-excluded sources
T timeliness          – linked course has a monitored current/upcoming term
Q content potential   – prior by target level (follows the planned 60/20/20 content mix)
N novelty             – time since the topic was last selected, reduced by open candidates

All weights and priors are initial assumptions (config.yaml / Settings), not measured facts.
A topic with no evidence gets a neutral prior and is flagged, never treated as zero interest.
"""
from __future__ import annotations

import math
from datetime import datetime

import pandas as pd

from core.config import load_config
from db.repository import Repository

SIGNALS = ["course_relevance", "observed_difficulty", "timeliness", "content_quality_potential", "novelty"]
SIGNAL_LABELS = {
    "course_relevance": "Course relevance",
    "observed_difficulty": "Observed difficulty",
    "timeliness": "Timeliness",
    "content_quality_potential": "Content potential",
    "novelty": "Novelty",
}
OPEN_STATUSES = ("candidate", "favorite")


def default_weights() -> dict[str, float]:
    return {k: float(v) for k, v in load_config()["ranking"]["weights"].items()}


def current_weights(repo: Repository) -> dict[str, float]:
    saved = repo.get_json_setting("ranking_weights") or {}
    weights = default_weights()
    weights.update({k: float(v) for k, v in saved.items() if k in weights})
    return weights


def _difficulty_signal(sources: pd.DataFrame, prior: float) -> tuple[float, bool]:
    if sources.empty:
        return prior, True
    count_part = min(1.0, len(sources) / 5)
    known = pd.to_numeric(sources["engagement_count"], errors="coerce").dropna()
    if known.empty:
        return count_part, False
    engagement_part = min(1.0, math.log1p(max(known.clip(lower=0).sum(), 0)) / math.log1p(200))
    return 0.5 * count_part + 0.5 * engagement_part, False


def rank_topics(repo: Repository, weights: dict[str, float] | None = None,
                now: datetime | None = None) -> pd.DataFrame:
    cfg = load_config()["ranking"]
    weights = weights or current_weights(repo)
    now = now or datetime.now()
    prior = float(cfg.get("missing_evidence_prior", 0.3))
    level_quality = cfg.get("level_quality", {})
    novelty_days = float(cfg.get("novelty_days", 60))

    topics = repo.topics_df(include_inactive=False)
    courses = repo.courses_df().set_index("course_id")
    offerings = repo.offerings_df()
    current_courses = set(
        offerings.loc[offerings["monitor"].astype(bool) & (offerings["term_year"] >= now.year - 1), "course_id"]
    ) if not offerings.empty else set()
    sources = repo.sources_df(include_excluded=False).explode("topic_ids")
    questions = repo.questions_df(OPEN_STATUSES)
    open_counts = questions["topic_id"].value_counts() if not questions.empty else pd.Series(dtype=int)

    rows = []
    for t in topics.itertuples():
        active_links = [c for c in t.course_ids if c in courses.index and courses.loc[c, "active"]]
        notes = []

        if active_links:
            relevance = max(int(courses.loc[c, "priority"]) for c in active_links) / 5
        else:
            relevance = 0.4
            notes.append("not linked to an active course")

        difficulty, missing = _difficulty_signal(sources[sources["topic_ids"] == t.topic_id], prior)
        if missing:
            notes.append("no evidence yet (neutral prior used)")

        if any(c in current_courses for c in active_links):
            timeliness = 1.0
        elif active_links:
            timeliness = 0.5
            notes.append("no monitored current term")
        else:
            timeliness = 0.4

        quality = float(level_quality.get(t.target_level, 0.7))

        last_used = t.last_used_at
        if last_used is None or pd.isna(last_used):
            novelty = 1.0
        else:
            novelty = min(1.0, max(0.0, (now - pd.Timestamp(last_used).to_pydatetime().replace(tzinfo=None)).days)
                          / novelty_days)
        n_open = int(open_counts.get(t.topic_id, 0))
        if n_open:
            novelty /= 1 + 0.5 * n_open
            notes.append(f"{n_open} open candidate question(s)")

        signals = dict(course_relevance=relevance, observed_difficulty=difficulty, timeliness=timeliness,
                       content_quality_potential=quality, novelty=novelty)
        total_w = sum(weights.values()) or 1.0
        score = 100 * sum(weights[k] * signals[k] for k in SIGNALS) / total_w
        rows.append({
            "topic_id": t.topic_id, "topic": t.name, "subject": t.subject, "courses": t.courses,
            **{k: round(v, 3) for k, v in signals.items()},
            "final_score": round(score, 1), "evidence_missing": missing,
            "source_count": int((sources["topic_ids"] == t.topic_id).sum()),
            "notes": "; ".join(notes),
        })
    cols = ["topic_id", "topic", "subject", "courses", *SIGNALS, "final_score", "evidence_missing",
            "source_count", "notes"]
    df = pd.DataFrame(rows, columns=cols)
    return df.sort_values("final_score", ascending=False).reset_index(drop=True)
