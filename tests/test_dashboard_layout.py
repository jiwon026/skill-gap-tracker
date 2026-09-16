"""대시보드 구조 만들기.

실측(2026-09-15)으로 확인한 요청 모양을 고정한다. Notion 은 모양이 틀리면
400 을 주는데, 설정 명령은 한 번만 돌아서 틀린 채로 오래 방치되기 쉽다.
"""
import pytest

from report.dashboard import MISSING, OWNED, SKILL_DB_PROPERTIES, SUMMARY_HEADING
from report.dashboard_layout import (
    GAP_COLUMNS,
    JOB_COLUMNS,
    OWNED_COLUMNS,
    build_dashboard,
    gap_view_payload,
    jobs_view_payload,
    owned_view_patch,
    skill_chart_payload,
    skill_database_payload,
    visible_columns,
)

JOB_PROPS = {name: f"id-{i}" for i, name in enumerate(
    ["공고명", "회사", "규모", "우선순위", "지역", "마감", "보유 스킬", "부족 스킬",
     "어필 경험", "어필 포인트", "URL", "상태", "key", "공고 현황"])}
SKILL_PROPS = {name: f"s-{i}" for i, name in enumerate(SKILL_DB_PROPERTIES)}


def test_visible_columns_keep_order_then_hide_the_rest():
    columns = visible_columns(JOB_PROPS, JOB_COLUMNS)
    assert [c["property_id"] for c in columns[:5]] == [JOB_PROPS[n] for n in JOB_COLUMNS]
    assert all(c["visible"] for c in columns[:5])
    assert len(columns) == len(JOB_PROPS) and not any(c["visible"] for c in columns[5:])


def test_visible_columns_reject_unknown_names():
    with pytest.raises(ValueError, match="마감"):
        visible_columns({"공고명": "title"}, ("공고명", "마감"))


def test_jobs_view_shows_open_postings_under_the_heading():
    body = jobs_view_payload("ds", JOB_PROPS, page_id="page", after_block="h")
    assert body["type"] == "table"
    assert body["create_database"] == {
        "parent": {"type": "page_id", "page_id": "page"},
        "position": {"type": "after_block", "block_id": "h"},
    }
    assert body["filter"] == {"property": "공고 현황", "select": {"equals": "모집중"}}
    assert body["sorts"] == [{"property": "우선순위", "direction": "ascending"}]


def test_skill_chart_counts_missing_skills_of_open_postings_only():
    body = skill_chart_payload("ds", JOB_PROPS, page_id="page", after_block="h")
    conf = body["configuration"]
    assert conf["chart_type"] == "bar"
    assert conf["x_axis"] == {"type": "multi_select", "property_id": JOB_PROPS["부족 스킬"], "sort": {"type": "manual"}}
    # '부족 스킬 없음' 막대가 끼지 않게 한다(2026-09-15 확인).
    assert {"property": "부족 스킬", "multi_select": {"is_not_empty": True}} in body["filter"]["and"]
    # 지금 아무 공고도 요구하지 않는 선택지가 0짜리 막대로 남지 않게 한다(2026-09-16 확인).
    assert conf["hide_empty_groups"] is True


def test_skill_database_has_the_synced_columns():
    body = skill_database_payload("page")
    props = body["initial_data_source"]["properties"]
    assert set(props) == set(SKILL_DB_PROPERTIES)
    assert [o["name"] for o in props["보유"]["select"]["options"]] == [OWNED, MISSING]


def test_owned_and_gap_views_filter_on_the_owned_column():
    assert owned_view_patch(SKILL_PROPS)["filter"] == {"property": "보유", "select": {"equals": OWNED}}
    gap = gap_view_payload("sds", SKILL_PROPS, page_id="gap")
    assert gap["filter"] == {"property": "보유", "select": {"equals": MISSING}}
    assert gap["sorts"] == [{"property": "요구 공고 수", "direction": "descending"}]
    shown = [c["property_id"] for c in gap["configuration"]["properties"] if c["visible"]]
    assert shown == [SKILL_PROPS[n] for n in GAP_COLUMNS]
    shown = [c["property_id"] for c in owned_view_patch(SKILL_PROPS)["configuration"]["properties"] if c["visible"]]
    assert shown == [SKILL_PROPS[n] for n in OWNED_COLUMNS]


