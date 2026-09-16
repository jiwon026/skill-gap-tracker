"""고용24 국민내일배움카드 훈련과정 수집기.

공고 수집기와 같은 계약을 따른다. 캐시 우선으로 받고, 파싱은 순수 함수이며,
한 줄이 깨져도 나머지는 살아남는다.

이 API 의 특징 두 가지를 여기서 흡수한다.

  - 같은 과정이 회차마다 한 줄씩 나온다(`trprId` 가 같다). 묶는 일은
    analyze/recommend.py 가 한다. 여기서는 받은 줄을 그대로 낸다.
  - 한 번에 100회차까지만 준다. 넓은 검색어는 회차가 수천이라 여러 페이지를
    읽어야 한다. 빈 페이지를 만나면 거기서 멈춘다.

정렬은 만족도 내림차순(sortCol=5)이다. 스킬당 다섯 건만 올리므로, 첫 페이지가
좋은 과정으로 채워지는 편이 유리하다.
"""
from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping

LIST_URL = "https://www.work24.go.kr/cm/openApi/call/hr/callOpenApiSvcInfo310L01.do"
DETAIL_URL = "https://www.work24.go.kr/cm/openApi/call/hr/callOpenApiSvcInfo310L02.do"
TIMEOUT_SEC = 40
PAGE_SIZE = 100
REQUEST_INTERVAL_SEC = 0.4

#: 주말 구분 코드. 화면에 그대로 쓴다.
WEEKEND_LABELS = {"1": "주말", "2": "주중 주말 혼합", "3": "주중", "9": "해당 없음"}

_SLUG = re.compile(r"[^0-9A-Za-z가-힣]+")


@dataclass(frozen=True, slots=True)
class Course:
    course_id: str
    degree: str
    institution_id: str
    title: str
    institution: str
    address: str
    start: str
    end: str
    #: 훈련 유형 이름. 재직자용을 거르는 근거다(근로자원격훈련).
    target: str
    weekend: str
    satisfaction: float | None
    capacity: int | None
    applicants: int | None
    #: 훈련비 총액. 정부지원금이 포함된 금액이라 본인이 내는 돈이 아니다.
    total_fee: int | None
    url: str
    #: 본인부담액. 상세 API 로만 알 수 있어서 추천에 남은 과정만 채운다.
    own_fee: int | None = None

    @property
    def key(self) -> str:
        return f"{self.course_id}:{self.degree}:{self.institution_id}"

    @property
    def is_remote(self) -> bool:
        """원격 과정은 주소가 수강 지역이 아니라 기관 주소다."""
        return "원격" in self.target

    @property
    def weekend_label(self) -> str:
        return "온라인" if self.is_remote else WEEKEND_LABELS.get(self.weekend, "해당 없음")


def _number(value: Any) -> int | None:
    text = str(value or "").strip().replace(",", "")
    return int(text) if text.isdigit() else None


def _decimal(value: Any) -> float | None:
    text = str(value or "").strip()
    try:
        number = float(text)
    except ValueError:
        return None
    # 만족도가 없는 과정은 0 으로 온다. 0 점과 구분할 수 없어 없는 값으로 본다.
    return number or None


def parse_courses(payload: Mapping[str, Any]) -> tuple[Course, ...]:
    """응답 한 페이지를 Course 로. 순수 함수다."""
    courses: list[Course] = []
    for row in payload.get("srchList") or ():
        try:
            courses.append(
                Course(
                    course_id=str(row["trprId"]),
                    degree=str(row["trprDegr"]),
                    institution_id=str(row["trainstCstId"]),
                    title=str(row["title"]).strip(),
                    institution=str(row.get("subTitle", "")).strip(),
                    address=str(row.get("address", "")).strip(),
                    start=str(row.get("traStartDate", "")).strip(),
                    end=str(row.get("traEndDate", "")).strip(),
                    target=str(row.get("trainTarget", "")).strip(),
                    weekend=str(row.get("wkendSe", "")).strip(),
                    satisfaction=_decimal(row.get("stdgScor")),
                    capacity=_number(row.get("yardMan")),
                    applicants=_number(row.get("regCourseMan")),
                    total_fee=_number(row.get("realMan")),
                    url=str(row.get("titleLink", "")).strip(),
                )
            )
        except (KeyError, TypeError, ValueError):
            # 한 과정이 깨져도 나머지는 계속된다.
            continue
    return tuple(courses)


def _parse_ymd(value: str) -> date:
    return datetime.strptime(value, "%Y%m%d").date()


def _cache_path(cache_dir: Path, name: str, today: str) -> Path:
    return cache_dir / f"{name}_{today}.json"


