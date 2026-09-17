"""대시보드 구조 만들기.

실측(2026-09-15)으로 확인한 요청 모양을 고정한다. Notion 은 모양이 틀리면
400 을 주는데, 설정 명령은 한 번만 돌아서 틀린 채로 오래 방치되기 쉽다.
"""
import pytest

from analyze.recommend import DIRECT, FOUNDATION
from report.course_notion import (
    COURSE_DB_PROPERTIES,
    COURSE_STATUSES,
    FEATURED,
    LISTINGS,
    OPEN_RECOMMENDATION,
)
from report.dashboard import MISSING, OWNED, SKILL_DB_PROPERTIES, SUMMARY_HEADING
from report.dashboard_layout import (
    CHART_HEADING,
    CHART_HEIGHT,
    CHART_PROPERTY,
    COURSE_COLUMNS,
    COURSE_PAGE,
    COURSE_SUMMARY_COLUMNS,
    COURSE_SUMMARY_HEADING,
    DETAIL_HEADING,
    GAP_COLUMNS,
    JOB_COLUMNS,
    OWNED_COLUMNS,
    build_courses,
    build_dashboard,
    course_database_payload,
    course_summary_view_payload,
    course_view_patch,
    gap_view_payload,
    jobs_view_payload,
    owned_view_patch,
    skill_chart_payload,
    skill_database_payload,
    visible_columns,
)

JOB_PROPS = {name: f"id-{i}" for i, name in enumerate(
    ["공고명", "회사", "규모", "우선순위", "지역", "마감", "보유 스킬", "부족 스킬",
     "핵심 부족 스킬", "어필 경험", "어필 포인트", "URL", "상태", "key", "공고 현황"])}
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
    # 전체 '부족 스킬' 이 아니라 오늘 상위 몇 개만 담긴 열을 센다. Notion 차트는
    # 막대를 상위 N 개로 자르지 못한다(2026-09-17 확인).
    assert conf["x_axis"] == {"type": "multi_select", "property_id": JOB_PROPS[CHART_PROPERTY],
                              "sort": {"type": "manual"}}
    assert CHART_PROPERTY == "핵심 부족 스킬"
    # 빈 값 막대가 끼지 않게 한다(2026-09-15 확인).
    assert {"property": CHART_PROPERTY, "multi_select": {"is_not_empty": True}} in body["filter"]["and"]
    # 지금 아무 공고도 요구하지 않는 선택지가 0짜리 막대로 남지 않게 한다(2026-09-16 확인).
    assert conf["hide_empty_groups"] is True
    # 차트가 세로로 길면 아래의 '들어볼 만한 강의' 가 첫 화면에서 밀려난다.
    # height 는 small, medium, large, extra_large 만 받는다(2026-09-17 확인).
    assert conf["height"] == CHART_HEIGHT == "small"


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
            if body["title"][0]["text"]["content"] == "강의":
                return {"id": "course-db", "data_sources": [{"id": "course-ds"}]}
            return {"id": "skill-db", "data_sources": [{"id": "skill-ds"}]}
        if method == "GET" and path == "/databases/skill-db":
            return {"id": "skill-db", "data_sources": [{"id": "skill-ds"}]}
        if method == "GET" and path == "/data_sources/skill-ds":
            return {"properties": {n: {"id": i} for n, i in SKILL_PROPS.items()}}
        if method == "GET" and path == "/data_sources/course-ds":
            return {"properties": {n: {"id": f"c-{i}"} for i, n in enumerate(COURSE_DB_PROPERTIES)}}
        if method == "GET" and path.startswith("/views?database_id=skill-db"):
            return {"results": [{"id": "default-view"}]}
        if method == "GET" and path.startswith("/views?database_id=course-db"):
            return {"results": [{"id": "course-view"}]}
        return {"id": "ok"}