class ScriptedNotion:
    """경로별로 정해 둔 응답을 주고 호출 순서를 기록한다."""

    def __init__(self):
        self.calls = []
        self._blocks = 0

    def __call__(self, method, path, body):
        self.calls.append((method, path, body))
        if method == "GET" and path == "/databases/jobs":
            return {"data_sources": [{"id": "jobs-ds"}], "url": "https://notion.so/jobs-db"}
        if method == "GET" and path == "/data_sources/jobs-ds":
            return {"properties": {n: {"id": i} for n, i in JOB_PROPS.items()}}
        if method == "PATCH" and path.endswith("/children"):
            results = []
            for _ in body["children"]:
                self._blocks += 1
                results.append({"id": f"b{self._blocks}"})
            return {"results": results}
        if method == "POST" and path == "/pages":
            return {"id": f"page-{body['properties']['title']['title'][0]['text']['content']}"}
        if method == "POST" and path == "/databases":
            return {"id": "skill-db", "data_sources": [{"id": "skill-ds"}]}
        if method == "GET" and path == "/data_sources/skill-ds":
            return {"properties": {n: {"id": i} for n, i in SKILL_PROPS.items()}}
        if method == "GET" and path.startswith("/views?database_id=skill-db"):
            return {"results": [{"id": "default-view"}]}
        return {"id": "ok"}


def test_build_dashboard_creates_everything_in_order():
    notion = ScriptedNotion()
    ids = build_dashboard(notion, page_id="dash", jobs_database_id="jobs")

    assert (ids.page_id, ids.skill_database_id) == ("dash", "skill-db")
    first_append = next(body for m, p, body in notion.calls if p == "/blocks/dash/children")
    assert first_append["children"][0]["heading_2"]["rich_text"][0]["text"]["content"] == SUMMARY_HEADING

    views = [body for m, p, body in notion.calls if m == "POST" and p == "/views"]
    assert [v["name"] for v in views] == ["지금 볼 공고", "채우면 좋은 스킬", "부족 스킬"]
    assert views[0]["create_database"]["position"]["block_id"] == "b3"  # '지금 볼 공고' 제목 뒤
    assert ("PATCH", "/views/default-view") in [(m, p) for m, p, _ in notion.calls]
    pages = [body["properties"]["title"]["title"][0]["text"]["content"]
             for m, p, body in notion.calls if m == "POST" and p == "/pages"]
    assert pages == ["나의 스킬", "역량 갭"]
    database = next(body for m, p, body in notion.calls if m == "POST" and p == "/databases")
    assert database["parent"] == {"type": "page_id", "page_id": "page-나의 스킬"}


def test_jobs_link_is_a_url_paragraph_not_a_link_to_page():
    """공고 DB 가 페이지 안에 들어 있는 인라인 DB 면 link_to_page 가 400 이 된다
    (실제 DB 에서 확인: database_id must reference a collection_view_page)."""
    notion = ScriptedNotion()
    build_dashboard(notion, page_id="dash", jobs_database_id="jobs")

    appended = [b for m, p, body in notion.calls if p == "/blocks/dash/children" for b in body["children"]]
    assert not any(b["type"] == "link_to_page" for b in appended)
    link = [b for b in appended if b["type"] == "paragraph" and b["paragraph"]["rich_text"]
            and (b["paragraph"]["rich_text"][0]["text"].get("link") or {}).get("url")]
    assert len(link) == 1
    assert link[0]["paragraph"]["rich_text"][0]["text"]["link"]["url"] == "https://notion.so/jobs-db"
