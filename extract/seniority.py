"""공고가 신입이 지원할 수 있는 자리인가.

직군 필터를 통과해도 대부분이 지원할 수 없는 자리였다. 실수집 36건 중
15건이 Senior·Staff·Principal·Director였다.

판정 재료가 소스마다 다르다. 우아한형제들(`careerRestrictionMinYears`)과
사람인(`experience-level.min`)은 최소 요구 경력을 숫자로 주지만,
Greenhouse는 주지 않는다. 그래서 믿을 만한 순서대로 본다.

    소스가 준 숫자 > 제목의 신입 표기 > 본문 자격요건의 연수 > 제목의 직급 표기

본문 단계가 없던 때, 직군 필터를 통과한 쿠팡 공고 7건이 전부 2~5년
경력직인데 목록에 올라왔다. 제목에 Senior 가 없었을 뿐, 자격요건에는
"최소 5년 이상"이 적혀 있었다.

추정은 틀릴 수 있다. 어느 쪽으로 틀릴지는 정해 두었다: 판단 근거가 전혀
없으면 통과시킨다. 지원 가능한 공고를 놓치는 쪽이, 쓸데없는 공고를 하나 더
보는 것보다 손해다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from extract.matching import alias_to_pattern


@dataclass(frozen=True, slots=True)
class SeniorityRule:
    max_years: int
    senior: re.Pattern[str] | None
    junior: re.Pattern[str] | None

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> "SeniorityRule":
        raw = config.get("max_years")
        if raw is None:
            raise ValueError("max_years 가 있어야 합니다")
        try:
            max_years = int(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"max_years 가 정수가 아닙니다: {raw!r}") from exc
        if max_years < 0:
            raise ValueError(f"max_years 는 음수일 수 없습니다: {max_years}")

        return cls(
            max_years=max_years,
            senior=_compile(config.get("senior_markers")),
            junior=_compile(config.get("junior_markers")),
        )


def _compile(aliases: Iterable[str] | None) -> re.Pattern[str] | None:
    cleaned = [str(a).strip() for a in (aliases or ()) if str(a).strip()]
    if not cleaned:
        return None
    return re.compile("|".join(alias_to_pattern(a) for a in cleaned), re.IGNORECASE)


def is_entry_level(
    title: str,
    experience_min: int | None,
    rule: SeniorityRule,
    *,
    stated_years: int | None = None,
) -> bool:
    """신입이 지원할 수 있는 공고인가. 순수 함수다.

    `experience_min`은 소스가 준 최소 요구 경력(년)이다. None이면 그 소스가
    숫자를 주지 않는다는 뜻이지, 경력이 필요 없다는 뜻이 아니다.

    `stated_years`는 본문 자격요건에서 읽은 연수다(`years_from_requirements`).
    """
    if experience_min is not None:
        # 숫자가 있으면 제목을 보지 않는다. '신입'이라 적혀 있어도 최소경력
        # 3년이면 신입 공고가 아니다.
        return experience_min <= rule.max_years

    text = title or ""
    if rule.junior is not None and rule.junior.search(text):
        # '신입/경력' 동시 채용. 직급 표기가 함께 있어도 지원할 수 있다.
        # 본문의 '3년 이상'도 경력 트랙 이야기라 이것을 뒤집지 못한다.
        return True
    if stated_years is not None:
        # 자격요건에 적힌 숫자는 제목의 직급 표기보다 정확하다.
        return stated_years <= rule.max_years
    if rule.senior is not None and rule.senior.search(text):
        return False
    return True


# ── 본문 자격요건에서 연수 읽기 ────────────────────────────────────────
#
# 틀리면 지원 가능한 공고가 조용히 사라지는 함수다. 그래서 정확도를 재현율
# 보다 앞에 둔다 — 필수 자격 헤더 아래에서만 읽고, 헤더가 없으면 읽지 않는다.

#: 경력 연수 표현. '3년', '3~5년', '8+ years', '8-10 years'. 첫 숫자(하한)를 잡는다.
#: '4년제 대학교'는 학위 종류라 뺀다. 앞에 숫자가 붙은 '2026년'도 빠진다.
_YEARS = re.compile(
    r"(?<!\d)(\d{1,2})\s*(?:[~\-–]\s*\d{1,2}\s*)?(?:년(?!제)|\+?\s*years?)",
    re.IGNORECASE,
)
#: 연수가 경력 이야기일 때만 센다. '창립 10년'은 요구 사항이 아니다.
_EXPERIENCE_CONTEXT = re.compile(r"경력|경험|실무|experience", re.IGNORECASE)
#: 필수 섹션 안이라도 그 줄이 스스로 '우대'라고 말하면 필수가 아니다.
_PREFERRED_INLINE = re.compile(r"우대|preferred|nice\s+to\s+have|a\s+plus", re.IGNORECASE)

#: 헤더는 키워드로 '시작'하는 짧은 문단이다. 끝에 'preferred'가 붙은
#: 'English proficiency preferred'는 내용이지 헤더가 아니다.
_LEAD = r"^[\[\(\*#\-\s\d\.]*"
_PREFERRED_HEADER = re.compile(_LEAD + r"(?:우대|preferred|nice\s+to\s+have|bonus|plus)", re.IGNORECASE)
_REQUIRED_HEADER = re.compile(
    _LEAD + r"(?:자격\s*요건|지원\s*자격|자격\s*사항|필수|basic\s+qualification|"
    r"minimum\s+qualification|qualification|requirement|what\s+we\s+are\s+looking\s+for|"
    r"who\s+you\s+are|what\s+you\s+need)",
    re.IGNORECASE,
)
_OTHER_HEADER = re.compile(
    _LEAD + r"(?:주요\s*업무|담당\s*업무|업무|직무\s*소개|회사\s*소개|조직\s*소개|전형|근무|"
    r"참고|혜택|복지|채용\s*절차|지원\s*서류|기타|개인정보|서류\s*반환|company|about|role|"
    r"what\s+you\s+will|key\s+responsibilit|responsibilit|recruitment|details|things\s+to|"
    r"privacy|document|equal|benefit)",
    re.IGNORECASE,
)
_HEADER_MAX_LEN = 40


def _header_kind(segment: str) -> str | None:
    if len(segment) > _HEADER_MAX_LEN or _YEARS.search(segment):
        # 연수가 적힌 줄은 헤더가 아니다. '업무 관련 경력 3년 이상'이
        # '업무' 헤더로 읽혀 필수 섹션을 닫아버리는 것을 막는다.
        return None
    # 'Preferred Qualifications'가 'Qualifications'로 먼저 잡히지 않게 우대부터 본다.
    if _PREFERRED_HEADER.match(segment):
        return "preferred"
    if _REQUIRED_HEADER.match(segment):
        return "required"
    if _OTHER_HEADER.match(segment):
        return "other"
    return None


def years_from_requirements(segments: Iterable[str]) -> int | None:
    """본문 필수 자격 섹션에 적힌 최소 요구 경력(년). 없으면 None.

    선택지가 여럿이면('대졸 2년 또는 고졸 5년') 가장 낮은 값을 낸다. 지원
    가능한 쪽이 기준이다.
    """
    section: str | None = None
    found: list[int] = []

    for raw in segments:
        segment = raw.strip()
        if not segment:
            continue
        kind = _header_kind(segment)
        if kind is not None:
            section = kind
            continue
        if section != "required":
            continue
        if _PREFERRED_INLINE.search(segment) or not _EXPERIENCE_CONTEXT.search(segment):
            continue
        found.extend(int(m.group(1)) for m in _YEARS.finditer(segment))

    return min(found) if found else None
