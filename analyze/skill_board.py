"""스킬 DB(나의 스킬, 역량 갭)에 올릴 행.

공고 한 건이 아니라 스킬 하나가 한 행이다. 보유 판정은 run.analyze 와 같은
규칙을 쓴다. profile.yaml 의 도구에 experience.yaml 이 증명하는 스킬을 더한다.
둘이 갈라지면 공고 DB 의 '부족 스킬'과 역량 갭 페이지가 서로 다른 말을 한다.

행은 보유했거나 모집중 공고가 요구하는 스킬만 만든다. 사전의 나머지는 어느
페이지에서도 쓸 데가 없고, 50개 넘는 행이 보기만 흐린다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from analyze.gap import AnalyzedPosting
from extract.experience import ExperienceBook

#: profile.yaml 에 적힌 도구라는 근거. 경험 이름과 나란히 '근거' 열에 들어간다.
TOOL_EVIDENCE = "도구"


@dataclass(frozen=True, slots=True)
class SkillRow:
    skill_id: str
    name: str
    category: str
    owned: bool
    #: 보유 근거. 도구 목록에 있으면 TOOL_EVIDENCE, 증명한 경험이 있으면 그 이름.
    evidence: tuple[str, ...]
    #: 모집중 공고 중 이 스킬을 요구하는 수. 보유 여부와 상관없이 센다.
    demand: int
    companies: tuple[str, ...]


def build_skill_rows(
    analyzed: Iterable[AnalyzedPosting],
    *,
    skill_entries: Iterable[Mapping[str, Any]],
    tool_ids: Iterable[str],
    experiences: ExperienceBook,
) -> tuple[SkillRow, ...]:
    """분석 결과를 스킬 단위로 모은다. 순수 함수다."""
    entries = {str(entry["id"]): entry for entry in skill_entries}
    tools = frozenset(tool_ids)
    owned = tools | experiences.proven_skills()

    demand: dict[str, int] = {}
    companies: dict[str, set[str]] = {}
    for row in analyzed:
        for skill_id in (*row.gap.matched, *row.gap.missing):
            demand[skill_id] = demand.get(skill_id, 0) + 1
            companies.setdefault(skill_id, set()).add(row.company_name)

    rows = []
    for skill_id in set(owned) | set(demand):
        entry = entries.get(skill_id, {})
        proven_by = tuple(e.name for e in experiences.experiences if skill_id in e.proves)
        rows.append(
            SkillRow(
                skill_id=skill_id,
                name=str(entry.get("name", skill_id)),
                category=str(entry.get("category", "")),
                owned=skill_id in owned,
                evidence=((TOOL_EVIDENCE,) if skill_id in tools else ()) + proven_by,
                demand=demand.get(skill_id, 0),
                companies=tuple(sorted(companies.get(skill_id, ()))),
            )
        )

    # 역량 갭은 '무엇을 먼저 배울까'를 보는 곳이다. 부족한 것을 공고 수 순으로 앞에 둔다.
    rows.sort(key=lambda r: (r.owned, -r.demand, r.name.lower()))
    return tuple(rows)
