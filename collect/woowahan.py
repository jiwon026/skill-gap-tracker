"""우아한형제들 채용 API 수집기.

Greenhouse와 달리 목록 응답에 본문이 없다(`recruitContents`가 항상 null).
그래서 수집이 2단이다 — 목록을 페이지 끝까지 훑고, 공고마다 상세를 한 번
더 부른다. 공고 수만큼 요청이 늘어나므로 캐시가 여기서는 선택이 아니다.

상세 조회 키는 `recruitSeq`(숫자)가 아니라 `recruitNumber`(R2609008)다.
숫자를 넣으면 404가 아니라 `code: 9002`인 200이 돌아와서, 조용히 빈
본문만 쌓인다. 식별자를 `recruitNumber`로 통일해 이 함정을 없앴다.

응답에 근무지 필드가 없다. 전 직군이 국내이므로 상수로 채운다.
"""
from __future__ import annotations

import json
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from collect.schema import ROLLING, Posting

LIST_URL = (
    "https://career.woowahan.com/w1/recruits"
    "?category=all&keyword=&employmentType=&page={page}&size=100"
)
DETAIL_URL = "https://career.woowahan.com/w1/recruits/{number}"
POSTING_URL = "https://career.woowahan.com/recruitment/{number}/detail"

TIMEOUT_SEC = 40
#: 페이지를 무한히 도는 사고를 막는 상한. 실제 공고는 100건 남짓이다.
MAX_PAGES = 20

#: 이 비율만큼 본문을 확보하지 못하면 수집이 실패한 것으로 본다.
MIN_DETAIL_RATIO = 0.8

#: 응답에 근무지가 없다. 범위 결정이지 분석 기준이 아니다.
DEFAULT_LOCATION = "대한민국"

#: API가 주는 '2026-09-05 10:20:30'. 소스 간 비교를 하려면 ISO로 맞춰야 한다.
_API_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_KST_SUFFIX = "+09:00"


@dataclass(frozen=True, slots=True)
class BoardSpec:
    company: str
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


def _load(opener: Callable[..., Any], url: str) -> Mapping[str, Any]:
    raw = opener(url, timeout=TIMEOUT_SEC).read()
    return json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)


