"""추천 강의를 Notion 강의 DB 에 적재한다.

공고 DB 와 같은 원칙이다.

  - `스킬:과정:회차:기관` 으로 upsert 한다. 매일 돌려도 중복이 쌓이지 않는다.
  - **사용자의 '상태'(관심, 신청함, 수강 중, 수료)는 새 행에만 쓴다.**
  - 추천에서 빠진 과정은 지우지 않고 '추천 현황'만 '지난 추천'으로 바꾼다.
    지우면 관심 표시해 둔 과정이 사라진다.
"""
from __future__ import annotations

import os
import sys
import urllib.request
from dataclasses import dataclass
from typing import Any, Iterable, Iterator, Mapping, Protocol

from analyze.recommend import Recommendation
from report.notion import _choice, _text, notion_request

#: 강의 DB 열. 설정 명령이 이대로 만들고 course_properties 가 이대로 쓴다.
COURSE_DB_PROPERTIES: dict[str, str] = {
    "과정명": "title",
    "스킬": "relation",
    "연결": "select",
    "기관": "rich_text",
    "지역": "rich_text",
    "기간": "rich_text",
    "수업 방식": "select",
    "본인부담액": "number",
    "훈련비": "number",
    "만족도": "number",
    "정원": "rich_text",
    "링크": "url",
    "추천 현황": "select",
    "상태": "select",
    "key": "rich_text",
}

OPEN_RECOMMENDATION, PAST_RECOMMENDATION = "추천 중", "지난 추천"
LISTINGS = (OPEN_RECOMMENDATION, PAST_RECOMMENDATION)

#: 사용자가 쓰는 열. 시스템은 새 행에만 첫 값을 넣는다.
INITIAL_STATUS = "관심"
COURSE_STATUSES = (INITIAL_STATUS, "신청함", "수강 중", "수료", "보류")


def course_properties(
    rec: Recommendation, *, skill_page_id: str, for_update: bool = False
) -> dict[str, Any]:
    """추천 한 건을 Notion 속성으로. 순수 함수다."""
    course = rec.course
    seats = f"정원 {course.capacity}명" if course.capacity else ""
    if course.applicants is not None and seats:
        seats = f"{seats}, 신청 {course.applicants}명"

    properties: dict[str, Any] = {
        "과정명": {"title": [{"text": {"content": course.title[:2000]}}]},
        "스킬": {"relation": [{"id": skill_page_id}]},
        "연결": _choice(rec.kind),
        "기관": _text(course.institution),
        "지역": _text("온라인" if course.is_remote else course.address),
        "기간": _text(f"{course.start} ~ {course.end}"),
        "수업 방식": _choice(course.weekend_label),
        # 없으면 null 을 보낸다. 속성을 빼면 어제 값이 남는다.
        "본인부담액": {"number": course.own_fee},
        "훈련비": {"number": course.total_fee},
        "만족도": {"number": course.satisfaction},
        "정원": _text(seats),
        "링크": {"url": course.url or None},
        "추천 현황": _choice(OPEN_RECOMMENDATION),
        "key": _text(rec.key),
    }
    if not for_update:
        properties["상태"] = _choice(INITIAL_STATUS)
    return properties


class CourseClient(Protocol):
    def find_page_id(self, key: str) -> str | None: ...
    def create_page(self, properties: Mapping[str, Any]) -> str: ...
    def update_page(self, page_id: str, properties: Mapping[str, Any]) -> None: ...
    def iter_rows(self) -> Iterator[tuple[str, str, str | None]]: ...


class HttpCourseClient:
    def __init__(self, token: str, database_id: str, *, opener=urllib.request.urlopen):
        self._token = token
        self._database_id = database_id
        self._opener = opener

    def _call(self, method: str, path: str, body: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return notion_request(self._token, method, path, body, opener=self._opener)

    def find_page_id(self, key: str) -> str | None:
        payload = self._call(
            "POST",
            f"/databases/{self._database_id}/query",
            {"filter": {"property": "key", "rich_text": {"equals": key}}, "page_size": 1},
        )
        results = payload.get("results") or ()
        return results[0]["id"] if results else None

    def create_page(self, properties: Mapping[str, Any]) -> str:
        payload = self._call(
            "POST", "/pages", {"parent": {"database_id": self._database_id}, "properties": properties}
        )
        return payload.get("id", "")

    def update_page(self, page_id: str, properties: Mapping[str, Any]) -> None:
        self._call("PATCH", f"/pages/{page_id}", {"properties": properties})

    def iter_rows(self) -> Iterator[tuple[str, str, str | None]]:
        cursor: str | None = None
        while True:
            body: dict[str, Any] = {"page_size": 100}
            if cursor:
                body["start_cursor"] = cursor
            payload = self._call("POST", f"/databases/{self._database_id}/query", body)
            for page in payload.get("results") or ():
                props = page.get("properties") or {}
                rich = (props.get("key") or {}).get("rich_text") or ()
                key = "".join(t.get("plain_text", "") for t in rich).strip()
                listing = ((props.get("추천 현황") or {}).get("select") or {}).get("name")
                yield page.get("id", ""), key, listing
            if not payload.get("has_more"):
                return
            cursor = payload.get("next_cursor")


@dataclass(frozen=True, slots=True)
class CourseSyncResult:
    created: int
    updated: int
    failed: int


@dataclass(frozen=True, slots=True)
class CourseSync:
    client: CourseClient

    @classmethod
    def from_env(cls) -> "CourseSync | None":
        token = os.environ.get("NOTION_TOKEN", "").strip()
        database_id = os.environ.get("NOTION_COURSE_DATABASE_ID", "").strip()
        if not (token and database_id):
            return None
        return cls(client=HttpCourseClient(token, database_id))

    def push(
        self, recommendations: Iterable[Recommendation], skill_pages: Mapping[str, str]
    ) -> CourseSyncResult:
        created = updated = failed = 0
        seen: set[str] = set()

        for rec in recommendations:
            skill_page_id = skill_pages.get(rec.skill_id)
            if not skill_page_id:
                # 스킬 행이 아직 없으면 관계를 걸 곳이 없다. 다음 실행에 잡힌다.
                continue
            if rec.key in seen:
                continue
            seen.add(rec.key)
            try:
                page_id = self.client.find_page_id(rec.key)
                if page_id:
                    self.client.update_page(
                        page_id, course_properties(rec, skill_page_id=skill_page_id, for_update=True)
                    )
                    updated += 1
                else:
                    self.client.create_page(course_properties(rec, skill_page_id=skill_page_id))
                    created += 1
            except (OSError, ValueError, KeyError) as exc:
                failed += 1
                print(f"  ! 강의 {rec.key}: 적재 실패 ({exc})", file=sys.stderr)

        return CourseSyncResult(created=created, updated=updated, failed=failed)

    def retire(self, active_keys: Iterable[str]) -> int:
        """오늘 추천에 없는 행을 '지난 추천'으로. 행은 지우지 않는다."""
        active = set(active_keys)
        changed = 0
        for page_id, key, listing in self.client.iter_rows():
            if not key or key in active or listing == PAST_RECOMMENDATION:
                continue
            try:
                self.client.update_page(page_id, {"추천 현황": _choice(PAST_RECOMMENDATION)})
                changed += 1
            except (OSError, ValueError, KeyError) as exc:
                print(f"  ! 강의 {key}: 추천 현황 갱신 실패 ({exc})", file=sys.stderr)
        return changed
