"""공고를 프로젝트 경험과 이어 붙인다.

스킬 갭은 "무엇이 부족한가"만 말해 준다. 지원 여부를 정하려면 "무엇으로
어필할 것인가"가 필요하고, 신입에게 그 재료는 경력이 아니라 프로젝트다.

두 가지 일을 한다.

  `proves`  경험이 증명하는 스킬을 보유 목록에 더한다. 도구는 profile.yaml,
            그 도구로 무엇을 했는지는 experience.yaml — 같은 것을 두 군데
            적지 않기 위한 분리다. 이걸 빠뜨리면 실제로 해 본 분석까지
            '부족 스킬'로 잡혀 갭이 실제보다 나쁘게 나온다.

  `signals` 공고에 나오면 그 경험으로 어필할 수 있다는 신호. 스킬 사전과
            달리 여기는 느슨해도 된다 — 틀려도 어필 후보가 하나 늘 뿐,
            지표를 왜곡하지 않는다.

스킬 추출과 달리 **본문을 본다.** 방법론과 주제어는 제목에 안 적히고
자격요건·우대사항 문단에 적히기 때문이다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from extract.matching import alias_to_pattern

#: 어필 근거의 강도 순서. 같은 신호 개수라면 앞선 종류가 이긴다.
#: 활동 경험의 신호(문서화·현업 요청)는 프로젝트의 신호(리텐션·예측 모델)보다
#: 넓게 걸려서, 개수만 세면 약한 근거가 항상 1순위가 된다.
KINDS = ("project", "activity")


@dataclass(frozen=True, slots=True)
class Experience:
    id: str
    name: str
    kind: str
    period: str
    pitch: str
    proves: frozenset[str]
    matcher: re.Pattern[str]


@dataclass(frozen=True, slots=True)
class Match:
    id: str
    name: str
    kind: str
    pitch: str
    signals: tuple[str, ...]

    @property
    def is_project(self) -> bool:
        return self.kind == "project"


@dataclass(frozen=True, slots=True)
class ExperienceBook:
    experiences: tuple[Experience, ...]

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> "ExperienceBook":
        entries = config.get("experiences") or ()
        seen: set[str] = set()
        experiences: list[Experience] = []

        for entry in entries:
            exp_id = str(entry.get("id", "")).strip()
            if not exp_id:
                raise ValueError(f"id 없는 항목: {entry!r}")
            if exp_id in seen:
                raise ValueError(f"중복 id: {exp_id}")
            seen.add(exp_id)

            pitch = str(entry.get("pitch", "")).strip()
            if not pitch:
                raise ValueError(f"{exp_id}: pitch 가 있어야 합니다")

            signals = [str(s).strip() for s in (entry.get("signals") or ()) if str(s).strip()]
            if not signals:
                raise ValueError(f"{exp_id}: signals 가 최소 하나는 있어야 합니다")

            kind = str(entry.get("kind", "project")).strip()
            if kind not in KINDS:
                raise ValueError(f"{exp_id}: 알 수 없는 kind — {kind!r}")

            experiences.append(
                Experience(
                    id=exp_id,
                    name=str(entry.get("name", exp_id)),
                    kind=kind,
                    period=str(entry.get("period", "")),
                    pitch=" ".join(pitch.split()),
                    proves=frozenset(
                        str(s).strip() for s in (entry.get("proves") or ()) if str(s).strip()
                    ),
                    matcher=re.compile(
                        "|".join(f"({alias_to_pattern(s)})" for s in signals), re.IGNORECASE
                    ),
                )
            )

        return cls(experiences=tuple(experiences))

    def proven_skills(self) -> frozenset[str]:
        """경험이 증명하는 스킬 전체. profile.yaml 의 보유 목록에 더해진다."""
        return frozenset().union(*(e.proves for e in self.experiences)) if self.experiences else frozenset()

    def validate_against(self, skill_ids: Iterable[str]) -> None:
        """`proves`의 id가 스킬 사전에 실제로 있는지 확인한다.

        오타는 조용히 실패한다 — 없는 id는 보유 목록에 더해져도 어떤 공고와도
        매칭되지 않아, 그 스킬이 계속 '부족'으로 뜬다. 시작할 때 걸러야 한다.
        """
        unknown = self.proven_skills() - set(skill_ids)
        if unknown:
            raise ValueError(f"config/skills.yaml 에 없는 스킬 id: {sorted(unknown)}")


def match_experiences(
    title: str,
    body_text: str,
    book: ExperienceBook,
) -> tuple[Match, ...]:
    """공고에 어필할 수 있는 경험을 신호가 많은 순으로 돌려준다.

    근거가 없으면 빈 튜플이다. 없는 경험을 지어내지 않는다.
    """
    haystack = f"{title or ''}\n{body_text or ''}".strip()
    if not haystack:
        return ()

    matches: list[tuple[int, int, Match]] = []
    for experience in book.experiences:
        hits = tuple(
            dict.fromkeys(
                m.group(0) for m in experience.matcher.finditer(haystack) if m.group(0)
            )
        )
        if hits:
            matches.append(
                (
                    KINDS.index(experience.kind),
                    -len(hits),
                    Match(
                        id=experience.id,
                        name=experience.name,
                        kind=experience.kind,
                        pitch=experience.pitch,
                        signals=hits,
                    ),
                )
            )

    # 종류가 먼저, 그 안에서 신호 개수. 활동 경험이 프로젝트를 밀어내지 않는다.
    matches.sort(key=lambda row: (row[0], row[1]))
    return tuple(match for _, _, match in matches)
