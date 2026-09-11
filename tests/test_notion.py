"""Notion 적재.

이 계층에서 틀리면 사용자의 작업이 지워진다. 지켜야 할 것 셋.

  - 같은 공고를 두 번 쌓지 않는다(`key`로 upsert).
  - **이미 있는 행의 '상태'를 절대 덮지 않는다.** 사용자가 '지원함'으로
    바꿔 둔 것을 매일 '신규'로 되돌리면 이 도구는 못 쓴다.
  - 자격증명이 없으면 조용히 건너뛴다. 파이프라인 전체가 멈추면 안 된다.
"""
import pytest

from analyze.gap import AnalyzedPosting, Gap
from collect.schema import Posting
from extract.companies import Company, CompanyBook
from report.notion import LISTING_STATUSES, NotionSync, build_properties, listing_status

SKILL_NAMES = {"sql": "SQL", "airflow": "Apache Airflow", "dbt": "dbt"}


def make_posting(**kw):
    base = dict(
        source="woowahan",
        source_id="R2609008",
        company="우아한형제들",
        title="데이터 분석가",
        location="대한민국",
        url="https://career.woowahan.com/recruitment/R2609008/detail",
        body_html="<p>본문</p>",
        posted_at="2026-09-01T10:00:00+09:00",
        fetched_at="2026-09-09T00:00:00+09:00",
    )
    return Posting(**{**base, **kw})


def make_analyzed(**kw):
    book = CompanyBook.from_config(
        {"companies": [{"name": "우아한형제들", "tier": "대기업",
                        "segment": "배달·퀵커머스", "aliases": ["우아한형제들"]}]}
    )
    base = dict(
        posting=make_posting(),
        company=book.identify("우아한형제들"),
        gap=Gap(matched=("sql",), missing=("airflow", "dbt")),
    )
    return AnalyzedPosting(**{**base, **kw})


class FakeNotion:
    """호출을 기록만 하는 대역. 네트워크를 타지 않는다."""

    def __init__(self, existing=None):
        self.existing = existing or {}
        self.listing = {}
        self.created, self.updated, self.queried = [], [], []

    def find_page_id(self, key):
        self.queried.append(key)
        return self.existing.get(key)

    def create_page(self, properties):
        self.created.append(properties)
        return "new-page-id"

    def update_page(self, page_id, properties):
        self.updated.append((page_id, properties))

    def iter_rows(self):
        """(page_id, key, 공고 현황). 현황은 listing 에 따로 적어 둔다."""
        for key, page_id in self.existing.items():
            yield page_id, key, self.listing.get(key)


class TestProperties:
    def test_key_is_the_posting_key(self):
        props = build_properties(make_analyzed(), SKILL_NAMES)
        assert props["key"]["rich_text"][0]["text"]["content"] == "woowahan:R2609008"

    def test_title_company_and_url_are_mapped(self):
        props = build_properties(make_analyzed(), SKILL_NAMES)
        assert props["공고명"]["title"][0]["text"]["content"] == "데이터 분석가"
        assert props["회사"]["select"]["name"] == "우아한형제들"
        assert props["URL"]["url"].startswith("https://")

    def test_company_uses_whitelist_name_not_raw(self):
        """'쿠팡풀필먼트서비스'가 아니라 '쿠팡'으로 묶여야 집계가 된다."""
        company = Company(name="쿠팡", tier="대기업", segment="종합몰",
                          matcher=__import__("re").compile("쿠팡"))
        analyzed = make_analyzed(
            posting=make_posting(company="쿠팡풀필먼트서비스"), company=company
        )
        assert build_properties(analyzed, SKILL_NAMES)["회사"]["select"]["name"] == "쿠팡"

    def test_skills_are_human_names_not_ids(self):
        props = build_properties(make_analyzed(), SKILL_NAMES)
        missing = [o["name"] for o in props["부족 스킬"]["multi_select"]]
        assert missing == ["Apache Airflow", "dbt"]

    def test_skill_count_columns_are_gone(self):
        """보유·부족 스킬 목록이 바로 옆에 있어 개수 열은 중복이다(2026-09-10 요청)."""
        props = build_properties(make_analyzed(), SKILL_NAMES)
        assert "매칭 스킬 수" not in props and "요구 스킬 수" not in props

    def test_posted_date_column_is_gone(self):
        """게시일을 주지 않는 소스가 있어 열이 대부분 비어 있었다(2026-09-10 요청으로
        삭제). 게시일은 스냅샷의 Posting.posted_at 에는 계속 남는다."""
        props = build_properties(make_analyzed(), SKILL_NAMES)
        assert "게시일" not in props

    def test_status_is_set_only_on_new_rows(self):
        """상태는 사용자의 것이다. 신규 생성 때만 초깃값을 넣는다."""
        props = build_properties(make_analyzed(), SKILL_NAMES)
        assert props["상태"]["select"]["name"] == "신규"
        assert "상태" not in build_properties(make_analyzed(), SKILL_NAMES, for_update=True)


