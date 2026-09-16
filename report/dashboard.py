"""대시보드 매일 갱신: 스킬 DB 행과 요약 카드.

페이지 구조(보기, 차트, 하위 페이지)는 `setup_notion.py --dashboard` 가 한 번
만든다. 여기서는 그 안의 내용만 바꾼다.

  - 스킬 DB 는 보여 주기 전용이다. 기준은 profile.yaml 과 experience.yaml 이고,
    Notion 에서 고친 값은 다음 실행에 덮어쓴다. 사용자가 적는 열이 없어서
    해당하지 않게 된 행은 보관해도 잃는 것이 없다.
  - 요약 카드는 '요약' 제목 바로 뒤부터 다음 제목 전까지만 바꾼다. 그 밖의
    블록은 사용자의 것이다.
  - 구버전 API 를 쓴다. 공고 적재와 같은 버전이라 같은 방식으로 실패한다.
"""
from __future__ import annotations

import os
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Iterable, Iterator, Mapping, Protocol, Sequence

from analyze.gap import AnalyzedPosting
from analyze.priority import PRIORITIES, assign_priority
from analyze.skill_board import SkillRow
from report.notion import _choice, _options, _text, notion_request

#: 스킬 DB 열. 설정 명령이 이대로 만들고 skill_properties 가 이대로 쓴다.
SKILL_DB_PROPERTIES: dict[str, str] = {
    "스킬": "title",
    "분류": "select",
    "보유": "select",
    "근거": "multi_select",
    "요구 공고 수": "number",
    "요구 회사": "multi_select",
    "id": "rich_text",
}

OWNED, MISSING = "보유", "부족"
SUMMARY_HEADING = "요약"

#: skills.yaml 의 category 를 사람이 읽는 이름으로. 없는 값은 그대로 쓴다.
CATEGORY_LABELS: dict[str, str] = {
    "query": "쿼리",
    "language": "언어",
    "warehouse": "웨어하우스",
    "engine": "처리 엔진",
    "pipeline": "파이프라인",
    "bi": "시각화",
    "product_analytics": "제품 분석",
    "method": "분석 방법",
    "ml": "머신러닝",
    "cloud": "클라우드",
    "tooling": "협업 도구",
}

#: 요약 카드가 쓰는 블록 종류. summary_blocks 가 쓰는 것과 설정 명령이 만드는
#: 자리 표시자가 이 두 종류뿐이다. 그 밖의 종류(어떤 제목이든, 하위 페이지,
#: 연결된 보기 등)를 만나면 요약 영역이 끝난 것으로 본다. 사용자가 다음
#: 제목을 지워도 하위 페이지와 연결된 보기는 절대 지우면 안 되기 때문이다.
_SUMMARY_REGION_TYPES = frozenset({"paragraph", "column_list"})


def skill_properties(row: SkillRow) -> dict[str, Any]:
    """스킬 한 줄을 Notion 속성으로. 순수 함수다."""
    return {
        "스킬": {"title": [{"text": {"content": row.name[:2000]}}]},
        # 비우면 None 을 보낸다. 속성을 빼면 예전 분류가 남는다.
        "분류": _choice(CATEGORY_LABELS.get(row.category, row.category)) if row.category else {"select": None},
        "보유": _choice(OWNED if row.owned else MISSING),
        "근거": _options(row.evidence),
        "요구 공고 수": {"number": row.demand},
        "요구 회사": _options(row.companies),
        "id": _text(row.skill_id),
    }


@dataclass(frozen=True, slots=True)
class Summary:
    open_postings: int
    high_priority: int
    #: 이번 실행에서 공고 DB 에 새로 생긴 행 수.
    new_postings: int


def summarize(rows: Sequence[AnalyzedPosting], *, created: int) -> Summary:
    return Summary(
        open_postings=len(rows),
        high_priority=sum(1 for row in rows if assign_priority(row) == PRIORITIES[0]),
        new_postings=created,
    )


