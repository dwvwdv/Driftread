"""Time-decayed explicit evidence; no inferred event relations or AI calls."""

from datetime import datetime, timezone
from math import exp, log

from routers.recommendations import Signals, _reason, _score
from services.source_health import summarize_source_health

HALF_LIFE_SECONDS = 24 * 3600
CANDIDATE_LIMIT = 500


def _time(raw: str | datetime) -> datetime:
    value = (
        datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if isinstance(raw, str)
        else raw
    )
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def rank_personal_heat(
    snapshot: dict, signals: Signals, at: datetime, limit: int
) -> dict:
    health = summarize_source_health(snapshot["sources"], at)
    groups: dict[str, dict[str, float]] = {}
    for evidence in snapshot["evidence"]:
        if not evidence.get("source_time"):
            continue
        age = (at - _time(evidence["source_time"])).total_seconds()
        if age < 0 or age > 7 * 24 * 3600:
            continue
        value = exp(-log(2) * age / HALF_LIFE_SECONDS)
        participants = groups.setdefault(evidence["group_key"], {})
        key = evidence["participant_key"]
        participants[key] = max(value, participants.get(key, 0.0))
    rows = []
    for candidate in snapshot["candidates"]:
        # RPC applies cohort + role + explicit feedback gates before ranking.
        parts = groups.get(candidate["group_key"], {})
        heat = sum(parts.values())
        affinity = _score(candidate, signals)
        # Saturating heat contributes less than the smallest strong category
        # signal (3), while weak history stays weaker than like/bookmark.
        score = affinity + 0.25 * heat / (1 + heat)
        why = _reason(candidate, signals) or "來自你未靜音的訂閱來源"
        if parts:
            why += f"；{len(parts)} 個獨立來源參與（依發佈時間衰減）"
        else:
            why += "；沒有可用的近期來源發佈時間"
        if not health["complete"]:
            why += "；部分來源尚未更新，熱度資訊未完整"
        rows.append(
            (
                score,
                dict(
                    candidate,
                    why=why,
                    heat=round(heat, 6),
                    participant_count=len(parts),
                ),
            )
        )
    rows.sort(
        key=lambda pair: (pair[0], _time(pair[1]["timeline_at"]), pair[1]["id"]),
        reverse=True,
    )
    # URL/manual Story dedup is presentation-only. Read/bookmark APIs continue
    # to refer to individual articles and original feed membership.
    seen = set()
    items = []
    for _, row in rows:
        if row["group_key"] in seen:
            continue
        seen.add(row["group_key"])
        items.append(row)
        if len(items) >= limit:
            break
    return {
        "items": items,
        "next_cursor": None,
        "snapshot_at": at,
        "complete": health["complete"],
        "behind_participant_count": health["behind_participant_count"],
        "candidate_limit": CANDIDATE_LIMIT,
    }