class TestSync:
    def test_creates_a_row_for_a_new_posting(self):
        client = FakeNotion()
        result = NotionSync(client).push([make_analyzed()], SKILL_NAMES)
        assert result.created == 1 and result.updated == 0
        assert len(client.created) == 1

    def test_updates_instead_of_duplicating_a_known_posting(self):
        client = FakeNotion(existing={"woowahan:R2609008": "page-1"})
        result = NotionSync(client).push([make_analyzed()], SKILL_NAMES)
        assert result.created == 0 and result.updated == 1
        assert client.updated[0][0] == "page-1"
        assert not client.created

    def test_update_never_touches_status(self):
        client = FakeNotion(existing={"woowahan:R2609008": "page-1"})
        NotionSync(client).push([make_analyzed()], SKILL_NAMES)
        assert "상태" not in client.updated[0][1]

    def test_one_failing_row_does_not_abort_the_rest(self):
        class Flaky(FakeNotion):
            def create_page(self, properties):
                if properties["key"]["rich_text"][0]["text"]["content"].endswith("BAD"):
                    raise OSError("Notion 5xx")
                return super().create_page(properties)

        client = Flaky()
        rows = [
            make_analyzed(posting=make_posting(source_id="BAD")),
            make_analyzed(posting=make_posting(source_id="OK")),
        ]
        result = NotionSync(client).push(rows, SKILL_NAMES)
        assert result.created == 1
        assert result.failed == 1

    def test_push_is_a_noop_without_rows(self):
        client = FakeNotion()
        result = NotionSync(client).push([], SKILL_NAMES)
        assert (result.created, result.updated, result.failed) == (0, 0, 0)
        assert not client.queried


class TestCredentials:
    def test_from_env_returns_none_when_unconfigured(self, monkeypatch):
        monkeypatch.delenv("NOTION_TOKEN", raising=False)
        monkeypatch.delenv("NOTION_DATABASE_ID", raising=False)
        assert NotionSync.from_env() is None

    def test_from_env_requires_both_values(self, monkeypatch):
        monkeypatch.setenv("NOTION_TOKEN", "secret")
        monkeypatch.delenv("NOTION_DATABASE_ID", raising=False)
        assert NotionSync.from_env() is None

    def test_from_env_builds_a_sync_when_configured(self, monkeypatch):
        monkeypatch.setenv("NOTION_TOKEN", "secret")
        monkeypatch.setenv("NOTION_DATABASE_ID", "db-1")
        assert isinstance(NotionSync.from_env(), NotionSync)