def fetch_payload(
    spec: BoardSpec,
    cache_path: Path,
    *,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> Mapping[str, Any]:
    """목록과 상세를 한 덩어리로 모아 온다. 캐시가 있으면 네트워크를 타지 않는다.

    상세 한 건이 실패해도 목록 전체를 버리지 않는다. 본문 없는 공고는
    제목과 링크만으로도 지원 후보 목록에서는 쓸모가 있다.
    """
    if cache_path.exists():
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        if _is_usable(cached):
            return cached
        print(f"  - 캐시({cache_path.name})의 본문 확보율이 낮아 다시 받습니다", file=sys.stderr)

    # 요청은 page=0부터 보내는데 응답의 pageNumber는 1로 돌아온다. API가
    # 1-기반이면 첫 두 요청이 같은 페이지를 주고 마지막 장이 빠진다. 어느
    # 규약이든 안전하도록 recruitNumber로 중복을 걷어내며 모은다.
    rows: list[Mapping[str, Any]] = []
    seen: set[str] = set()
    page, total_pages = 0, 1
    while page < total_pages and page < MAX_PAGES:
        data = _load(opener, LIST_URL.format(page=page)).get("data") or {}
        for row in data.get("list") or ():
            number = str(row.get("recruitNumber") or "").strip()
            if number and number in seen:
                continue
            if number:
                seen.add(number)
            rows.append(row)
        total_pages = int(data.get("totalPageNumber") or 1)
        page += 1

    details: dict[str, Any] = {}
    for row in rows:
        number = str(row.get("recruitNumber") or "").strip()
        if not number:
            continue
        try:
            details[number] = _load(opener, DETAIL_URL.format(number=number))
        except (OSError, ValueError) as exc:
            print(f"  ! {number}: 상세 조회 실패, 본문 없이 진행 — {exc}", file=sys.stderr)

    payload = {
        "listing": {"data": {"list": rows}},
        "details": details,
    }
    if not _is_usable(payload):
        print(
            f"  ! 우아한형제들: 공고 {len(rows)}건 중 본문 {len(details)}건만 확보했습니다."
            " 캐시하지 않습니다",
            file=sys.stderr,
        )
        return payload

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return payload


def _years(value: Any) -> int | None:
    """최소 요구 경력. 값이 없거나 이상하면 None(=판단 불가)이다."""
    try:
        years = int(value)
    except (TypeError, ValueError):
        return None
    return years if years >= 0 else None


def _is_usable(payload: Mapping[str, Any]) -> bool:
    """본문을 충분히 확보한 수집인가.

    상세는 공고마다 한 번씩 부르므로 네트워크가 흔들리면 절반이 빈다.
    그 결과가 날짜 기반 캐시로 굳으면 그날은 몇 번을 다시 돌려도 반쪽짜리
    데이터로 분석하게 된다. 캐시 파일을 손으로 지우는 것 말고는 복구
    수단이 없어서, 확보율이 낮으면 캐시를 쓰지도 만들지도 않는다.
    """
    rows = ((payload.get("listing") or {}).get("data") or {}).get("list") or ()
    if not rows:
        return False
    return len(payload.get("details") or {}) >= len(rows) * MIN_DETAIL_RATIO


def _to_iso(value: str | None) -> str | None:
    """'2026-09-05 10:20:30' → '2026-09-05T10:20:30+09:00'.

    형식이 어긋나면 None이다. 원본('상시')을 그대로 흘려보내면 Notion이
    그 행만 400으로 떨궈 영구히 적재되지 않는다. 날짜를 비우면 행은
    정상적으로 올라간다 — 날짜 하나 때문에 공고를 버리는 것은 손해다.
    """
    if not value:
        return None
    try:
        from datetime import datetime

        return datetime.strptime(str(value).strip(), _API_DATE_FORMAT).isoformat() + _KST_SUFFIX
    except ValueError:
        return None


#: 우아한형제들은 상시채용의 마감일을 9999-12-31 로 채운다. 날짜로 옮기면
#: Notion 에 9999년 마감으로 뜬다.
_UNLIMITED_YEAR = "9999"


def _deadline(row: Mapping[str, Any]) -> tuple[str | None, str | None]:
    """(마감일, 마감 표시). 실수집 54건은 전부 isUnlimitedEndDate=True 였다."""
    end = str(row.get("recruitEndDate") or "").strip()
    if row.get("isUnlimitedEndDate") or end.startswith(_UNLIMITED_YEAR):
        return None, ROLLING
    iso = _to_iso(end)
    return (iso[:10] if iso else None), None


def parse_board(
    payload: Mapping[str, Any],
    spec: BoardSpec,
    *,
    fetched_at: str,
) -> CollectResult:
    """목록 + 상세를 Posting 목록으로 옮긴다. 순수 함수다."""
    rows = ((payload.get("listing") or {}).get("data") or {}).get("list") or ()
    details = payload.get("details") or {}

    postings: list[Posting] = []
    skipped: list[Skipped] = []

    for row in rows:
        number = str(row.get("recruitNumber") or "").strip()
        if row.get("isHidden"):
            continue

        detail = (details.get(number) or {}).get("data") or {}
        deadline, deadline_note = _deadline(row)
        try:
            postings.append(
                Posting(
                    source="woowahan",
                    source_id=number,
                    company=spec.company,
                    title=row.get("recruitName") or "",
                    location=DEFAULT_LOCATION,
                    url=POSTING_URL.format(number=number),
                    body_html=detail.get("recruitContents") or "",
                    posted_at=_to_iso(row.get("recruitOpenDate")),
                    fetched_at=fetched_at,
                    departments=(),
                    experience_min=_years(row.get("careerRestrictionMinYears")),
                    deadline=deadline,
                    deadline_note=deadline_note,
                )
            )
        except (ValueError, TypeError) as exc:
            skipped.append(Skipped(source_id=number or "?", reason=str(exc)))

    return CollectResult(postings=tuple(postings), skipped=tuple(skipped))
