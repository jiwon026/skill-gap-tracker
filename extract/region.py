"""근무지가 대상 지역(수도권)인가.

판정 순서는 제목의 지역 표기 > 지역 필드 > 모르면 통과다.

  - 제목을 먼저 본다. 지역 필드에 본사 주소가 들어오는 공고가 있다.
    '…, 부산' 공고의 지역 필드가 서울 강남구였다.
  - 모르면 통과시킨다. 우아한형제들은 근무지를 아예 주지 않는다. '모름'을
    제외로 치면 한 소스가 통째로 사라진다. 지원 가능한 공고를 놓치는 쪽이,
    제목에 '부산'이 보이는 공고를 하나 더 보는 것보다 손해다.

제목에서는 지역명이 **독립된 단어일 때만** 센다. '제주항공', 'BNK부산은행',
'서울대학교병원'의 지역명은 근무지가 아니다. 이걸 근무지로 읽으면 수도권
공고가 조용히 사라진다. 지역 필드는 '서울특별시', '부산광역시'처럼 붙여
쓰므로 부분 일치로 찾는다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from extract.matching import alias_to_pattern

#: 제목에서 지역명 바로 뒤에 붙어도 근무지로 읽는 말('부산센터', '부산시').
_SITE_SUFFIXES = "센터|지사|캠프|오피스|근무|지역|광역시|특별시|특별자치시|특별자치도|시|도"
#: 지역명이 이 문자와 붙어 있으면 다른 이름의 일부다.
_WORD_CHARS = "가-힣A-Za-z"


def _tokens(values: Iterable[Any] | None) -> list[str]:
    return [str(v).strip() for v in (values or ()) if str(v).strip()]


def _loose(tokens: list[str]) -> re.Pattern[str]:
    """지역 필드용. 한글은 부분 일치, 영문은 단어 경계(extract.matching 규칙)."""
    return re.compile("|".join(alias_to_pattern(t) for t in tokens), re.IGNORECASE)


def _standalone(tokens: list[str]) -> re.Pattern[str]:
    """제목용. 다른 글자와 붙은 지역명('부산은행', '제주항공')은 세지 않는다."""
    parts = []
    for token in tokens:
        if token.isascii():
            parts.append(alias_to_pattern(token))
        else:
            parts.append(
                rf"(?<![{_WORD_CHARS}]){re.escape(token)}"
                rf"(?=(?:{_SITE_SUFFIXES})?(?![{_WORD_CHARS}]))"
            )
    return re.compile("|".join(parts), re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class RegionRule:
    inside_location: re.Pattern[str]
    outside_location: re.Pattern[str]
    inside_title: re.Pattern[str]
    outside_title: re.Pattern[str]

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> "RegionRule":
        include = _tokens(config.get("include"))
        exclude = _tokens(config.get("exclude"))
        if not include:
            raise ValueError("include 가 비어 있습니다. 대상 지역을 적어야 합니다")
        if not exclude:
            # 비워 두면 '모르면 통과' 규칙 때문에 모든 공고가 통과한다.
            raise ValueError("exclude 가 비어 있습니다. 비우면 아무것도 걸러지지 않습니다")
        return cls(
            inside_location=_loose(include),
            outside_location=_loose(exclude),
            inside_title=_standalone(include),
            outside_title=_standalone(exclude),
        )


def in_region(title: str | None, location: str | None, rule: RegionRule) -> bool:
    """대상 지역의 공고인가. 순수 함수다."""
    title, location = title or "", location or ""

    title_inside = bool(rule.inside_title.search(title))
    if rule.outside_title.search(title) and not title_inside:
        return False
    if title_inside:
        return True
    if rule.inside_location.search(location):
        return True
    if rule.outside_location.search(location):
        return False
    return True