def _fresh_cache(cache_dir: Path, name: str, today: str, cache_days: int) -> Path | None:
    """cache_days 안에 받아 둔 파일 중 가장 최근 것."""
    limit = date.fromisoformat(today) - timedelta(days=cache_days - 1)
    candidates = []
    for path in cache_dir.glob(f"{name}_*.json"):
        stamp = path.stem.rsplit("_", 1)[-1]
        try:
            when = date.fromisoformat(stamp)
        except ValueError:
            continue
        if limit <= when <= date.fromisoformat(today):
            candidates.append((when, path))
    return max(candidates)[1] if candidates else None


def _call(url: str, params: Mapping[str, str], opener: Callable[..., Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        f"{url}?{urllib.parse.urlencode(params)}",
        headers={"User-Agent": "JobGap/1.0 (personal job-search tool)"},
    )
    raw = opener(request, timeout=TIMEOUT_SEC).read()
    return json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)


def search_courses(
    auth_key: str,
    query: str,
    *,
    start_date: str,
    end_date: str,
    pages: int,
    cache_dir: Path,
    today: str,
    cache_days: int,
    opener: Callable[..., Any] = urllib.request.urlopen,
    sleep: Callable[[float], Any] = time.sleep,
) -> tuple[Course, ...]:
    """과정명으로 검색한다. 캐시가 살아 있으면 네트워크를 타지 않는다."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    slug = _SLUG.sub("_", query).strip("_") or "query"
    # 창의 길이와 페이지 수를 키에 넣는다. 시작일은 항상 오늘이라 절대 날짜를
    # 그대로 넣으면 매일 바뀌어 list_cache_days 가 뜻을 잃는다(창 길이가 같으면
    # 어제 받은 캐시를 오늘도 써야 한다). 페이지 수까지 함께 넣어야 좁은 창과
    # 넓은 창을 구분할 수 있다.
    days = (_parse_ymd(end_date) - _parse_ymd(start_date)).days
    name = f"{slug}__{days}d_p{pages}"

    cached = _fresh_cache(cache_dir, name, today, cache_days)
    if cached is not None:
        with cached.open(encoding="utf-8") as handle:
            return parse_courses({"srchList": json.load(handle)})

    rows: list[dict[str, Any]] = []
    for page in range(1, pages + 1):
        payload = _call(
            LIST_URL,
            {
                "authKey": auth_key,
                "returnType": "JSON",
                "outType": "1",
                "pageNum": str(page),
                "pageSize": str(PAGE_SIZE),
                "srchTraStDt": start_date,
                "srchTraEndDt": end_date,
                "sort": "DESC",
                # 만족도 순. 스킬당 다섯 건만 쓰므로 좋은 과정이 앞에 와야 한다.
                "sortCol": "5",
                "srchTraProcessNm": query,
            },
            opener,
        )
        page_rows = list(payload.get("srchList") or ())
        rows.extend(page_rows)
        if not page_rows:
            # 빈 페이지를 만나면 끝이다. 100행 미만으로 판단하면 픽스처처럼
            # 결과가 적은 검색어에서 다음 페이지를 영영 확인하지 못한다.
            break
        if page < pages:
            sleep(REQUEST_INTERVAL_SEC)

    if rows:
        # 빈 200 은 캐시에 남기지 않는다. 남기면 검색어가 실제로는 결과가
        # 있는데도 list_cache_days 동안 빈 목록을 계속 돌려주게 된다.
        path = _cache_path(cache_dir, name, today)
        with path.open("w", encoding="utf-8") as handle:
            json.dump(rows, handle, ensure_ascii=False)
    return parse_courses({"srchList": rows})


def fetch_own_fee(
    auth_key: str,
    course: Course,
    *,
    cache_dir: Path,
    today: str,
    cache_days: int,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> int | None:
    """본인부담액. 목록에 없는 값이라 추천에 남은 과정만 부른다."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    name = _SLUG.sub("_", course.key)

    cached = _fresh_cache(cache_dir, name, today, cache_days)
    if cached is not None:
        with cached.open(encoding="utf-8") as handle:
            return json.load(handle).get("own_fee")

    payload = _call(
        DETAIL_URL,
        {
            "authKey": auth_key,
            "returnType": "JSON",
            "outType": "2",
            "srchTrprId": course.course_id,
            "srchTrprDegr": course.degree,
            "srchTorgId": course.institution_id,
        },
        opener,
    )
    detail = payload.get("inst_detail_info") or {}
    if isinstance(detail, list):
        detail = detail[0] if detail else {}
    own_fee = _number(detail.get("tgcrGnrlTrneOwepAllt"))

    with _cache_path(cache_dir, name, today).open("w", encoding="utf-8") as handle:
        json.dump({"own_fee": own_fee}, handle, ensure_ascii=False)
    return own_fee


def with_own_fee(course: Course, own_fee: int | None) -> Course:
    return replace(course, own_fee=own_fee)