def _card(label: str, value: int) -> dict[str, Any]:
    return {
        "type": "callout",
        "callout": {
            "rich_text": [
                {"text": {"content": f"{label}\n"}},
                {"text": {"content": str(value)}, "annotations": {"bold": True}},
            ],
            "icon": {"type": "emoji", "emoji": "📌"},
            "color": "gray_background",
        },
    }


def summary_blocks(summary: Summary, run_date: str) -> list[dict[str, Any]]:
    """기준일 한 줄과 나란히 놓인 카드 3개. 순수 함수다.

    카드를 차트(숫자)로 만들지 않은 이유: 교육 플러스 요금제에서 숫자 차트는
    카드 하나가 화면 절반을 차지하고 제목이 DB 이름으로만 나온다(2026-09-15 확인).
    이 숫자들은 매일 실행 때만 바뀌므로 실시간으로 셀 이유도 없다.
    """
    cards = (
        ("오늘 분석한 공고", summary.open_postings),
        ("우선순위 높음", summary.high_priority),
        ("새로 들어온 공고", summary.new_postings),
    )
    return [
        {
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{"text": {"content": f"{run_date} 실행 기준"}, "annotations": {"color": "gray"}}]
            },
        },
        {
            "type": "column_list",
            "column_list": {
                "children": [
                    {"type": "column", "column": {"children": [_card(label, value)]}} for label, value in cards
                ]
            },
        },
    ]


def _plain(block: Mapping[str, Any]) -> str:
    rich = (block.get(block.get("type", "")) or {}).get("rich_text") or ()
    return "".join(t.get("plain_text") or (t.get("text") or {}).get("content", "") for t in rich).strip()


def blocks_to_replace(children: Sequence[Mapping[str, Any]]) -> tuple[str | None, tuple[str, ...]]:
    """'요약' 제목 블록 id 와, 그 뒤부터 다음 제목 전까지의 블록 id 들."""
    heading_id: str | None = None
    stale: list[str] = []
    for block in children:
        kind = block.get("type")
        if heading_id is None:
            if kind == "heading_2" and _plain(block) == SUMMARY_HEADING:
                heading_id = block["id"]
            continue
        if kind not in _SUMMARY_REGION_TYPES:
            break
        stale.append(block["id"])
    return heading_id, tuple(stale)


class DashboardClient(Protocol):
    def iter_skill_rows(self) -> Iterator[tuple[str, str]]: ...
    def create_skill(self, properties: Mapping[str, Any]) -> str: ...
    def update_skill(self, page_id: str, properties: Mapping[str, Any]) -> None: ...
    def archive(self, page_id: str) -> None: ...
    def page_children(self) -> list[Mapping[str, Any]]: ...
    def delete_block(self, block_id: str) -> None: ...
    def append_after(self, after_id: str, children: Sequence[Mapping[str, Any]]) -> None: ...


