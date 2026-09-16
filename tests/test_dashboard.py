"""대시보드 매일 갱신.

스킬 DB 는 보여 주기 전용이라 덮어써도 되지만, 대시보드 페이지는 사용자의
페이지다. 여기서 지키는 것은 **요약 영역 밖의 블록을 절대 지우지 않는 것**이다.
"""
import json
import urllib.parse

from analyze.gap import AnalyzedPosting, Gap
from analyze.skill_board import SkillRow
from collect.schema import Posting
from report.dashboard import (
    MISSING,
    OWNED,
    SKILL_DB_PROPERTIES,
    SUMMARY_HEADING,
    DashboardSync,
    HttpDashboardClient,
    Summary,
    blocks_to_replace,
    skill_properties,
    summarize,
    summary_blocks,
)


def skill(skill_id="powerbi", owned=False, **kw):
    base = dict(skill_id=skill_id, name="Power BI", category="bi", owned=owned,
                evidence=(), demand=2, companies=("쿠팡", "컬리"))
    return SkillRow(**{**base, **kw})


def heading(block_id, text, level=2):
    kind = f"heading_{level}"
    return {"id": block_id, "type": kind, kind: {"rich_text": [{"plain_text": text}]}}


def block(block_id, kind="paragraph"):
    return {"id": block_id, "type": kind, kind: {"rich_text": []}}


class FakeDashboard:
    def __init__(self, rows=(), children=()):
        self.rows = list(rows)
        self.children = list(children)
        self.created, self.updated, self.archived = [], [], []
        self.deleted, self.appended = [], []

    def iter_skill_rows(self):
        yield from self.rows

    def create_skill(self, properties):
        self.created.append(properties)
        return "new"

    def update_skill(self, page_id, properties):
        self.updated.append((page_id, properties))

    def archive(self, page_id):
        self.archived.append(page_id)

    def page_children(self):
        return self.children

    def delete_block(self, block_id):
        self.deleted.append(block_id)

    def append_after(self, after_id, children):
        self.appended.append((after_id, children))


class TestSkillProperties:
    def test_every_column_is_written_with_its_declared_type(self):
        props = skill_properties(skill())
        assert set(props) == set(SKILL_DB_PROPERTIES)
        for name, kind in SKILL_DB_PROPERTIES.items():
            assert kind in props[name], name

    def test_owned_label_and_category_label(self):
        assert skill_properties(skill(owned=True))["보유"] == {"select": {"name": OWNED}}
        assert skill_properties(skill())["보유"] == {"select": {"name": MISSING}}
        assert skill_properties(skill())["분류"] == {"select": {"name": "시각화"}}

    def test_empty_category_clears_the_select(self):
        assert skill_properties(skill(category=""))["분류"] == {"select": None}

    def test_demand_and_key(self):
        props = skill_properties(skill())
        assert props["요구 공고 수"] == {"number": 2}
        assert props["id"]["rich_text"][0]["text"]["content"] == "powerbi"


class TestSummary:
    def test_counts(self):
        posting = Posting(
            source="g", source_id="1", company="쿠팡", title="분석가", location="서울",
            url="https://e.com/1", body_html="", posted_at=None, fetched_at="2026-09-15T00:00:00+09:00",
        )
        # 경험 매칭이 없고 타겟 회사도 아니므로 우선순위는 '낮음'이다.
        rows = [AnalyzedPosting(posting=posting, company=None, gap=Gap(matched=(), missing=()))]
        summary = summarize(rows, created=1)
        assert (summary.open_postings, summary.high_priority, summary.new_postings) == (1, 0, 1)

    def test_blocks_are_a_caption_and_three_cards_side_by_side(self):
        blocks = summary_blocks(Summary(open_postings=12, high_priority=1, new_postings=3), "2026-09-15")
        assert blocks[0]["type"] == "paragraph"
        assert "2026-09-15" in blocks[0]["paragraph"]["rich_text"][0]["text"]["content"]
        columns = blocks[1]["column_list"]["children"]
        texts = ["".join(t["text"]["content"] for t in c["column"]["children"][0]["callout"]["rich_text"])
                 for c in columns]
        assert texts == ["오늘 분석한 공고\n12", "우선순위 높음\n1", "새로 들어온 공고\n3"]


