"""Notion 지원 보드 DB 자동 생성.

처음 쓰는 사람이 열 14개의 이름과 종류를 손으로 맞추다 한 글자만 틀려도
적재가 전부 400으로 실패한다. 이 스크립트가 만드는 DB는 파이프라인이 쓰는
속성과 정확히 같아야 한다. 여기서 지키는 것은 그 일치다.
"""
import io
import json
import urllib.error

import pytest

import check_notion
import setup_notion
from analyze.priority import PRIORITIES
from extract.company_size import SIZES, UNKNOWN
from report.notion import INITIAL_STATUS, LISTING_STATUSES, NOTION_VERSION

PAGE_ID = "1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d"


@pytest.fixture
def payload():
    return setup_notion.database_payload(PAGE_ID, title="지원 보드")


def option_names(payload, name):
    return [o["name"] for o in payload["properties"][name]["select"]["options"]]


class TestPayload:
    def test_properties_match_what_the_pipeline_writes(self, payload):
        """check_notion.EXPECTED 는 build_properties 와 테스트로 묶여 있다
        (test_notion.TestSchemaCheckContract). 여기까지 묶으면 세 곳이 한 몸이 된다.

        READ_ONLY 는 적재 코드가 값을 쓰지 않지만 DB 에는 있어야 한다. 이게 빠지면
        새로 설치한 사람의 '지금 볼 공고' 보기가 없는 열로 정렬하려다 400 이 난다.
        """
        schema = {**check_notion.EXPECTED, **check_notion.READ_ONLY}
        assert set(payload["properties"]) == set(schema)
        for name, kind in schema.items():
            assert list(payload["properties"][name]) == [kind], name

    def test_read_only_properties_are_not_written_by_the_pipeline(self):
        """Notion 이 채우는 값이라 적재가 건드리면 안 된다."""
        assert not set(check_notion.READ_ONLY) & set(check_notion.EXPECTED)

    def test_parent_and_title(self, payload):
        assert payload["parent"] == {"type": "page_id", "page_id": PAGE_ID}
        assert payload["title"][0]["text"]["content"] == "지원 보드"

    def test_select_options_follow_the_sort_order(self, payload):
        """Notion 은 선택지 순서대로 정렬한다. 값을 쓰면서 자동 생성되게 두면
        정렬이 뒤섞이므로, 순서가 의미 있는 열은 만들 때 순서를 박아 둔다."""
        assert option_names(payload, "우선순위") == list(PRIORITIES)
        assert option_names(payload, "공고 현황") == list(LISTING_STATUSES)
        assert option_names(payload, "규모") == [*SIZES, UNKNOWN]

    def test_status_starts_with_the_value_new_rows_get(self, payload):
        assert option_names(payload, "상태")[0] == INITIAL_STATUS

    def test_free_select_columns_start_empty(self, payload):
        """회사명은 공고마다 다르다. 값이 들어올 때 Notion 이 선택지를 만든다."""
        assert payload["properties"]["회사"]["select"]["options"] == []

    def test_payload_has_no_duplicate_option_names(self, payload):
        for name, prop in payload["properties"].items():
            if "select" in prop:
                names = option_names(payload, name)
                assert len(names) == len(set(names)), name


class TestPageId:
    @pytest.mark.parametrize(
        "value",
        [
            PAGE_ID,
            "1a2b3c4d-5e6f-7a8b-9c0d-1e2f3a4b5c6d",
            f"https://www.notion.so/workspace/My-Job-Board-{PAGE_ID}",
            f"https://www.notion.so/{PAGE_ID}?pvs=4",
        ],
    )
    def test_accepts_ids_and_page_urls(self, value):
        assert setup_notion.page_id_from(value) == PAGE_ID

    @pytest.mark.parametrize("value", ["", "not-a-page", "https://www.notion.so/My-Page"])
    def test_rejects_values_without_an_id(self, value):
        with pytest.raises(ValueError, match="페이지"):
            setup_notion.page_id_from(value)


class _Opener:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.requests = response, error, []

    def __call__(self, request, timeout=0):
        self.requests.append(request)
        if self.error:
            raise self.error
        return io.BytesIO(json.dumps(self.response).encode("utf-8"))


def _http_error(code, body="{}"):
    return urllib.error.HTTPError("https://api.notion.com/v1/databases", code, "err", {}, io.BytesIO(body.encode()))


class TestCreate:
    def test_posts_the_payload_and_returns_the_new_id(self):
        opener = _Opener(response={"id": "new-db-id", "url": "https://www.notion.so/new"})
        created = setup_notion.create_database("secret", PAGE_ID, title="지원 보드", opener=opener)
        assert created == {"id": "new-db-id", "url": "https://www.notion.so/new"}

        request = opener.requests[0]
        assert request.get_method() == "POST"
        assert request.full_url.endswith("/databases")
        assert request.get_header("Authorization") == "Bearer secret"
        assert request.get_header("Notion-version") == NOTION_VERSION
        body = json.loads(request.data.decode("utf-8"))
        assert set(body["properties"]) == set(check_notion.EXPECTED) | set(check_notion.READ_ONLY)