class TestHttpClientContract:
    """`find_page_id`의 조회 속성명과 `build_properties`가 쓰는 속성명은
    같아야 upsert가 성립한다. 갈라지면 매 실행이 모든 공고를 중복 생성하고
    사용자가 편집한 '상태'가 전부 '신규'인 새 행에 묻힌다.

    FakeNotion은 딕셔너리 조회라 이 계약을 검증하지 못한다.
    """

    def _client(self, response):
        import json as _json

        sent = []

        def opener(request, timeout=0):
            sent.append((request.full_url, _json.loads(request.data.decode("utf-8"))))
            return type("R", (), {"read": lambda self: _json.dumps(response).encode()})()

        from report.notion import HttpNotionClient

        return HttpNotionClient("token", "db-1", opener=opener), sent

    def test_query_filters_on_the_same_property_that_is_written(self):
        client, sent = self._client({"results": []})
        client.find_page_id("woowahan:R1")

        _, body = sent[0]
        queried = body["filter"]["property"]
        assert queried in build_properties(make_analyzed(), SKILL_NAMES), (
            f"조회 속성 {queried!r}이 build_properties 가 쓰는 속성에 없다"
        )
        assert body["filter"]["rich_text"]["equals"] == "woowahan:R1"

    def test_query_property_type_matches_how_the_value_is_written(self):
        client, sent = self._client({"results": []})
        client.find_page_id("k")

        _, body = sent[0]
        written = build_properties(make_analyzed(), SKILL_NAMES)[body["filter"]["property"]]
        assert "rich_text" in written and "rich_text" in body["filter"]

    def test_find_page_id_returns_the_first_result(self):
        client, _ = self._client({"results": [{"id": "page-9"}]})
        assert client.find_page_id("k") == "page-9"

    def test_create_page_targets_the_configured_database(self):
        client, sent = self._client({"id": "new"})
        client.create_page({"key": _key_property("x")})
        assert sent[0][1]["parent"]["database_id"] == "db-1"

    def test_update_page_sends_only_properties(self):
        client, sent = self._client({})
        client.update_page("page-1", {"지역": {"rich_text": []}})
        assert sent[0][0].endswith("/pages/page-1")
        assert set(sent[0][1]) == {"properties"}


def _key_property(value):
    return {"rich_text": [{"text": {"content": value}}]}


class TestSelectSanitizing:
    def test_comma_in_company_name_is_removed(self):
        """Notion은 select 옵션명에 쉼표를 허용하지 않는다. 그 행만 400으로 죽는다."""
        analyzed = make_analyzed(
            posting=make_posting(company="주식회사 가나다, 라마바"), company=None
        )
        assert "," not in build_properties(analyzed, SKILL_NAMES)["회사"]["select"]["name"]

    def test_empty_company_falls_back_instead_of_failing(self):
        analyzed = make_analyzed(posting=make_posting(company=" "), company=None)
        assert build_properties(analyzed, SKILL_NAMES)["회사"]["select"]["name"] == "미상"


class TestDuplicateKeysWithinOneRun:
    def test_same_key_twice_creates_only_one_row(self):
        """사람인 키워드 두 개가 같은 공고를 준다. 두 번 create 하면 중복 행이 남고
        사용자가 편집한 '상태'가 다음 실행부터 보이지 않게 된다."""
        client = FakeNotion()
        result = NotionSync(client).push([make_analyzed(), make_analyzed()], SKILL_NAMES)
        assert result.created == 1
        assert len(client.created) == 1


class TestPriorityProperty:
    def test_priority_is_written_as_a_select(self):
        from analyze.priority import PRIORITIES

        props = build_properties(make_analyzed(), SKILL_NAMES)
        assert props["우선순위"]["select"]["name"] in PRIORITIES

    def test_tier_column_is_no_longer_written(self):
        """adjacent 가 비면서 전 행이 '바로 지원 대상'이 됐다. 정보가 없는 열이라
        뺐고, 직군 판정은 통과/제외 하나로 줄었다(2026-09-11)."""
        assert "직군" not in build_properties(make_analyzed(), SKILL_NAMES)

    def test_priority_is_refreshed_on_update(self):
        """상태와 달리 우선순위는 매 실행 다시 계산된다. 사용자의 것이 아니다."""
        props = build_properties(make_analyzed(), SKILL_NAMES, for_update=True)
        assert "우선순위" in props


class TestSchemaCheckContract:
    """check_notion.py 의 EXPECTED 와 build_properties 가 갈라지면, 점검은
    통과했는데 실제 적재는 전부 400으로 실패한다. push() 는 실패 건수만
    세고 넘어가므로 첫 실행에서 조용히 0건이 된다.
    """

    def test_expected_names_match_written_properties(self):
        import check_notion

        written = build_properties(make_analyzed(), SKILL_NAMES)
        assert set(check_notion.EXPECTED) == set(written)

    def test_expected_types_match_written_values(self):
        import check_notion

        written = build_properties(make_analyzed(), SKILL_NAMES)
        for name, expected_type in check_notion.EXPECTED.items():
            assert expected_type in written[name], (
                f"{name!r}: 점검은 {expected_type} 를 기대하는데 {list(written[name])} 를 보낸다"
            )


