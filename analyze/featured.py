"""첫 화면에 올릴 상위 몇 개를 고른다.

Notion 보기에는 '상위 N 개'가 없다(2026-09-17 확인). 차트 그룹을 몇 개까지만
그리게 할 수도, 표 행을 몇 줄까지만 보이게 할 수도 없다. 모르는 설정은 400 도
안 주고 조용히 버린다.

그래서 여기서 미리 고르고, 적재할 때 그 표시를 Notion 에 써 둔다. 보기는
그 표시를 조건으로 걸기만 하면 된다.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Iterable, Mapping, Protocol, Sequence

#: 첫 화면에 보일 개수. 스킬 막대도 강의 줄도 이만큼만 본다.
FEATURED_LIMIT = 5


class _HasGap(Protocol):
    gap: Any


def top_missing_skills(
    rows: Iterable[_HasGap],
    skill_names: Mapping[str, str],
    *,
    limit: int = FEATURED_LIMIT,
) -> tuple[str, ...]:
    """공고가 많이 요구하는 부족 스킬 상위 N 개를 이름으로 돌려준다.

    동률은 이름순으로 끊는다. 순서가 흔들리면 막대가 매일 갈아끼워져서
    어제와 오늘을 비교할 수 없다.
    """
    counts: Counter[str] = Counter()
    for row in rows:
        counts.update(row.gap.labeled(skill_names, "missing"))
    ranked = sorted(counts, key=lambda name: (-counts[name], name))
    return tuple(ranked[:limit])


class _HasSkillKey(Protocol):
    skill_id: str
    key: str


def featured_courses(
    recommendations: Sequence[_HasSkillKey],
    *,
    limit: int = FEATURED_LIMIT,
) -> frozenset[str]:
    """스킬을 돌아가며 한 건씩 골라 N 건의 key 를 돌려준다.

    한 스킬의 과정이 다섯 자리를 다 먹으면 다른 역량이 화면에서 사라진다.
    스킬 차례는 추천이 준 순서(요구가 많은 스킬 먼저)를 그대로 따른다.
    """
    by_skill: dict[str, list[str]] = {}
    seen: set[str] = set()
    for rec in recommendations:
        if rec.key in seen:
            continue
        seen.add(rec.key)
        by_skill.setdefault(rec.skill_id, []).append(rec.key)

    picked: list[str] = []
    while len(picked) < limit:
        before = len(picked)
        for keys in by_skill.values():
            if len(picked) >= limit:
                break
            if keys:
                picked.append(keys.pop(0))
        if len(picked) == before:  # 더 고를 게 없다
            break
    return frozenset(picked)