class TestMain:
    def test_missing_token_stops_with_a_clear_message(self, monkeypatch, capsys):
        monkeypatch.delenv("NOTION_TOKEN", raising=False)
        assert setup_notion.main([PAGE_ID]) == 1
        assert "NOTION_TOKEN" in capsys.readouterr().err

    def test_bad_page_value_stops(self, monkeypatch, capsys):
        monkeypatch.setenv("NOTION_TOKEN", "secret")
        assert setup_notion.main(["not-a-page"]) == 1
        assert "페이지" in capsys.readouterr().err

    def test_success_prints_the_next_steps(self, monkeypatch, capsys):
        monkeypatch.setenv("NOTION_TOKEN", "secret")
        opener = _Opener(response={"id": "new-db-id", "url": "https://www.notion.so/new"})
        assert setup_notion.main([PAGE_ID], opener=opener) == 0
        out = capsys.readouterr().out
        assert "NOTION_DATABASE_ID" in out and "new-db-id" in out
        assert "check_notion.py" in out

    def test_page_not_shared_explains_the_connection_step(self, monkeypatch, capsys):
        """가장 흔한 실패: 페이지를 인테그레이션에 연결하지 않았다."""
        monkeypatch.setenv("NOTION_TOKEN", "secret")
        assert setup_notion.main([PAGE_ID], opener=_Opener(error=_http_error(404))) == 1
        assert "연결" in capsys.readouterr().err

    def test_bad_token_is_named(self, monkeypatch, capsys):
        monkeypatch.setenv("NOTION_TOKEN", "wrong")
        assert setup_notion.main([PAGE_ID], opener=_Opener(error=_http_error(401))) == 1
        assert "토큰" in capsys.readouterr().err


class TestDashboardFlag:
    def test_needs_the_jobs_database(self, monkeypatch, capsys):
        monkeypatch.setenv("NOTION_TOKEN", "tok")
        monkeypatch.delenv("NOTION_DATABASE_ID", raising=False)
        assert setup_notion.main([PAGE_ID, "--dashboard"]) == 1
        assert "NOTION_DATABASE_ID" in capsys.readouterr().err

    def test_prints_the_two_env_vars(self, monkeypatch, capsys):
        """강의 DB 없이 대시보드만 만들어지면 안내도 두 줄이어야 한다."""
        from report.dashboard_layout import DashboardIds

        monkeypatch.setenv("NOTION_TOKEN", "tok")
        monkeypatch.setenv("NOTION_DATABASE_ID", "jobs")
        seen = {}

        def fake_build(request, *, page_id, jobs_database_id):
            seen.update(page_id=page_id, jobs=jobs_database_id)
            return DashboardIds(page_id=page_id, skill_database_id="skill-db")

        monkeypatch.setattr(setup_notion, "build_dashboard", fake_build)
        assert setup_notion.main([PAGE_ID, "--dashboard"]) == 0
        out = capsys.readouterr().out
        assert seen == {"page_id": PAGE_ID, "jobs": "jobs"}
        assert "환경변수 두 개를 넣으세요" in out
        assert "NOTION_DASHBOARD_PAGE_ID" in out and PAGE_ID in out
        assert "NOTION_SKILL_DATABASE_ID" in out and "skill-db" in out
        assert "NOTION_COURSE_DATABASE_ID" not in out

    def test_prints_three_env_vars_when_the_course_database_is_also_built(self, monkeypatch, capsys):
        """build_dashboard 는 이제 강의 DB 까지 함께 만든다. 안내 줄 수가
        실제로 찍는 setx 줄 수와 갈라지면 안 된다."""
        from report.dashboard_layout import DashboardIds

        monkeypatch.setenv("NOTION_TOKEN", "tok")
        monkeypatch.setenv("NOTION_DATABASE_ID", "jobs")

        def fake_build(request, *, page_id, jobs_database_id):
            return DashboardIds(page_id=page_id, skill_database_id="skill-db", course_database_id="course-db")

        monkeypatch.setattr(setup_notion, "build_dashboard", fake_build)
        assert setup_notion.main([PAGE_ID, "--dashboard"]) == 0
        out = capsys.readouterr().out
        assert "환경변수 세 개를 넣으세요" in out
        assert "NOTION_COURSE_DATABASE_ID" in out and "course-db" in out

    def test_network_drop_also_shows_the_partial_build_hint(self, monkeypatch, capsys):
        """중간에 연결이 끊겨도 페이지에는 이미 일부 블록이 생겨 있을 수 있다.
        HTTPError/값 오류와 같은 안내를 받아야 한다."""
        monkeypatch.setenv("NOTION_TOKEN", "tok")
        monkeypatch.setenv("NOTION_DATABASE_ID", "jobs")

        def fake_build(request, *, page_id, jobs_database_id):
            raise OSError("boom")

        monkeypatch.setattr(setup_notion, "build_dashboard", fake_build)
        assert setup_notion.main([PAGE_ID, "--dashboard"]) == 1
        err = capsys.readouterr().err
        assert "boom" in err
        assert setup_notion._PARTIAL_BUILD_HINT in err


class TestCoursesFlag:
    def test_courses_flag_needs_the_skill_database(self, monkeypatch, capsys):
        monkeypatch.setenv("NOTION_TOKEN", "tok")
        monkeypatch.delenv("NOTION_SKILL_DATABASE_ID", raising=False)
        assert setup_notion.main([PAGE_ID, "--courses"]) == 1
        assert "NOTION_SKILL_DATABASE_ID" in capsys.readouterr().err

    def test_courses_flag_prints_the_env_var(self, monkeypatch, capsys):
        monkeypatch.setenv("NOTION_TOKEN", "tok")
        monkeypatch.setenv("NOTION_SKILL_DATABASE_ID", "skill-db")
        monkeypatch.setattr(setup_notion, "build_courses", lambda request, *, page_id, skill_database_id: "course-db")
        assert setup_notion.main([PAGE_ID, "--courses"]) == 0
        out = capsys.readouterr().out
        assert "NOTION_COURSE_DATABASE_ID" in out and "course-db" in out
