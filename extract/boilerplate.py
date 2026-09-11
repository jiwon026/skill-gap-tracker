"""회사 상용구 문단을 찾아 걷어낸다.

한 회사의 공고들을 나란히 놓고, 여러 건에 토씨까지 똑같이 반복되는 문단을
상용구로 본다. 회사 소개·법무 부록·복지 안내가 여기 걸린다. 스킬은 공고마다
달라지므로 이 규칙에 걸리지 않는다.

비교 대상이 하나뿐이면 아무것도 단정하지 않는다. 반복이 관찰돼야 상용구다.

'반복'은 **서로 다른 공고** 사이의 반복이다. 같은 공고를 지역·트랙만 바꿔
여러 번 올리면 본문 전체가 반복되어 자격요건까지 상용구가 된다. 한 회사가
서울·부산·병역특례로 세 번 올린 공고가 23문단 중 3문단만 남았다. 그래서
거의 같은 공고는 하나로 묶은 뒤 세고, 표본 수(min_sample)도 묶은 뒤의
수로 판단한다.

같은 이유로 한 문단이 상용구가 되려면 최소 두 공고에서 나와야 한다. 비율
기준만 쓰면 표본이 작을 때(0.5 × 2 = 1) 한 번 나온 문단도 기준을 넘는다.
"""
from __future__ import annotations

from collections import Counter
from typing import Iterable, Sequence

#: 이보다 짧은 조각은 반복되어도 상용구로 보지 않는다. 'SQL' 같은 단독
#: 토큰이 문단으로 잡히는 경우를 막는다.
DEFAULT_MIN_CHARS = 20
#: 같은 회사 공고의 이 비율 이상에 나타나면 상용구.
DEFAULT_THRESHOLD = 0.5
#: 서로 다른 공고가 이보다 적으면 아무것도 단정하지 않는다. 두 공고만으로는
#: 공통 문단이 회사 소개인지 비슷한 직무의 같은 자격요건인지 가를 수 없다.
#: (예전에는 2건일 때 컷오프 0.5*2=1.0 이 '한 건에만 나온 문단'까지 잡았다.
#: 지금은 _MIN_REPEATS 가 그것을 막고, 이 값은 한 겹 더 보수적으로 둔다.)
DEFAULT_MIN_SAMPLE = 3

#: 문단 집합이 이만큼 겹치면 같은 공고의 복제로 본다. 실측(2026-09-10):
#: 복제는 0.81~1.00(쿠팡 물류센터 7곳, 지역·트랙만 바꾼 3건), 서로 다른 공고는 중앙값
#: 0.07~0.38. 경계에서 틀리면 '덜 지우는' 쪽으로 틀린다 — 회사 소개가 조금
#: 남는 것이 자격요건을 지우는 것보다 낫다.
DUPLICATE_SIMILARITY = 0.8
#: 상용구가 되려면 적어도 이만큼의 서로 다른 공고에서 반복돼야 한다.
_MIN_REPEATS = 2

Segments = Sequence[str]


def _similarity(a: frozenset[str], b: frozenset[str]) -> float:
    union = a | b
    return len(a & b) / len(union) if union else 1.0


def _distinct(documents: Iterable[Segments]) -> list[tuple[str, ...]]:
    """거의 같은 공고를 하나만 남긴다. 먼저 나온 것을 대표로 쓴다.

    짧은 문단까지 포함해 비교한다. 긴 문단만 보면, 짧은 한 줄씩만 다른
    두 공고가 같은 공고로 묶인다.
    """
    kept: list[tuple[str, ...]] = []
    kept_sets: list[frozenset[str]] = []
    for document in documents:
        segments = tuple(s.strip() for s in document if s.strip())
        as_set = frozenset(segments)
        if any(_similarity(as_set, other) >= DUPLICATE_SIMILARITY for other in kept_sets):
            continue
        kept.append(segments)
        kept_sets.append(as_set)
    return kept


def find_boilerplate(
    documents: Iterable[Segments],
    *,
    threshold: float = DEFAULT_THRESHOLD,
    min_chars: int = DEFAULT_MIN_CHARS,
    min_documents: int | None = None,
    min_sample: int = DEFAULT_MIN_SAMPLE,
) -> frozenset[str]:
    """문단 단위로 문서 빈도를 세어 상용구 집합을 만든다.

    표본이 min_sample 미만이면 빈 집합을 준다. 반복이 관찰되지 않은 상태에서
    비율을 적용하면 유일한 문단이 상용구가 되어 본문이 통째로 사라진다.

    비율(threshold)만으로는 일부 공고에만 붙는 법무 부록을 못 잡는다.
    쿠팡 371건 중 14건에만 붙은 개인정보보호법 조항이 그렇다. min_documents
    를 주면 '똑같은 긴 문단이 N건 이상에 반복되면 템플릿'이라는 절대 기준이
    함께 적용된다. 실제 자격요건도 비슷한 직무 사이에서 반복될 수 있으므로,
    지표 계산 경로에서는 비워두고 사전 보강 후보를 고를 때만 쓴다.
    """
    docs = _distinct(documents)
    if len(docs) < min_sample:
        return frozenset()

    freq: Counter[str] = Counter()
    for segments in docs:
        freq.update({s for s in segments if len(s) >= min_chars})

    ratio_cutoff = max(_MIN_REPEATS, threshold * len(docs))
    absolute_cutoff = max(_MIN_REPEATS, min_documents) if min_documents is not None else None
    return frozenset(
        segment
        for segment, n in freq.items()
        if n >= ratio_cutoff or (absolute_cutoff is not None and n >= absolute_cutoff)
    )


def strip_boilerplate(segments: Segments, boilerplate: frozenset[str]) -> tuple[str, ...]:
    """상용구를 뺀 문단만 남긴다."""
    return tuple(s for s in segments if s.strip() not in boilerplate)
