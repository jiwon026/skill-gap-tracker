"""공고가 요구하는 스킬과 보유 스킬의 차이.

판정은 이진이다 — 가지고 있거나, 없거나. `config/profile.yaml`의 level은
여기서 쓰지 않는다. '사용' 수준을 요구 대비 몇 퍼센트로 볼 것인지에
객관적 기준이 없어서, 숫자를 만들면 근거 없는 정밀도가 생긴다.

coverage 는 '요구 중 몇 개를 갖췄나'이지 '이 공고에 붙을 확률'이 아니다.
공고마다 요구 스킬 수가 달라 회사 간 비교에는 쓸 수 없다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from collect.schema import Posting
from extract.companies import Company
from extract.company_size import UNKNOWN
from extract.experience import Match


@dataclass(frozen=True, slots=True)
class Gap:
    matched: tuple[str, ...]
    missing: tuple[str, ...]

    @property
    def coverage(self) -> float:
        """요구 스킬 중 보유 비율. 요구가 없으면 0.0이다.

        요구가 0인 공고에 1.0을 주면 스킬을 한 줄도 안 적은 공고가
        정렬 맨 위로 올라온다.
        """
        total = len(self.matched) + len(self.missing)
        return len(self.matched) / total if total else 0.0

    def labeled(self, names: Mapping[str, str], field: str) -> tuple[str, ...]:
        """스킬 id를 사람이 읽는 이름으로 바꾼다. 사전에 없으면 id를 그대로 쓴다."""
        return tuple(names.get(sid, sid) for sid in getattr(self, field))


@dataclass(frozen=True, slots=True)
class AnalyzedPosting:
    posting: Posting
    company: Company | None
    gap: Gap
    #: 이 공고에 어필할 수 있는 프로젝트 경험. 신호가 많은 순이다.
    #: 스킬 갭이 "무엇이 부족한가"라면 이쪽은 "무엇으로 쓸 것인가"다.
    experiences: tuple[Match, ...] = ()
    #: 화이트리스트 밖 회사의 규모. 소스 회사 정보로 판정한다(run.attach_company_sizes).
    company_size: str | None = None

    @property
    def project_experiences(self) -> tuple[Match, ...]:
        """프로젝트 경험만. 정렬에는 이것만 센다 — 활동 경험은 거의 모든
        공고에 걸려서 함께 세면 순위를 가르지 못한다."""
        return tuple(m for m in self.experiences if m.is_project)

    @property
    def pitch(self) -> str:
        """자소서 첫 문장에 쓸 어필 포인트. 가장 강한 경험 하나를 낸다."""
        return self.experiences[0].pitch if self.experiences else ""

    @property
    def scale(self) -> str:
        """Notion '규모'. 화이트리스트 tier 가 이긴다 — 사람이 정한 값이다."""
        if self.company is not None:
            return self.company.tier
        return self.company_size or UNKNOWN

    @property
    def company_name(self) -> str:
        """화이트리스트 이름을 우선한다. 계열사 표기를 하나로 묶기 위해서다."""
        return self.company.name if self.company else self.posting.company


def compute_gap(required: Iterable[str], owned: Iterable[str]) -> Gap:
    """요구 스킬과 보유 스킬을 맞춰 본다. 순수 함수다.

    보유했지만 요구되지 않은 스킬은 세지 않는다. 이 공고에 대해서는
    정보가 아니기 때문이다.
    """
    required_set, owned_set = set(required), set(owned)
    return Gap(
        matched=tuple(sorted(required_set & owned_set)),
        missing=tuple(sorted(required_set - owned_set)),
    )
