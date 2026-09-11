"""사람인 오픈API 수집기.

국내 중견·대기업 커버리지의 주력 소스다. 다른 어떤 소스도 무신사·컬리·
11번가·SSG를 한 번에 주지 않는다.

**이 소스에는 공고 본문이 없다.** API가 주는 것은 메타데이터와 쉼표로
구분된 `keyword`뿐이다. 스킬 추출이 아무것도 못 먹으면 갭 분석에서
이 소스의 공고만 통째로 빠지므로, 키워드·직무코드·산업명을 텍스트로
합쳐 `body_html` 자리에 넣는다.

그것은 진짜 본문이 아니다. 그래서 `BODY_NOTICE` 한 줄을 함께 심어,
나중에 "이 공고의 스킬 목록은 본문 기반이 아니다"를 데이터만 보고
판별할 수 있게 했다. 이 표식이 없으면 커버리지 통계가 조용히 거짓말을
하게 된다.

하루 500콜 제한이 있다. 키워드 하나당 (전체건수 / 110)번 호출되므로,
`config/sources.yaml`에서 키워드를 늘릴 때는 예산을 계산해야 한다.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping

from collect.schema import NOTICE_PREFIX, ROLLING, Posting

BASE_URL = "https://oapi.saramin.co.kr/job-search"
TIMEOUT_SEC = 40

#: API가 한 번에 주는 최대치. 콜 예산을 아끼려면 항상 최대로 받는다.
PAGE_SIZE = 110
#: 폭주 방지 상한. 키워드 하나가 콜 예산을 다 먹는 것을 막는다.
MAX_PAGES = 5

#: 합성 본문임을 나타내는 표식. 지우지 말 것 — 모듈 docstring 참조.
BODY_NOTICE = f"{NOTICE_PREFIX} 사람인 API에는 공고 본문이 없습니다. 아래는 키워드·직무코드 요약입니다."

#: API가 주는 '2026-09-01 10:00:00 +0900'.
_API_DATE_FORMAT = "%Y-%m-%d %H:%M:%S %z"


@dataclass(frozen=True, slots=True)
class KeywordSpec:
    keyword: str
    domain: str
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class Skipped:
    source_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class CollectResult:
    postings: tuple[Posting, ...]
    skipped: tuple[Skipped, ...]


def access_key_from_env() -> str | None:
    """키가 없으면 None. 호출부는 이 소스를 통째로 건너뛴다."""
    key = os.environ.get("SARAMIN_ACCESS_KEY", "").strip()
    return key or None


def _build_url(spec: KeywordSpec, access_key: str, start: int) -> str:
    query = urllib.parse.urlencode(
        {
            "access-key": access_key,
            "keywords": spec.keyword,
            "count": PAGE_SIZE,
            "start": start,
            "sort": "pd",  # 최신 등록순
        }
    )
    return f"{BASE_URL}?{query}"


def _as_list(value: Any) -> list[Any]:
    """결과가 1건일 때 API가 배열 대신 객체를 준다. 그 차이를 여기서 흡수한다."""
    if value is None:
        return []
    return list(value) if isinstance(value, list) else [value]


def fetch_payload(
    spec: KeywordSpec,
    cache_path: Path,
    access_key: str,
    *,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> Mapping[str, Any]:
    """키워드 하나의 검색 결과를 끝까지 모은다. 캐시가 있으면 네트워크를 타지 않는다."""
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))

    jobs: list[Any] = []
    total = None
    for page in range(MAX_PAGES):
        raw = opener(_build_url(spec, access_key, page), timeout=TIMEOUT_SEC).read()
        body = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
        block = body.get("jobs") or {}
        batch = _as_list(block.get("job"))
        jobs.extend(batch)

        if total is None:
            reported = str(block.get("total") or "").strip()
            if not reported.isdigit() or reported == "0":
                # total 이 사라지면 len(jobs) >= 0 이 즉시 참이 되어 110건에서
                # 조용히 멈춘다. 커버리지가 1/N로 떨어진 것을 아무도 모른다.
                print(f"  ! 사람인/{spec.keyword}: total 을 읽지 못했습니다"
                      f" (받은 값 {reported!r}). 첫 페이지만 사용합니다", file=sys.stderr)
            total = int(reported) if reported.isdigit() else len(batch)
        if len(batch) < PAGE_SIZE or len(jobs) >= total:
            break
    else:
        if len(jobs) < (total or 0):
            print(f"  ! 사람인/{spec.keyword}: {total}건 중 {len(jobs)}건에서 잘렸습니다"
                  f" (MAX_PAGES={MAX_PAGES}). 콜 예산 보호를 위한 상한입니다", file=sys.stderr)

    payload = {"jobs": {"total": str(total or len(jobs)), "job": jobs}}
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return payload


def _is_closed(job: Mapping[str, Any]) -> bool:
    """마감 공고인가.

    `active`가 null이나 빈 문자열로 오는 경우가 있다. int()를 그대로 쓰면
    공고 한 건이 파이프라인 전체를 죽인다. 판정 불가는 '열려 있음'으로 본다 —
    살아 있는 공고를 버리는 쪽이 더 손해다.
    """
    raw = str(job.get("active", "1")).strip().lower()
    return raw in ("0", "false", "n")


def _to_iso(value: str | None) -> str | None:
    """API 날짜를 ISO로. 파싱 실패는 None이다.

    원본 문자열('상시채용')을 그대로 흘려보내면 Notion이 그 행만 400으로
    떨궈 영구히 적재되지 않는다. 게시일을 비우면 행은 정상적으로 올라간다.
    """
    if not value:
        return None
    try:
        return datetime.strptime(str(value).strip(), _API_DATE_FORMAT).isoformat()
    except ValueError:
        return None


def _years(value: Any) -> int | None:
    """최소 요구 경력. 값이 없거나 이상하면 None(=판단 불가)이다."""
    try:
        years = int(value)
    except (TypeError, ValueError):
        return None
    return years if years >= 0 else None


#: 날짜 없이 끝나는 마감 형식(close-type 코드 → 표시). 1 은 '접수 마감일'이라
#: expiration-date 를 쓴다. 이 형식들에는 사람인이 먼 미래 마감일을 붙여
#: 보내기도 해서, 코드가 있으면 날짜를 믿지 않는다.
_OPEN_ENDED = {"2": "채용시 마감", "3": ROLLING, "4": "수시채용"}


def _deadline(job: Mapping[str, Any]) -> tuple[str | None, str | None]:
    """(마감일, 마감 표시)."""
    close_type = job.get("close-type")
    code = str(close_type.get("code", "")).strip() if isinstance(close_type, Mapping) else ""
    if code in _OPEN_ENDED:
        return None, _OPEN_ENDED[code]
    iso = _to_iso(job.get("expiration-date"))
    return (iso[:10] if iso else None), None


def _synthesize_body(job: Mapping[str, Any]) -> str:
    """키워드·직무코드·산업명을 본문 자리에 놓을 텍스트로 합친다.

    진짜 본문이 아니라는 표식을 맨 앞에 둔다. 모듈 docstring 참조.
    """
    position = job.get("position") or {}
    parts = [
        BODY_NOTICE,
        position.get("title") or "",
        (job.get("keyword") or "").replace(",", ", "),
        ((position.get("job-code") or {}).get("name") or "").replace(",", ", "),
        (position.get("industry") or {}).get("name") or "",
        (position.get("experience-level") or {}).get("name") or "",
    ]
    return "\n".join(part for part in parts if part)


def parse_search(
    payload: Mapping[str, Any],
    spec: KeywordSpec,
    *,
    fetched_at: str,
) -> CollectResult:
    """검색 결과를 Posting 목록으로 옮긴다. 순수 함수다."""
    postings: list[Posting] = []
    skipped: list[Skipped] = []

    for job in _as_list((payload.get("jobs") or {}).get("job")):
        if not isinstance(job, Mapping):
            skipped.append(Skipped(source_id="?", reason=f"공고가 객체가 아님: {type(job).__name__}"))
            continue

        source_id = str(job.get("id", "")).strip()
        if _is_closed(job):
            continue

        position = job.get("position") or {}
        job_codes = (position.get("job-code") or {}).get("name") or ""
        deadline, deadline_note = _deadline(job)

        try:
            postings.append(
                Posting(
                    source="saramin",
                    source_id=source_id,
                    # 회사명이 비면 run.analyze 의 회사별 상용구 판정에서
                    # 서로 다른 회사가 "" 하나로 뭉친다. 키워드로 폴백한다.
                    company=(((job.get("company") or {}).get("detail") or {}).get("name")
                             or f"(미상) {spec.keyword}"),
                    title=position.get("title") or "",
                    location=(position.get("location") or {}).get("name") or "",
                    url=job.get("url") or "",
                    body_html=_synthesize_body(job),
                    posted_at=_to_iso(job.get("posting-date")),
                    fetched_at=fetched_at,
                    departments=tuple(c.strip() for c in job_codes.split(",") if c.strip()),
                    experience_min=_years((position.get("experience-level") or {}).get("min")),
                    deadline=deadline,
                    deadline_note=deadline_note,
                )
            )
        except (ValueError, TypeError) as exc:
            skipped.append(Skipped(source_id=source_id or "?", reason=str(exc)))

    return CollectResult(postings=tuple(postings), skipped=tuple(skipped))