class TestBlocksToReplace:
    def test_only_blocks_between_summary_and_next_heading(self):
        children = [block("intro"), heading("h1", SUMMARY_HEADING), block("old1"),
                    block("old2", "column_list"), heading("h2", "지금 볼 공고"), block("view")]
        assert blocks_to_replace(children) == ("h1", ("old1", "old2"))

    def test_no_summary_heading(self):
        assert blocks_to_replace([block("a"), heading("h", "다른 제목")]) == (None, ())

    def test_summary_at_the_end(self):
        assert blocks_to_replace([heading("h1", SUMMARY_HEADING), block("x")]) == ("h1", ("x",))

    def test_child_pages_and_linked_views_end_the_region_too(self):
        """제목이 아니어도 하위 페이지나 보기를 만나면 요약 영역이 끝난다.

        사용자가 다음 제목을 지워도 그 뒤의 하위 페이지나 연결된 보기까지
        요약으로 오인해 지우면 안 된다.
        """
        children = [
            heading("h1", SUMMARY_HEADING),
            block("old"),
            block("old2", "column_list"),
            block("view", "child_database"),
            block("p", "child_page"),
            block("after"),
        ]
        assert blocks_to_replace(children) == ("h1", ("old", "old2"))


class TestSyncSkills:
    def test_update_existing_create_new_archive_leftover(self):
        fake = FakeDashboard(rows=[("p1", "powerbi"), ("p2", "spark"), ("p3", "")])
        result = DashboardSync(fake).sync_skills([skill("powerbi"), skill("ab_test", name="A/B 테스트")])

        assert [page for page, _ in fake.updated] == ["p1"]
        assert len(fake.created) == 1
        assert fake.archived == ["p2"]
        assert (result.created, result.updated, result.archived, result.failed) == (1, 1, 1, 0)

    def test_row_without_key_is_left_alone(self):
        """사용자가 손으로 추가한 행일 수 있다."""
        fake = FakeDashboard(rows=[("p3", "")])
        DashboardSync(fake).sync_skills([])
        assert fake.archived == []

    def test_duplicate_key_is_archived(self):
        fake = FakeDashboard(rows=[("p1", "powerbi"), ("p9", "powerbi")])
        DashboardSync(fake).sync_skills([skill("powerbi")])
        assert fake.archived == ["p9"]

    def test_one_failure_does_not_stop_the_rest(self):
        class Flaky(FakeDashboard):
            def create_skill(self, properties):
                if properties["id"]["rich_text"][0]["text"]["content"] == "bad":
                    raise OSError("boom")
                return super().create_skill(properties)

        fake = Flaky()
        result = DashboardSync(fake).sync_skills([skill("bad"), skill("good")])
        assert (result.created, result.failed) == (1, 1)


class TestWriteSummary:
    def test_appends_new_blocks_before_deleting_old_ones(self):
        """새 블록을 먼저 넣는다. 지운 뒤 추가가 실패하면 요약이 통째로 사라진다."""
        fake = FakeDashboard(children=[heading("h1", SUMMARY_HEADING), block("old"), heading("h2", "다음")])
        calls = []
        fake.append_after = lambda after, children: calls.append(("append", after))
        fake.delete_block = lambda block_id: calls.append(("delete", block_id))

        assert DashboardSync(fake).write_summary(Summary(1, 0, 0), "2026-09-15") is True
        assert calls == [("append", "h1"), ("delete", "old")]

    def test_missing_heading_writes_nothing(self, capsys):
        fake = FakeDashboard(children=[block("a")])
        assert DashboardSync(fake).write_summary(Summary(1, 0, 0), "2026-09-15") is False
        assert fake.appended == [] and fake.deleted == []
        assert SUMMARY_HEADING in capsys.readouterr().err


class TestFromEnv:
    def test_none_without_all_three(self, monkeypatch):
        monkeypatch.setenv("NOTION_TOKEN", "t")
        monkeypatch.setenv("NOTION_SKILL_DATABASE_ID", "s")
        monkeypatch.delenv("NOTION_DASHBOARD_PAGE_ID", raising=False)
        assert DashboardSync.from_env() is None

    def test_built_with_all_three(self, monkeypatch):
        for name in ("NOTION_TOKEN", "NOTION_SKILL_DATABASE_ID", "NOTION_DASHBOARD_PAGE_ID"):
            monkeypatch.setenv(name, "x")
        assert DashboardSync.from_env() is not None


