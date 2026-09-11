"""공고에 찍힌 회사명을 화이트리스트의 회사로 묶는다.

같은 회사가 표기를 여러 개 쓴다. 쿠팡은 계열사명(쿠팡풀필먼트서비스,
쿠팡로지스틱스서비스)으로, 신세계는 SSG.COM·지마켓으로 공고를 올린다.
묶이지 않으면 tier 정렬이 어긋나고, 사람인처럼 전 산업이 섞여 오는
소스를 좁힐 수가 없다.

별칭 매칭 규칙은 스킬 사전과 공유한다(`extract.matching`). 짧은 영문
별칭(CLS, SSG)이 다른 단어 안에서 잡히는 것을 막는 것이 여기서 제일
중요하다 — 한 번 잘못 묶이면 전혀 다른 회사가 타겟 목록에 올라온다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from extract.matching import alias_to_pattern

#: 리포트 정렬 순서. 목록에 없는 회사는 맨 뒤로 간다.
TIER_ORDER = ("대기업", "중견")


@dataclass(frozen=True, slots=True)
class Company:
    name: str
    tier: str
    segment: str
    matcher: re.Pattern[str]


@dataclass(frozen=True, slots=True)
class CompanyBook:
    companies: tuple[Company, ...]

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> "CompanyBook":
        entries = config.get("companies") or ()
        companies: list[Company] = []
        seen: set[str] = set()

        for entry in entries:
            name = str(entry.get("name", "")).strip()
            if not name:
                raise ValueError(f"name 없는 항목: {entry!r}")
            if name in seen:
                raise ValueError(f"중복 회사명: {name}")
            seen.add(name)

            tier = str(entry.get("tier", "")).strip()
            if tier not in TIER_ORDER:
                raise ValueError(f"{name}: 알 수 없는 tier — {tier!r}")

            aliases = [str(a).strip() for a in (entry.get("aliases") or ()) if str(a).strip()]
            if not aliases:
                raise ValueError(f"{name}: aliases 가 최소 하나는 있어야 합니다")

            companies.append(
                Company(
                    name=name,
                    tier=tier,
                    segment=str(entry.get("segment", "")),
                    matcher=re.compile(
                        "|".join(alias_to_pattern(a) for a in aliases), re.IGNORECASE
                    ),
                )
            )

        book = cls(companies=tuple(companies))
        book._reject_overlapping_aliases(entries)
        return book

    @staticmethod
    def _reject_overlapping_aliases(entries: Iterable[Mapping[str, Any]]) -> None:
        """두 회사가 같은 표기를 주장하면 설정 오류다.

        `identify`는 '먼저 걸린 것'을 쓰므로, 겹치는 별칭은 YAML 순서에 따라
        조용히 다른 회사로 귀속된다. 목록이 27개사에서 계속 늘어날 것이므로
        사람 눈에 맡기지 않는다.
        """
        owner: dict[str, str] = {}
        for entry in entries:
            name = str(entry.get("name", "")).strip()
            for alias in entry.get("aliases") or ():
                key = str(alias).strip().lower()
                if key in owner and owner[key] != name:
                    raise ValueError(f"별칭 중복: {alias!r} — {owner[key]} 와 {name}")
                owner[key] = name

    def identify(self, raw_name: str) -> Company | None:
        """공고의 회사명 표기를 화이트리스트 항목으로 옮긴다.

        먼저 걸린 것을 쓴다. 별칭이 겹치는 회사를 만들지 않는 것은
        설정 파일의 책임이다.
        """
        text = (raw_name or "").strip()
        if not text:
            return None
        return next((c for c in self.companies if c.matcher.search(text)), None)

    def tier_rank(self, tier: str | None) -> int:
        """정렬 키. 목록 밖(None)은 항상 뒤로 간다."""
        if tier in TIER_ORDER:
            return TIER_ORDER.index(tier)
        return len(TIER_ORDER)