class TestListingStatus:
    """결과에 없는 행을 어떻게 볼 것인가.

    '없음'을 전부 '제외됨'으로 찍으면, 어느 날 쿠팡 수집이 네트워크 오류로
    실패했을 때 멀쩡한 쿠팡 공고가 전부 사라진 것처럼 보인다. 그래서 확실히
    아는 경우만 표시하고, 모르면 건드리지 않는다.
    """

    KW = dict(
        active={"greenhouse:1"},
        collected={"greenhouse:1", "greenhouse:2"},
        complete_sources=frozenset({"greenhouse"}),
    )

    def test_in_todays_results_is_open(self):
        assert listing_status("greenhouse:1", **self.KW) == "모집중"

    def test_collected_but_filtered_out_is_excluded(self):
        """이번 경력 필터 수정으로 빠진 7건이 이 경우다."""
        assert listing_status("greenhouse:2", **self.KW) == "제외됨"

    def test_missing_from_a_complete_source_is_closed(self):
        assert listing_status("greenhouse:9", **self.KW) == "마감"

    def test_missing_from_an_incomplete_source_is_left_alone(self):
        """수집이 실패한 날 공고가 전부 마감으로 찍히면 안 된다."""
        kw = {**self.KW, "complete_sources": frozenset()}
        assert listing_status("greenhouse:9", **kw) is None

    def test_search_sources_are_never_judged_closed(self):
        """사람인은 키워드 검색이라 목록에 없다고 마감이 아니다."""
        assert listing_status("saramin:77", **self.KW) is None

    def test_collected_from_an_incomplete_source_is_still_excluded(self):
        """수집됐다는 것도, 필터에 걸렸다는 것도 확실하다."""
        kw = dict(active=set(), collected={"saramin:5"}, complete_sources=frozenset())
        assert listing_status("saramin:5", **kw) == "제외됨"

    def test_statuses_run_from_open_to_closed(self):
        """Notion select 는 옵션 순서대로 정렬된다."""
        assert LISTING_STATUSES == ("모집중", "제외됨", "마감")


class TestListingStatusProperty:
    def test_pushed_rows_are_marked_open(self):
        props = build_properties(make_analyzed(), SKILL_NAMES)
        assert props["공고 현황"]["select"]["name"] == "모집중"

    def test_open_is_restored_on_update(self):
        """규칙이 느슨해져 다시 결과에 들어오면 모집중으로 돌아와야 한다."""
        props = build_properties(make_analyzed(), SKILL_NAMES, for_update=True)
        assert props["공고 현황"]["select"]["name"] == "모집중"


class TestRetire:
    def _client(self, rows):
        """rows: {key: (page_id, 현재 공고 현황)}"""
        client = FakeNotion(existing={k: v[0] for k, v in rows.items()})
        client.listing = {k: v[1] for k, v in rows.items()}
        return client

    def _retire(self, client, **kw):
        base = dict(active=set(), collected=set(), complete_sources=frozenset({"greenhouse"}))
        return NotionSync(client).retire(**{**base, **kw})

    def test_filtered_row_is_marked_excluded(self):
        client = self._client({"greenhouse:2": ("p2", "모집중")})
        result = self._retire(client, collected={"greenhouse:2"})
        assert client.updated == [("p2", {"공고 현황": {"select": {"name": "제외됨"}}})]
        assert result.excluded == 1

    def test_vanished_row_is_marked_closed(self):
        client = self._client({"greenhouse:9": ("p9", "모집중")})
        result = self._retire(client)
        assert client.updated == [("p9", {"공고 현황": {"select": {"name": "마감"}}})]
        assert result.closed == 1

    def test_active_rows_are_not_touched(self):
        """push 가 이미 모집중으로 썼다. 다시 쓰면 요청만 늘어난다."""
        client = self._client({"greenhouse:1": ("p1", "모집중")})
        self._retire(client, active={"greenhouse:1"}, collected={"greenhouse:1"})
        assert client.updated == []

    def test_unchanged_status_is_not_rewritten(self):
        client = self._client({"greenhouse:9": ("p9", "마감")})
        result = self._retire(client)
        assert client.updated == []
        assert result.unchanged == 1

    def test_unknown_rows_are_left_alone(self):
        client = self._client({"saramin:7": ("p7", "모집중")})
        result = self._retire(client)
        assert client.updated == []
        assert result.skipped == 1

    def test_retire_never_writes_user_status(self):
        """'상태'는 사용자의 것이다. 공고가 마감돼도 '지원함'은 남아야 한다."""
        client = self._client({"greenhouse:9": ("p9", "모집중")})
        self._retire(client)
        assert all("상태" not in props for _, props in client.updated)

    def test_rows_without_a_key_are_ignored(self):
        """사용자가 손으로 추가한 행일 수 있다."""
        client = self._client({"": ("p0", None)})
        result = self._retire(client)
        assert client.updated == [] and result.skipped == 1

    def test_one_failing_update_does_not_abort_the_rest(self):
        class Flaky(FakeNotion):
            def update_page(self, page_id, properties):
                if page_id == "bad":
                    raise OSError("Notion 5xx")
                super().update_page(page_id, properties)

        client = Flaky(existing={"greenhouse:8": "bad", "greenhouse:9": "p9"})
        client.listing = {"greenhouse:8": "모집중", "greenhouse:9": "모집중"}
        result = NotionSync(client).retire(
            active=set(), collected=set(), complete_sources=frozenset({"greenhouse"})
        )
        assert result.closed == 1 and result.failed == 1


