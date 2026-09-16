"""부족 스킬에 맞는 훈련과정 고르기.

순수 함수다. 네트워크를 모른다.

여기서 지키는 것 셋.
  - **들을 수 있는 과정만 권한다.** 재직자 대상(근로자원격훈련, 제목의 '재직자')은
    구직자가 못 듣는다. 이미 시작한 과정도 뺀다.
  - **같은 과정의 회차를 여러 건으로 세지 않는다.** 가장 빨리 시작하는 회차 하나만 남긴다.
  - **직접 다루는 과정과 기반 역량 과정을 구분한다.** 도구 이름이 과정명에 없는 스킬이
    많아서(2026-09-16 실측: A/B, 에어플로우, 스파크 모두 0건) 기반 과정으로 잇는데,
    그걸 '이 과정에서 그 도구를 배운다'고 말하면 과장이 된다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from analyze.skill_board import SkillRow
from collect.training import Course

DIRECT = "직접"
FOUNDATION = "기반"

#: 직접 과정을 기반 과정보다 앞에 둔다.
_KIND_RANK = {DIRECT: 0, FOUNDATION: 1}


@dataclass(frozen=True, slots=True)
class Recommendation:
    skill_id: str
    skill_name: str
    kind: str
    course: Course

    @property
    def key(self) -> str:
        """스킬과 과정의 짝. Notion upsert 키다."""
        return f"{self.skill_id}:{self.course.key}"


def target_skills(rows: Iterable[SkillRow], *, min_demand: int) -> tuple[SkillRow, ...]:
    """부족한 스킬 중 공고가 min_demand 건 이상 요구하는 것. 공고 수 순이다."""
    picked = [row for row in rows if not row.owned and row.demand >= min_demand]
    picked.sort(key=lambda row: (-row.demand, row.name.lower()))
    return tuple(picked)


def search_terms(entry: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    """스킬 사전 항목에서 (검색어, 종류)를 꺼낸다. 직접 과정이 먼저다."""
    training = entry.get("training") or {}
    direct = [(str(term), DIRECT) for term in training.get("direct") or ()]
    foundation = [(str(term), FOUNDATION) for term in training.get("foundation") or ()]
    return tuple(direct + foundation)


def usable(
    course: Course,
    *,
    today: str,
    metro_prefixes: Sequence[str],
    exclude_targets: Sequence[str],
    exclude_title_words: Sequence[str],
) -> bool:
    if course.target in exclude_targets:
        return False
    if any(word in course.title for word in exclude_title_words):
        return False
    if course.start < today:
        return False
    if course.is_remote:
        # 원격 과정의 주소는 기관 주소다. 지역으로 거르지 않는다.
        return True
    return course.address.startswith(tuple(metro_prefixes))


def _order(course: Course) -> tuple[float, str]:
    # 만족도 내림차순, 같으면 빨리 시작하는 순. 만족도가 없는 과정은 뒤로.
    return (-(course.satisfaction or 0.0), course.start)


def pick(
    courses: Iterable[Course],
    *,
    per_skill: int,
    today: str,
    metro_prefixes: Sequence[str],
    exclude_targets: Sequence[str],
    exclude_title_words: Sequence[str],
) -> tuple[Course, ...]:
    """거르고, 과정 단위로 묶고, 좋은 순으로 per_skill 건."""
    best: dict[str, Course] = {}
    for course in courses:
        if not usable(course, today=today, metro_prefixes=metro_prefixes,
                      exclude_targets=exclude_targets, exclude_title_words=exclude_title_words):
            continue
        current = best.get(course.course_id)
        if current is None or course.start < current.start:
            best[course.course_id] = course
    return tuple(sorted(best.values(), key=_order)[:per_skill])


def recommend(
    skills: Iterable[SkillRow],
    entries: Mapping[str, Mapping[str, Any]],
    courses_by_term: Mapping[str, Sequence[Course]],
    *,
    per_skill: int,
    today: str,
    metro_prefixes: Sequence[str],
    exclude_targets: Sequence[str],
    exclude_title_words: Sequence[str],
) -> tuple[Recommendation, ...]:
    """스킬마다 과정을 골라 추천 목록을 만든다."""
    filters = dict(metro_prefixes=metro_prefixes, exclude_targets=exclude_targets,
                   exclude_title_words=exclude_title_words)
    out: list[Recommendation] = []

    for skill in skills:
        kinds: dict[str, str] = {}
        courses: dict[str, Course] = {}
        for term, kind in search_terms(entries.get(skill.skill_id, {})):
            for found in courses_by_term.get(term, ()):
                # 회차마다 courses 에는 항상 남긴다. pick() 이 가장 빨리 시작하는
                # 회차를 고르려면 그 과정의 회차를 전부 봐야 한다. kinds 갱신만
                # 건너뛴다: 더 강한 종류로 이미 잡힌 과정을 약한 종류로 덮으면 안 된다.
                courses[f"{found.course_id}:{found.degree}"] = found
                known = kinds.get(found.course_id)
                if known is not None and _KIND_RANK[known] <= _KIND_RANK[kind]:
                    continue
                kinds[found.course_id] = kind

        # 쿼터를 종류별로 채운다. 직접 과정은 도구를 실제로 다루므로 추천의
        # 핵심이다. 만족도가 높다는 이유로 기반 과정에 밀리면 안 된다.
        chosen: list[Course] = []
        for kind in (DIRECT, FOUNDATION):
            remaining = per_skill - len(chosen)
            if remaining <= 0:
                break
            subset = [course for course in courses.values() if kinds[course.course_id] == kind]
            chosen.extend(pick(subset, per_skill=remaining, today=today, **filters))

        for course in chosen:
            out.append(
                Recommendation(
                    skill_id=skill.skill_id,
                    skill_name=skill.name,
                    kind=kinds[course.course_id],
                    course=course,
                )
            )
    return tuple(out)
