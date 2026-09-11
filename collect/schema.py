"""소스 중립적인 공고 레코드.

수집 계층의 유일한 출력 타입이다. 소스별 응답 스키마의 차이는 이 경계
안에서 흡수되고, 밖으로 새 나가지 않는다. 본문은 정제하지 않은 HTML
그대로 담는다 — 스냅샷의 충실도를 유지해야 분석 로직을 바꿔도 과거
데이터로 재계산할 수 있다.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from typing import Any, Mapping

_REQUIRED = ("source", "source_id", "title", "url")

#: 마감일 없이 열려 있는 공고의 표시. 소스가 명시적으로 그렇다고 할 때만 쓴다.
ROLLING = "상시채용"

#: 수집기가 본문 자리에 넣는 표식의 머리말. 표식은 본문이 아니라 '읽을
#: 본문이 없다'는 메타데이터라, 추출 단계는 이 머리말로 시작하는 문단을
#: 읽지 않는다. 이 규칙이 없던 때 사람인 표식의 '키워드'·'API'와 이미지 본문
#: 표식의 '이미지라'(→ Jira)·'텍스트'가 가짜 스킬과 가짜 어필을 만들었다.
#: 표식은 반드시 독립된 문단(블록 태그나 줄바꿈으로 분리)으로 넣을 것.
NOTICE_PREFIX = "[수집 표식]"


def is_notice(segment: str) -> bool:
    """수집기가 심은 표식 문단인가."""
    return segment.lstrip().startswith(NOTICE_PREFIX)


@dataclass(frozen=True, slots=True)
class Posting:
    source: str
    source_id: str
    company: str
    title: str
    location: str
    url: str
    body_html: str
    posted_at: str | None
    fetched_at: str
    departments: tuple[str, ...] = field(default=())
    #: 소스가 준 최소 요구 경력(년). None 은 '그 소스가 숫자를 주지 않는다'는
    #: 뜻이지 '경력이 필요 없다'는 뜻이 아니다. Greenhouse 가 그렇다.
    #: 기본값이 있으므로 이 필드가 없는 과거 스냅샷도 그대로 읽힌다.
    experience_min: int | None = None
    #: 지원 마감일(ISO 날짜). 소스가 날짜를 줄 때만 있다.
    deadline: str | None = None
    #: 날짜 없이 끝나는 공고의 표시 — '상시채용', '채용시 마감', '수시채용'.
    #: deadline 보다 우선한다. 둘 다 None 이면 '모름'이지 '상시채용'이 아니다.
    deadline_note: str | None = None
    #: 소스 안에서 회사를 가리키는 id(소스의 회사 번호). 규모를
    #: 조회할 때 쓴다. 회사명은 표기가 갈라져 조회 키가 되지 못한다.
    company_id: str | None = None

    def __post_init__(self) -> None:
        for name in _REQUIRED:
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name}: 비어 있을 수 없습니다 (source_id={self.source_id!r})")
        object.__setattr__(self, "departments", tuple(self.departments))

    @property
    def key(self) -> str:
        """소스를 가로질러 유일한 식별자. 소스가 다르면 다른 공고로 센다."""
        return f"{self.source}:{self.source_id}"

    def to_json(self) -> str:
        """JSONL 한 줄. 개행이 섞이지 않도록 이스케이프된 형태로 낸다."""
        return json.dumps(asdict(self), ensure_ascii=False, separators=(",", ":"))

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "Posting":
        known = {f.name for f in fields(cls)}
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"알 수 없는 필드: {sorted(unknown)}")
        return cls(**{k: v for k, v in data.items() if k in known})


#: 법정 기업 규모. 소스 표기('중견기업')는 수집기가 이 표기로 맞춘다.
LEGAL_SIZES = ("대기업", "중견", "중소")


@dataclass(frozen=True, slots=True)
class CompanyProfile:
    """소스가 알려 준 회사 정보. 모르는 값은 None 이다.

    공고와 달리 스냅샷에 넣지 않는다. 회사 정보는 공고 원문이 아니고,
    분석 대상 회사만 조회해 따로 캐시한다(collect/company_cache.py).
    """

    #: 법정 규모 — LEGAL_SIZES 중 하나. 주는 소스만 있다.
    size_class: str | None = None
    #: 사원수 상한. 실제 인원을 주는 소스는 그 수, 구간을 주는 소스는 구간의
    #: 상한('51~300명' → 300).
    headcount_max: int | None = None
    founded_year: int | None = None

    def __post_init__(self) -> None:
        if self.size_class is not None and self.size_class not in LEGAL_SIZES:
            raise ValueError(f"size_class: {LEGAL_SIZES} 중 하나여야 합니다 — {self.size_class!r}")
