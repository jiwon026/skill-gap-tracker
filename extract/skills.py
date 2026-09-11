"""사전 매칭으로 공고 본문에서 요구 스킬을 뽑는다.

LLM 대신 사전을 쓰는 이유는 비용·재현성·검증가능성이다(인수인계서 §2-1).
그 대가로 오탐 통제가 이쪽 책임이 된다. 두 가지를 지킨다.

  - 영문 별칭은 단어 경계를 지킨다. 'SQL'이 'MySQL' 안에서 잡히면
    커버리지가 부풀려져 지표 전체가 거짓말이 된다.
  - 한글 별칭에는 단어 경계를 적용하지 않는다. '퍼널을'의 조사까지
    경계로 치면 미탐이 난다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from extract.matching import alias_to_pattern

#: 사전에 없는 기술 토큰 후보. 한글·일반 영단어를 걸러내기 위한 형태 규칙이다.
_CANDIDATE = re.compile(r"[A-Za-z][A-Za-z0-9+#.\-]{1,24}")

#: 문장을 여는 자리. 여기서의 대문자는 제품명의 근거가 되지 못한다.
_SENTENCE_OPENERS = frozenset(".!?:;*-|>•·–—\n\r\t")


def _looks_like_product_name(token: str, text: str, start: int) -> bool:
    """이 자리의 토큰이 제품·기술 이름처럼 보이는가.

    'Airflow'와 'Please'는 형태가 같다. 둘을 가르는 것은 대문자의 위치다.
    문장 첫머리의 대문자는 문법이지 고유명사의 근거가 아니므로, 문장
    중간에서 대문자로 나타난 적이 있어야 후보로 인정한다. 숫자를 품거나
    전부 대문자인 것(GA4, ETL, AWS)은 위치와 무관하게 통과시킨다.
    """
    if any(ch.isdigit() for ch in token):
        return True
    if len(token) >= 2 and token.isupper():
        return True
    if not any(ch.isupper() for ch in token):
        return False

    cursor = start - 1
    while cursor >= 0 and text[cursor] in " 	":
        cursor -= 1
    return cursor >= 0 and text[cursor] not in _SENTENCE_OPENERS


@dataclass(frozen=True, slots=True)
class Skill:
    id: str
    name: str
    category: str
    matcher: re.Pattern[str]


@dataclass(frozen=True, slots=True)
class SkillDict:
    skills: tuple[Skill, ...]

    @classmethod
    def from_entries(cls, entries: Iterable[Mapping[str, Any]]) -> "SkillDict":
        skills: list[Skill] = []
        seen: set[str] = set()

        for entry in entries:
            skill_id = str(entry.get("id", "")).strip()
            if not skill_id:
                raise ValueError(f"id 없는 항목: {entry!r}")
            if skill_id in seen:
                raise ValueError(f"중복 id: {skill_id}")
            seen.add(skill_id)

            raw_pattern = entry.get("pattern")
            aliases: Sequence[str] = entry.get("aliases") or ()
            if raw_pattern:
                pattern = raw_pattern
            elif aliases:
                pattern = "|".join(alias_to_pattern(a) for a in aliases if a)
            else:
                raise ValueError(f"{skill_id}: aliases 나 pattern 중 하나는 있어야 합니다")

            try:
                matcher = re.compile(pattern, re.IGNORECASE)
            except re.error as exc:
                raise ValueError(f"{skill_id}: 정규식 오류 — {exc}") from exc

            skills.append(
                Skill(
                    id=skill_id,
                    name=entry.get("name", skill_id),
                    category=entry.get("category", "misc"),
                    matcher=matcher,
                )
            )

        return cls(skills=tuple(skills))

    def by_id(self, skill_id: str) -> Skill | None:
        return next((s for s in self.skills if s.id == skill_id), None)

    def unknown_tokens(self, text: str) -> frozenset[str]:
        """사전이 못 잡은 기술 토큰 후보. 다음 주 사전 보강의 입력이 된다."""
        if not text:
            return frozenset()
        matched_spans = [m.span() for s in self.skills for m in s.matcher.finditer(text)]

        def covered(span: tuple[int, int]) -> bool:
            return any(a <= span[0] and span[1] <= b for a, b in matched_spans)

        return frozenset(
            m.group(0).lower()
            for m in _CANDIDATE.finditer(text)
            if _looks_like_product_name(m.group(0), text, m.start())
            and not covered(m.span())
        )


def extract_skills(text: str, dictionary: SkillDict) -> set[str]:
    """본문에서 요구 스킬 id 집합을 뽑는다. 순수 함수다."""
    if not text:
        return set()
    return {skill.id for skill in dictionary.skills if skill.matcher.search(text)}
