"""회사 정보 캐시.

회사 규모는 공고와 달리 거의 바뀌지 않는다. 매일 다시 물으면 소스 서버에
쓸데없는 요청이 쌓이므로, 한 번 받은 정보는 TTL_DAYS 동안 다시 묻지 않는다.

실패는 캐시하지 않는다. 하루 장애가 한 달 동안 '기타'로 굳으면 안 된다.
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Callable, Iterable

from collect.schema import CompanyProfile

TTL_DAYS = 30


def _load(cache_path: Path) -> dict[str, dict]:
    if not cache_path.exists():
        return {}
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
    except ValueError:
        print(f"  ! 회사 정보 캐시가 깨져 새로 받습니다 — {cache_path}", file=sys.stderr)
        return {}
    return data if isinstance(data, dict) else {}


def _fresh(entry: dict, today: date) -> CompanyProfile | None:
    try:
        age = (today - date.fromisoformat(entry["fetched_on"])).days
        return CompanyProfile(**entry["profile"]) if age < TTL_DAYS else None
    except (KeyError, TypeError, ValueError):
        return None


def lookup(
    company_ids: Iterable[str],
    fetch: Callable[[str], CompanyProfile | None],
    cache_path: Path,
    *,
    today: date,
) -> dict[str, CompanyProfile]:
    """회사 id → 회사 정보. 캐시에 없거나 오래된 것만 `fetch`로 받는다.

    받지 못한 회사(실패, 소스에 정보 없음)는 결과에서 빠진다.
    """
    cache = _load(cache_path)
    found: dict[str, CompanyProfile] = {}
    changed = False

    for company_id in dict.fromkeys(company_ids):
        cached = _fresh(cache.get(company_id) or {}, today)
        if cached is not None:
            found[company_id] = cached
            continue
        try:
            profile = fetch(company_id)
        except (OSError, ValueError) as exc:
            print(f"  ! 회사 {company_id}: 정보 조회 실패, 규모는 기타로 둡니다 — {exc}", file=sys.stderr)
            continue
        if profile is None:
            continue
        found[company_id] = profile
        cache = {**cache, company_id: {"fetched_on": today.isoformat(), "profile": asdict(profile)}}
        changed = True

    if changed:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    return found