class HttpDashboardClient:
    def __init__(self, token: str, skill_database_id: str, page_id: str, *, opener=urllib.request.urlopen):
        self._token = token
        self._skill_database_id = skill_database_id
        self._page_id = page_id
        self._opener = opener

    def _call(self, method: str, path: str, body: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return notion_request(self._token, method, path, body, opener=self._opener)

    def iter_skill_rows(self) -> Iterator[tuple[str, str]]:
        cursor: str | None = None
        while True:
            body: dict[str, Any] = {"page_size": 100}
            if cursor:
                body["start_cursor"] = cursor
            payload = self._call("POST", f"/databases/{self._skill_database_id}/query", body)
            for page in payload.get("results") or ():
                rich = ((page.get("properties") or {}).get("id") or {}).get("rich_text") or ()
                yield page.get("id", ""), "".join(t.get("plain_text", "") for t in rich).strip()
            if not payload.get("has_more"):
                return
            cursor = payload.get("next_cursor")

    def create_skill(self, properties: Mapping[str, Any]) -> str:
        payload = self._call(
            "POST", "/pages", {"parent": {"database_id": self._skill_database_id}, "properties": properties}
        )
        return payload.get("id", "")

    def update_skill(self, page_id: str, properties: Mapping[str, Any]) -> None:
        self._call("PATCH", f"/pages/{page_id}", {"properties": properties})

    def archive(self, page_id: str) -> None:
        self._call("PATCH", f"/pages/{page_id}", {"archived": True})

    def page_children(self) -> list[Mapping[str, Any]]:
        children: list[Mapping[str, Any]] = []
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {"page_size": 100}
            if cursor:
                # 커서는 불투명한 값이라 그대로 이어붙이면 안 된다. URL 인코딩을
                # 거쳐야 커서에 & 나 = 같은 문자가 있어도 요청이 깨지지 않는다.
                params["start_cursor"] = cursor
            query = urllib.parse.urlencode(params)
            payload = self._call("GET", f"/blocks/{self._page_id}/children?{query}")
            children.extend(payload.get("results") or ())
            if not payload.get("has_more"):
                return children
            cursor = payload.get("next_cursor")

    def delete_block(self, block_id: str) -> None:
        self._call("DELETE", f"/blocks/{block_id}")

    def append_after(self, after_id: str, children: Sequence[Mapping[str, Any]]) -> None:
        self._call("PATCH", f"/blocks/{self._page_id}/children", {"children": list(children), "after": after_id})


@dataclass(frozen=True, slots=True)
class SkillSyncResult:
    created: int
    updated: int
    archived: int
    failed: int


@dataclass(frozen=True, slots=True)
class DashboardSync:
    client: DashboardClient

    @classmethod
    def from_env(cls) -> "DashboardSync | None":
        """토큰, 스킬 DB, 대시보드 페이지가 모두 있을 때만 만든다."""
        token = os.environ.get("NOTION_TOKEN", "").strip()
        skill_db = os.environ.get("NOTION_SKILL_DATABASE_ID", "").strip()
        page = os.environ.get("NOTION_DASHBOARD_PAGE_ID", "").strip()
        if not (token and skill_db and page):
            return None
        return cls(client=HttpDashboardClient(token, skill_db, page))

    def sync_skills(self, rows: Iterable[SkillRow]) -> SkillSyncResult:
        existing: dict[str, str] = {}
        duplicates: list[str] = []
        for page_id, skill_id in self.client.iter_skill_rows():
            if not skill_id:
                # 사용자가 손으로 추가한 행일 수 있다.
                continue
            if skill_id in existing:
                duplicates.append(page_id)
            else:
                existing[skill_id] = page_id

        created = updated = failed = 0
        written: set[str] = set()
        for row in rows:
            written.add(row.skill_id)
            try:
                page_id = existing.get(row.skill_id)
                if page_id:
                    self.client.update_skill(page_id, skill_properties(row))
                    updated += 1
                else:
                    self.client.create_skill(skill_properties(row))
                    created += 1
            except (OSError, ValueError, KeyError) as exc:
                failed += 1
                print(f"  ! 스킬 {row.skill_id}: 대시보드 적재 실패 ({exc})", file=sys.stderr)

        leftovers = [page_id for skill_id, page_id in existing.items() if skill_id not in written]
        archived = 0
        for page_id in (*leftovers, *duplicates):
            try:
                self.client.archive(page_id)
                archived += 1
            except (OSError, ValueError, KeyError) as exc:
                failed += 1
                print(f"  ! 스킬 행 보관 실패 ({exc})", file=sys.stderr)

        return SkillSyncResult(created=created, updated=updated, archived=archived, failed=failed)

    def write_summary(self, summary: Summary, run_date: str) -> bool:
        heading_id, stale = blocks_to_replace(self.client.page_children())
        if heading_id is None:
            print(
                f"  ! 대시보드에 '{SUMMARY_HEADING}' 제목(제목 2)이 없어 요약을 쓰지 않았습니다",
                file=sys.stderr,
            )
            return False
        # 새 블록을 먼저 넣는다. 지운 뒤 추가가 실패하면 요약이 통째로 사라진다.
        self.client.append_after(heading_id, summary_blocks(summary, run_date))
        for block_id in stale:
            self.client.delete_block(block_id)
        return True
