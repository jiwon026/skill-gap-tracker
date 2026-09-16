"""대시보드 구조를 한 번 만든다.

Views API(`2026-03-11`)가 필요한 곳은 여기뿐이다. 매일 적재는 구버전을 그대로
쓰고, 구버전으로도 새 버전이 만든 DB 를 조회하고 쓸 수 있다(2026-09-15 확인).

만들지 않는 것과 그 이유(2026-09-15, 교육 플러스 요금제에서 확인):
  - 대시보드 뷰: 비즈니스 요금제 전용이라 화면에 업그레이드 안내만 뜬다.
  - 숫자 차트: 카드 하나가 화면 절반을 차지하고 제목이 DB 이름으로만 나온다.
    요약 숫자는 report/dashboard.py 가 일반 블록으로 매일 쓴다.
  - 단 블록 안의 보기: API 가 막는다(cannot contain a linked database).

이미 만든 보기는 위치를 옮길 수 없어서, 순서대로 만들어야 한다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from report.dashboard import MISSING, OWNED, SKILL_DB_PROPERTIES, SUMMARY_HEADING
from report.notion import OPEN

VIEWS_API_VERSION = "2026-03-11"

JOB_COLUMNS = ("우선순위", "회사", "공고명", "마감", "상태")
OWNED_COLUMNS = ("스킬", "분류", "근거", "요구 공고 수")
GAP_COLUMNS = ("스킬", "요구 공고 수", "요구 회사", "분류")

JOBS_HEADING = "지금 볼 공고"
CHART_HEADING = "채우면 좋은 스킬"
DETAIL_HEADING = "자세히 보기"
OWNED_PAGE = "나의 스킬"
GAP_PAGE = "역량 갭"
JOBS_LINK_TEXT = "공고 전체 보기"

#: (method, path, body) 를 받아 응답 JSON 을 주는 호출. 테스트는 대역을 넣는다.
Request = Callable[..., Mapping[str, Any]]


def visible_columns(property_ids: Mapping[str, str], shown: Sequence[str]) -> list[dict[str, Any]]:
    """보일 열을 순서대로, 나머지는 숨긴다. 배열 순서가 곧 화면의 열 순서다."""
    unknown = [name for name in shown if name not in property_ids]
    if unknown:
        raise ValueError(f"DB 에 없는 열: {unknown}")
    hidden = [pid for name, pid in property_ids.items() if name not in shown]
    return [{"property_id": property_ids[n], "visible": True} for n in shown] + [
        {"property_id": pid, "visible": False} for pid in hidden
    ]


def heading(text: str) -> dict[str, Any]:
    return {"type": "heading_2", "heading_2": {"rich_text": [{"type": "text", "text": {"content": text}}]}}


def _paragraph(text: str) -> dict[str, Any]:
    return {"type": "paragraph", "paragraph": {"rich_text": [{"type": "text", "text": {"content": text}}]}}


def _linked(page_id: str, after_block: str | None = None) -> dict[str, Any]:
    target: dict[str, Any] = {"parent": {"type": "page_id", "page_id": page_id}}
    if after_block:
        target["position"] = {"type": "after_block", "block_id": after_block}
    return target


def jobs_link_block(url: str) -> dict[str, Any]:
    """공고 DB 로 가는 링크 문단.

    link_to_page 블록을 쓰지 않는 이유: 공고 DB 가 페이지 안에 들어 있는
    인라인 DB 면 Notion 이 400 을 준다(database_id must reference a
    collection_view_page). DB 주소로 거는 링크는 어느 쪽이든 된다.
    """
    return {
        "type": "paragraph",
        "paragraph": {
            "rich_text": [{"type": "text", "text": {"content": JOBS_LINK_TEXT, "link": {"url": url}}}]
        },
    }


def jobs_view_payload(data_source_id: str, property_ids: Mapping[str, str], *, page_id: str, after_block: str) -> dict[str, Any]:
    return {
        "data_source_id": data_source_id,
        "name": JOBS_HEADING,
        "type": "table",
        "create_database": _linked(page_id, after_block),
        "filter": {"property": "공고 현황", "select": {"equals": OPEN}},
        # select 정렬은 선택지 순서를 따른다. 우선순위 선택지는 높음, 중간, 낮음 순이다.
        "sorts": [{"property": "우선순위", "direction": "ascending"}],
        "configuration": {"type": "table", "properties": visible_columns(property_ids, JOB_COLUMNS)},
    }


def skill_chart_payload(data_source_id: str, property_ids: Mapping[str, str], *, page_id: str, after_block: str) -> dict[str, Any]:
    return {
        "data_source_id": data_source_id,
        "name": CHART_HEADING,
        "type": "chart",
        "create_database": _linked(page_id, after_block),
        "filter": {
            "and": [
                {"property": "공고 현황", "select": {"equals": OPEN}},
                {"property": "부족 스킬", "multi_select": {"is_not_empty": True}},
            ]
        },
        "configuration": {
            "type": "chart",
            "chart_type": "bar",
            "x_axis": {"type": "multi_select", "property_id": property_ids["부족 스킬"], "sort": {"type": "manual"}},
            "y_axis": {"aggregator": "count"},
            "sort": "y_descending",
        },
    }


def skill_database_payload(parent_page_id: str) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    for name, kind in SKILL_DB_PROPERTIES.items():
        if name == "보유":
            properties[name] = {"select": {"options": [{"name": OWNED, "color": "green"}, {"name": MISSING, "color": "red"}]}}
        elif kind == "select":
            properties[name] = {"select": {"options": []}}
        elif kind == "multi_select":
            properties[name] = {"multi_select": {"options": []}}
        elif kind == "number":
            properties[name] = {"number": {"format": "number"}}
        else:
            properties[name] = {kind: {}}
    return {
        "parent": {"type": "page_id", "page_id": parent_page_id},
        "title": [{"type": "text", "text": {"content": "스킬"}}],
        "initial_data_source": {"properties": properties},
    }


def owned_view_patch(property_ids: Mapping[str, str]) -> dict[str, Any]:
    return {
        "name": "보유 스킬",
        "filter": {"property": "보유", "select": {"equals": OWNED}},
        "sorts": [{"property": "요구 공고 수", "direction": "descending"}],
        "configuration": {"type": "table", "properties": visible_columns(property_ids, OWNED_COLUMNS)},
    }


def gap_view_payload(data_source_id: str, property_ids: Mapping[str, str], *, page_id: str) -> dict[str, Any]:
    return {
        "data_source_id": data_source_id,
        "name": "부족 스킬",
        "type": "table",
        "create_database": _linked(page_id),
        "filter": {"property": "보유", "select": {"equals": MISSING}},
        "sorts": [{"property": "요구 공고 수", "direction": "descending"}],
        "configuration": {"type": "table", "properties": visible_columns(property_ids, GAP_COLUMNS)},
    }


def _property_ids(data_source: Mapping[str, Any]) -> dict[str, str]:
    return {name: prop["id"] for name, prop in (data_source.get("properties") or {}).items()}


def _append(request: Request, page_id: str, children: Sequence[Mapping[str, Any]]) -> list[str]:
    """페이지 끝에 블록을 붙이고 새 블록 id 들을 돌려준다."""
    payload = request("PATCH", f"/blocks/{page_id}/children", {"children": list(children)})
    return [block["id"] for block in (payload.get("results") or ())][-len(children):]


def _child_page(request: Request, parent_page_id: str, title: str) -> str:
    payload = request(
        "POST",
        "/pages",
        {
            "parent": {"type": "page_id", "page_id": parent_page_id},
            "properties": {"title": {"title": [{"type": "text", "text": {"content": title}}]}},
        },
    )
    return payload["id"]


@dataclass(frozen=True, slots=True)
class DashboardIds:
    page_id: str
    skill_database_id: str


def build_dashboard(request: Request, *, page_id: str, jobs_database_id: str) -> DashboardIds:
    """빈 페이지에 대시보드를 만든다. 공고 DB 는 옮기지 않고 보기와 링크만 둔다."""
    jobs_db = request("GET", f"/databases/{jobs_database_id}", None)
    jobs_ds = jobs_db["data_sources"][0]["id"]
    jobs_props = _property_ids(request("GET", f"/data_sources/{jobs_ds}", None))

    top = _append(request, page_id, [
        heading(SUMMARY_HEADING),
        # 첫 실행이 이 줄을 요약 카드로 바꾼다(요약 제목 뒤, 다음 제목 전까지가 요약 영역).
        _paragraph("다음 자동 실행 뒤에 요약 숫자가 채워집니다."),
        heading(JOBS_HEADING),
    ])
    request("POST", "/views", jobs_view_payload(jobs_ds, jobs_props, page_id=page_id, after_block=top[-1]))

    chart_heading = _append(request, page_id, [heading(CHART_HEADING)])[-1]
    request("POST", "/views", skill_chart_payload(jobs_ds, jobs_props, page_id=page_id, after_block=chart_heading))

    _append(request, page_id, [heading(DETAIL_HEADING)])
    owned_page = _child_page(request, page_id, OWNED_PAGE)
    gap_page = _child_page(request, page_id, GAP_PAGE)
    _append(request, page_id, [
        jobs_link_block(jobs_db["url"]),
    ])

    skill_db = request("POST", "/databases", skill_database_payload(owned_page))
    skill_ds = skill_db["data_sources"][0]["id"]
    skill_props = _property_ids(request("GET", f"/data_sources/{skill_ds}", None))
    default_view = request("GET", f"/views?database_id={skill_db['id']}", None)["results"][0]["id"]
    request("PATCH", f"/views/{default_view}", owned_view_patch(skill_props))
    request("POST", "/views", gap_view_payload(skill_ds, skill_props, page_id=gap_page))

    return DashboardIds(page_id=page_id, skill_database_id=skill_db["id"])
