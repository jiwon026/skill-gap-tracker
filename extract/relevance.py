"""공고가 데이터 직군인지 판정한다.

소스를 늘리면 수집량의 대부분이 무관한 공고다. 스킬 갭 분석은 대상
직군의 공고만 모였을 때 의미가 있으므로, 분석 전에 여기서 좁힌다.

**제목과 부서만 본다.** 본문에는 어느 회사 어느 직군이든 "데이터 기반으로
의사결정합니다"가 적혀 있어서, 본문까지 보면 필터가 아무것도 거르지
못한다. 이 제약은 시그니처로 못 박아 두었다.

**대상 회사에서만 여는 직무(`target`)가 있다.** 신입을 '데이터 분석가'가 아니라
공채 사업 직무(마케팅, SCM)로 뽑는 회사가 많아서다. 모든 회사에 열면 전 산업의
마케터 공고가 쏟아지므로, 호출하는 쪽이 대상 회사인지 알려 줄 때만 쓴다
(2026-09-21 사용자 결정).

판정을 수집이 아니라 추출 계층에 둔 이유는 스냅샷을 원본에 가깝게
유지하기 위해서다. 사전을 고치면 과거 스냅샷으로 다시 돌릴 수 있다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from extract.matching import alias_to_pattern

#: 설정 파일에 올 수 있는 키. adjacent(참고용 직군)는 2026-09-11에 없앴다 —
#: 개발·연구직을 수집에서 빼면서 비어 있었고, 판정은 통과/제외 하나로 줄었다.
_KEYS = ("core", "exclude", "target")


@dataclass(frozen=True, slots=True)
class Term:
    label: str
    matcher: re.Pattern[str]


@dataclass(frozen=True, slots=True)
class Verdict:
    """판정 결과. `matched`가 비면 대상이 아니다.

    `matched`를 남기는 이유는 사전 수정 때문이다. 왜 통과했는지
    모르면 오탐을 고칠 수가 없다.
    """

    matched: tuple[str, ...]

    @property
    def relevant(self) -> bool:
        return bool(self.matched)


@dataclass(frozen=True, slots=True)
class RelevanceDict:
    terms: tuple[Term, ...]
    exclusions: tuple[Term, ...]
    #: 대상 회사 공고에서만 통과 근거가 되는 직무.
    target_terms: tuple[Term, ...] = ()

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> "RelevanceDict":
        unknown = set(config) - set(_KEYS)
        if unknown:
            raise ValueError(f"알 수 없는 항목: {sorted(unknown)}")
        if not config.get("core"):
            raise ValueError("core 항목이 최소 하나는 있어야 합니다")

        def compile_all(aliases: Iterable[str]) -> tuple[Term, ...]:
            return tuple(
                Term(label=alias, matcher=re.compile(alias_to_pattern(alias), re.IGNORECASE))
                for alias in (str(a).strip() for a in aliases)
                if alias
            )

        return cls(
            terms=compile_all(config.get("core") or ()),
            exclusions=compile_all(config.get("exclude") or ()),
            target_terms=compile_all(config.get("target") or ()),
        )


def classify(
    title: str,
    departments: Sequence[str],
    dictionary: RelevanceDict,
    *,
    target: bool = False,
) -> Verdict:
    """제목·부서로 직군을 판정한다. 순수 함수다.

    인자에 본문이 없는 것은 실수가 아니라 설계다. 모듈 docstring 참조.

    **필드를 이어 붙이지 않는다.** 제목과 부서를 공백으로 잇고 검색하면
    어느 쪽에도 없는 별칭이 경계를 걸쳐 만들어진다 — 제목 '물류 데이터'와
    부서 '분석 운영'이 '데이터 분석'으로 잡힌다. 필드마다 따로 본다.

    **제외어를 먼저 본다.** core 의 짧은 항목('Analytics')이 'Analytics
    Engineer' 안에서 잡혀도, 그 제목은 제외어에 먼저 걸려 여기까지 오지 않는다.

    `target=True`(대상 회사 공고)이면 `target` 직무도 통과 근거가 된다. 이때
    **제외어가 걸린 자리가 target 직무가 잡은 범위 안이면 그 제외는 면제한다.**
    '데이터 엔지니어'는 제외어 '엔지니어'를 품고 있지만 통과하고, 'SCM 엔지니어'의
    '엔지니어'나 '마케팅 인재풀'의 '인재풀'은 범위 밖이라 그대로 제외된다.
    """
    tracks = dictionary.target_terms if target else ()
    active = (*dictionary.terms, *tracks)

    def excluded(field: str) -> bool:
        waived = [m.span() for t in tracks for m in t.matcher.finditer(field)]
        return any(
            not any(start <= m.start() and m.end() <= end for start, end in waived)
            for term in dictionary.exclusions
            for m in term.matcher.finditer(field)
        )

    # 제목이 제외어에 걸리면 공고 전체가 대상이 아니다. 제목이 직무를 정한다.
    # 부서가 걸리면 그 부서만 뺀다. 공채·사람인 직무코드는 여러 직무를 부서로
    # 싣는데, '데이터 엔지니어' 하나 때문에 '데이터 분석가'까지 버리면 데이터
    # 직무가 포함된 공채가 전부 사라진다.
    title = title or ""
    if title.strip() and excluded(title):
        return Verdict(matched=())
    kept = tuple(d for d in (departments or ()) if d and d.strip() and not excluded(d))

    fields = tuple(f for f in (title, *kept) if f and f.strip())
    matched = (
        term.label
        for field in fields
        for term in active
        if term.matcher.search(field)
    )
    return Verdict(matched=tuple(dict.fromkeys(matched)))
