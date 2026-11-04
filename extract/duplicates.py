"""소스 간 같은 공고를 알아본다.

어떤 소스는 다른 채용 사이트의 공고를 다시 올린다. 같은 공고가 두 소스로 들어오면
key 가 달라 Notion 에 행이 두 개 생기고, 한쪽에 적은 '상태'가 다른 쪽에는 없다.
그래서 다시 올리는 소스 쪽을 뺀다. 원래 소스가 회사 번호나 경력 숫자를 더 준다.

같은 공고인지는 회사와 제목으로 본다. 다시 올리는 쪽은 제목 앞에 '[회사명]'을
붙이고, 회사명을 다르게 적기도 한다('씨제이커머스(CJ커머스)'와 'CJ커머스'). 그래서
앞머리 대괄호를 떼고, 공백과 기호를 지운 뒤 한쪽이 다른 쪽을 품으면 같다고 본다.
짧은 제목('인턴')은 어디에나 들어가므로 품는 것으로는 같다고 보지 않는다.

법인명과 브랜드명이 갈리는 회사도 있다(한쪽은 'OO회계법인', 다른 쪽은 브랜드명). 제목이
길게 겹치면(공백, 기호를 지우고 12자 이상) 회사 이름은 두 글자만 함께 있어도 같다고 본다.

판정은 수집이 아니라 분석 단계에서 쓴다. 빠진 공고도 '수집됨'으로 남아야 Notion 에서
'마감'이 아니라 '제외됨'으로 표시된다.
"""
from __future__ import annotations

import re
from typing import Collection, Iterable

from collect.schema import Posting

#: 품는 것으로 같다고 볼 최소 길이(공백, 기호를 지운 뒤).
_MIN_CONTAINED = 6
#: 제목이 이만큼 길게 겹치면 회사 이름이 갈려도 두 글자만 함께 있으면 같다고 본다.
_LONG_TITLE = 12

_LEADING_BRACKETS = re.compile(r"^\s*(?:\[[^\]]*\]\s*)+")
_NOISE = re.compile(r"[\W_]+")
_CORPORATE = re.compile(r"\(주\)|주식회사")


def _company(name: str) -> str:
    return _NOISE.sub("", _CORPORATE.sub("", name or "")).lower()


def _title(title: str) -> str:
    return _NOISE.sub("", _LEADING_BRACKETS.sub("", title or "")).lower()


def _overlaps(a: str, b: str, *, minimum: int) -> bool:
    if not a or not b:
        return False
    if a == b:
        return True
    shorter, longer = sorted((a, b), key=len)
    return len(shorter) >= minimum and shorter in longer


def _shares_two_chars(a: str, b: str) -> bool:
    return any(a[i : i + 2] in b for i in range(len(a) - 1))


def _same(repost: Posting, original: Posting) -> bool:
    titles = _title(repost.title), _title(original.title)
    if not _overlaps(*titles, minimum=_MIN_CONTAINED):
        return False
    companies = _company(repost.company), _company(original.company)
    if _overlaps(*companies, minimum=2):
        return True
    return min(map(len, titles)) >= _LONG_TITLE and _shares_two_chars(*companies)


def reposted_duplicates(postings: Iterable[Posting], reposting: Collection[str]) -> frozenset[str]:
    """다시 올리는 소스의 공고 중 원래 소스에도 있는 것의 key. 순수 함수다."""
    rows = list(postings)
    originals = [p for p in rows if p.source not in reposting]
    return frozenset(
        p.key
        for p in rows
        if p.source in reposting and any(_same(p, o) for o in originals)
    )