def test_build_dashboard_creates_everything_in_order():
    notion = ScriptedNotion()
    ids = build_dashboard(notion, page_id="dash", jobs_database_id="jobs")

    assert (ids.page_id, ids.skill_database_id) == ("dash", "skill-db")
    first_append = next(body for m, p, body in notion.calls if p == "/blocks/dash/children")
    assert first_append["children"][0]["heading_2"]["rich_text"][0]["text"]["content"] == SUMMARY_HEADING

    views = [body for m, p, body in notion.calls if m == "POST" and p == "/views"]
    assert [v["name"] for v in views] == [
        "지금 볼 공고", "채우면 좋은 스킬", "부족 스킬", COURSE_SUMMARY_HEADING]
    assert views[0]["create_database"]["position"]["block_id"] == "b3"  # '지금 볼 공고' 제목 뒤
    assert ("PATCH", "/views/default-view") in [(m, p) for m, p, _ in notion.calls]
    pages = [body["properties"]["title"]["title"][0]["text"]["content"]
             for m, p, body in notion.calls if m == "POST" and p == "/pages"]
    assert pages == ["나의 스킬", "역량 갭", COURSE_PAGE]
    database = next(body for m, p, body in notion.calls if m == "POST" and p == "/databases")
    assert database["parent"] == {"type": "page_id", "page_id": "page-나의 스킬"}
    assert ids.course_database_id == "course-db"


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


def test_course_database_relates_to_the_skill_database():
    body = course_database_payload("page", "skill-ds")
    props = body["initial_data_source"]["properties"]
    assert set(props) == set(COURSE_DB_PROPERTIES)
    assert props["스킬"]["relation"]["data_source_id"] == "skill-ds"
    assert [o["name"] for o in props["추천 현황"]["select"]["options"]] == list(LISTINGS)
    assert [o["name"] for o in props["상태"]["select"]["options"]] == list(COURSE_STATUSES)
    # 하드코딩한 "직접"/"기반" 이 analyze.recommend 의 종류 상수와 갈라지면
    # 강의 DB 의 '연결' 열 값이 추천이 실제로 쓰는 kind 와 달라진다.
    assert [o["name"] for o in props["연결"]["select"]["options"]] == [DIRECT, FOUNDATION]


def test_course_view_shows_open_recommendations_only():
    props = {name: f"c-{i}" for i, name in enumerate(COURSE_DB_PROPERTIES)}
    patch = course_view_patch(props)
    assert patch["filter"] == {"property": "추천 현황", "select": {"equals": OPEN_RECOMMENDATION}}
    shown = [c["property_id"] for c in patch["configuration"]["properties"] if c["visible"]]
    assert shown == [props[n] for n in COURSE_COLUMNS]


class ScriptedCourses(ScriptedNotion):
    def __call__(self, method, path, body):
        if method == "GET" and path == "/databases/skill-db":
            self.calls.append((method, path, body))
            return {"id": "skill-db", "data_sources": [{"id": "skill-ds"}]}
        if method == "GET" and path.startswith("/views?database_id=course-db"):
            self.calls.append((method, path, body))
            return {"results": [{"id": "course-view"}]}
        if method == "POST" and path == "/databases" and body["title"][0]["text"]["content"] == "강의":
            self.calls.append((method, path, body))
            return {"id": "course-db", "data_sources": [{"id": "course-ds"}]}
        if method == "GET" and path == "/data_sources/course-ds":
            self.calls.append((method, path, body))
            return {"properties": {n: {"id": f"c-{i}"} for i, n in enumerate(COURSE_DB_PROPERTIES)}}
        return super().__call__(method, path, body)


def test_build_courses_adds_a_page_and_database_to_an_existing_dashboard():
    notion = ScriptedCourses()
    course_db = build_courses(notion, page_id="dash", skill_database_id="skill-db")

    assert course_db == "course-db"
    pages = [body["properties"]["title"]["title"][0]["text"]["content"]
             for m, p, body in notion.calls if m == "POST" and p == "/pages"]
    assert pages == [COURSE_PAGE]
    course_db_body = next(body for m, p, body in notion.calls
                           if m == "POST" and p == "/databases" and body["title"][0]["text"]["content"] == "강의")
    assert course_db_body["parent"] == {"type": "page_id", "page_id": f"page-{COURSE_PAGE}"}
    assert ("PATCH", "/views/course-view") in [(m, p) for m, p, _ in notion.calls]
    # 대시보드 페이지에는 제목만 더한다. 기존 블록은 건드리지 않는다.
    appended = [b for m, p, body in notion.calls if p == "/blocks/dash/children" for b in body["children"]]
    assert all(b["type"] == "heading_2" for b in appended) or appended == []