class _Response:
    """opener 가 주는 가짜 응답. read() 한 번만 부른다."""

    def __init__(self, payload):
        self._raw = json.dumps(payload).encode("utf-8") if payload is not None else b""

    def read(self):
        return self._raw


class TestHttpDashboardClientContract:
    """HttpDashboardClient 가 실제로 보내는 요청 모양을 검증한다.

    FakeDashboard 는 딕셔너리 조회라 URL 인코딩이나 페이지네이션 같은 HTTP
    세부 사항을 검증하지 못한다. 커서를 그대로 이어붙이면 요청이 깨질 수
    있어서(위 인코딩 수정의 이유), 여기서 실제 Request 객체를 기록해 확인한다.
    """

    def _scripted(self, payloads):
        """호출 순서대로 준비된 응답을 내주는 opener. 보낸 request 를 기록한다."""
        sent = []

        def opener(request, timeout=0):
            sent.append(request)
            return _Response(payloads[len(sent) - 1])

        return opener, sent

    def test_page_children_paginates_with_url_encoded_cursor(self):
        cursor = "a+b/c="
        pages = [
            {"results": [{"id": "b1"}], "has_more": True, "next_cursor": cursor},
            {"results": [{"id": "b2"}], "has_more": False},
        ]
        opener, sent = self._scripted(pages)
        client = HttpDashboardClient("t", "skill-db", "page-1", opener=opener)

        children = client.page_children()

        assert [b["id"] for b in children] == ["b1", "b2"]
        assert len(sent) == 2
        for request in sent:
            assert request.get_method() == "GET"
            assert request.data is None

        # 커서를 그대로 이어붙이면 "+"와 "/"가 다른 문자로 해석돼 요청이
        # 깨진다. 인코딩을 거치면 되돌려 읽어도 원래 커서가 나온다.
        query = urllib.parse.urlparse(sent[1].full_url).query
        assert urllib.parse.parse_qs(query)["start_cursor"] == [cursor]

    def test_iter_skill_rows_paginates_and_reads_id_text(self):
        pages = [
            {
                "results": [
                    {"id": "p1", "properties": {"id": {"rich_text": [{"plain_text": "powerbi"}]}}},
                ],
                "has_more": True,
                "next_cursor": "c2",
            },
            {
                # id 열에 rich_text 가 없는 행: 사용자가 손으로 추가한 행일 수 있다.
                "results": [{"id": "p2", "properties": {"id": {"rich_text": []}}}],
                "has_more": False,
            },
        ]
        sent_bodies = []

        def opener(request, timeout=0):
            sent_bodies.append(json.loads(request.data.decode("utf-8")))
            return _Response(pages[len(sent_bodies) - 1])

        client = HttpDashboardClient("t", "skill-db", "page-1", opener=opener)
        rows = list(client.iter_skill_rows())

        assert rows == [("p1", "powerbi"), ("p2", "")]
        assert sent_bodies[1]["start_cursor"] == "c2"

    def test_append_after_sends_children_and_after(self):
        opener, sent = self._scripted([{}])
        client = HttpDashboardClient("t", "skill-db", "page-1", opener=opener)

        client.append_after("h1", [{"type": "paragraph"}])

        request = sent[0]
        assert request.get_method() == "PATCH"
        assert request.full_url.endswith("/blocks/page-1/children")
        assert json.loads(request.data.decode("utf-8")) == {
            "children": [{"type": "paragraph"}],
            "after": "h1",
        }

    def test_archive_sends_archived_true(self):
        opener, sent = self._scripted([{}])
        client = HttpDashboardClient("t", "skill-db", "page-1", opener=opener)

        client.archive("page-9")

        request = sent[0]
        assert request.get_method() == "PATCH"
        assert request.full_url.endswith("/pages/page-9")
        assert json.loads(request.data.decode("utf-8")) == {"archived": True}