class TestIterRowsContract:
    def test_paginates_and_reads_key_and_listing(self):
        import json as _json

        from report.notion import HttpNotionClient

        pages = [
            {"results": [_row("p1", "greenhouse:1", "모집중")], "has_more": True, "next_cursor": "c2"},
            {"results": [_row("p2", "greenhouse:2", None)], "has_more": False},
        ]
        sent = []

        def opener(request, timeout=0):
            sent.append(_json.loads(request.data.decode("utf-8")))
            body = pages[len(sent) - 1]
            return type("R", (), {"read": lambda self: _json.dumps(body).encode()})()

        rows = list(HttpNotionClient("t", "db", opener=opener).iter_rows())
        assert rows == [("p1", "greenhouse:1", "모집중"), ("p2", "greenhouse:2", None)]
        assert sent[1]["start_cursor"] == "c2"


def _row(page_id, key, listing):
    return {
        "id": page_id,
        "properties": {
            "key": {"rich_text": [{"plain_text": key}]},
            "공고 현황": {"select": {"name": listing} if listing else None},
        },
    }


class TestDeadlineProperty:
    def _closing(self, **posting_kw):
        props = build_properties(make_analyzed(posting=make_posting(**posting_kw)), SKILL_NAMES)
        return "".join(t["text"]["content"] for t in props["마감"]["rich_text"])

    def test_rolling_shows_the_note(self):
        assert self._closing(deadline_note="상시채용") == "상시채용"

    def test_date_is_shown_when_there_is_no_note(self):
        assert self._closing(deadline="2026-09-30") == "2026-09-30"

    def test_note_wins_over_a_date(self):
        assert self._closing(deadline="9999-12-31", deadline_note="상시채용") == "상시채용"

    def test_unknown_deadline_is_sent_empty_not_omitted(self):
        """속성을 빼면 예전 값이 남는다. 마감 정보가 사라진 공고는 비워야 한다."""
        props = build_properties(make_analyzed(), SKILL_NAMES)
        assert props["마감"] == {"rich_text": []}


class TestScale:
    """'규모' 열. 화이트리스트가 이기고, 없으면 소스 회사 정보, 그것도 없으면 기타."""

    def test_whitelisted_company_uses_its_tier(self):
        row = make_analyzed(company_size="스타트업")
        assert row.scale == "대기업"
        assert build_properties(row, SKILL_NAMES)["규모"]["select"]["name"] == "대기업"

    def test_other_company_uses_the_looked_up_size(self):
        row = make_analyzed(company=None, company_size="스타트업")
        assert build_properties(row, SKILL_NAMES)["규모"]["select"]["name"] == "스타트업"

    def test_unknown_company_is_etc(self):
        row = make_analyzed(company=None)
        assert build_properties(row, SKILL_NAMES)["규모"]["select"]["name"] == "기타"