def test_course_summary_view_shows_only_the_four_summary_columns():
    """첫 화면 표는 역량, 과정명, 기간, 유형만 보여 준다. 나머지는 하위 페이지에서 본다."""
    props = {name: f"c-{i}" for i, name in enumerate(COURSE_DB_PROPERTIES)}
    body = course_summary_view_payload("course-ds", props, page_id="dash", after_block="h")

    assert body["type"] == "table"
    assert body["name"] == COURSE_SUMMARY_HEADING
    assert body["create_database"] == {
        "parent": {"type": "page_id", "page_id": "dash"},
        "position": {"type": "after_block", "block_id": "h"},
    }
    assert body["filter"] == {"and": [
        {"property": "추천 현황", "select": {"equals": OPEN_RECOMMENDATION}},
        {"property": "첫 화면", "select": {"equals": FEATURED}},
    ]}
    shown = [c["property_id"] for c in body["configuration"]["properties"] if c["visible"]]
    assert shown == [props[n] for n in COURSE_SUMMARY_COLUMNS]
    assert COURSE_SUMMARY_COLUMNS == ("스킬", "과정명", "기간", "수업 방식")


def test_course_summary_sits_between_the_chart_and_the_detail_heading():
    """블록은 중간에 끼워 넣을 수 없으니(Views API 가 after 를 막는다) 순서로 푼다.

    차트 제목, 강의 제목, '자세히 보기' 순으로 붙이고 표는 나중에 그 제목 뒤에 만든다.
    """
    notion = ScriptedNotion()
    build_dashboard(notion, page_id="dash", jobs_database_id="jobs")

    # ScriptedNotion 은 붙인 순서대로 b1, b2, ... 를 준다. 제목의 블록 id 를 그대로 센다.
    ids: dict[str, str] = {}
    order: list[str] = []
    n = 0
    for m, p, body in notion.calls:
        if p != "/blocks/dash/children":
            continue
        assert "after" not in body  # Views API 는 after 를 400 으로 막는다
        for b in body["children"]:
            n += 1
            if b["type"] == "heading_2":
                name = b["heading_2"]["rich_text"][0]["text"]["content"]
                ids[name] = f"b{n}"
                order.append(name)

    assert order.index(CHART_HEADING) < order.index(COURSE_SUMMARY_HEADING) < order.index(DETAIL_HEADING)

    head_id = ids[COURSE_SUMMARY_HEADING]
    summary = next(body for m, p, body in notion.calls
                   if m == "POST" and p == "/views" and body["name"] == COURSE_SUMMARY_HEADING)
    assert summary["create_database"]["position"] == {"type": "after_block", "block_id": head_id}


def test_build_courses_leaves_the_front_page_alone_without_an_anchor():
    """하위 페이지만 원할 때는 첫 화면에 아무것도 더하지 않는다."""
    notion = ScriptedCourses()
    build_courses(notion, page_id="dash", skill_database_id="skill-db")

    assert not [b for m, p, body in notion.calls if p == "/blocks/dash/children"
                for b in body["children"]]
    assert not [body for m, p, body in notion.calls
                if m == "POST" and p == "/views" and body["name"] == COURSE_SUMMARY_HEADING]


def test_build_courses_adds_the_summary_view_after_the_given_block():
    """제목 블록은 부르는 쪽이 만든다. 여기서는 그 뒤에 표만 만든다."""
    notion = ScriptedCourses()
    build_courses(notion, page_id="dash", skill_database_id="skill-db", summary_after_block="head")

    assert not [b for m, p, body in notion.calls if p == "/blocks/dash/children"
                for b in body["children"]]
    summary = next(body for m, p, body in notion.calls
                   if m == "POST" and p == "/views" and body["name"] == COURSE_SUMMARY_HEADING)
    assert summary["create_database"]["position"] == {"type": "after_block", "block_id": "head"}
