"""Greenhouse Job Board API 수집기.

공개 엔드포인트라 인증이 없다. `content=true`를 붙이면 공고 본문이 함께
오는데, 이 본문이 HTML 엔티티로 한 번 더 이스케이프된 상태라 여기서
되돌린다. 그 밖의 정제는 하지 않는다 — 스냅샷은 원본에 가까울수록 좋다.
"""
from __future__ import annotations

import html
import json
import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from collect.schema import Posting

BASE_URL = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
TIMEOUT_SEC = 40

#: 근무지가 한국인 공고만 남긴다. 목표가 국내 취업이므로 범위 결정이지 분석 기준이 아니다.
LOCATION_INCLUDE = re.compile(r"korea|seoul|서울|한국", re.I)
#: 보드에 섞여 있는 내부 테스트·템플릿 공고. 쿠팡 보드에서 21건 확인됐다.
LOCATION_EXCLUDE = re.compile(r"z-test|template|do not use", re.I)


@dataclass(frozen=True, slots=True)
class BoardSpec:
    token: str
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


def fetch_payload(
    spec: BoardSpec,
    cache_path: Path,
    *,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> Mapping[str, Any]:
    """보드 응답을 가져온다. 캐시가 있으면 네트워크를 타지 않는다.

    개발 중에는 같은 보드를 수십 번 호출하게 되므로 캐시가 기본이다.
    주 1회 실행에서는 캐시 파일명에 날짜가 들어가 자연히 갱신된다.
    """
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))

    url = BASE_URL.format(token=spec.token)
    raw = opener(url, timeout=TIMEOUT_SEC).read()
    payload = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return payload


def parse_board(
    payload: Mapping[str, Any],
    spec: BoardSpec,
    *,
    fetched_at: str,
    location_include: re.Pattern[str] = LOCATION_INCLUDE,
    location_exclude: re.Pattern[str] = LOCATION_EXCLUDE,
) -> CollectResult:
    """보드 응답을 Posting 목록으로 옮긴다.

    공고 한 건의 결함이 보드 전체를 무너뜨리지 않도록, 검증에 걸린 건은
    이유와 함께 `skipped`로 빠진다.
    """
    postings: list[Posting] = []
    skipped: list[Skipped] = []

    for job in payload.get("jobs", ()):
        source_id = str(job.get("id", "")).strip()
        location = (job.get("location") or {}).get("name", "") or ""

        if location_exclude.search(location):
            continue
        if not location_include.search(location):
            continue

        try:
            postings.append(
                Posting(
                    source="greenhouse",
                    source_id=source_id,
                    company=job.get("company_name") or spec.company,
                    title=job.get("title", ""),
                    location=location,
                    url=job.get("absolute_url", ""),
                    body_html=html.unescape(job.get("content") or ""),
                    posted_at=job.get("first_published") or job.get("updated_at"),
                    fetched_at=fetched_at,
                    departments=tuple(
                        d.get("name", "") for d in (job.get("departments") or ())
                    ),
                    # 비어 있으면 '모름'이다. Greenhouse 는 이 필드를 거의 안 써서
                    # (쿠팡 701건 전부 비어 있음) 상시채용이라 단정하지 않는다.
                    deadline=str(job.get("application_deadline") or "").strip()[:10] or None,
                )
            )
        except (ValueError, TypeError) as exc:
            skipped.append(Skipped(source_id=source_id or "?", reason=str(exc)))

    return CollectResult(postings=tuple(postings), skipped=tuple(skipped))
