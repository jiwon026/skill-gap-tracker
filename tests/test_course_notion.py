"""강의 DB 적재.

공고 DB 와 같은 원칙이다. 사용자가 쓰는 '상태'를 덮지 않고, 추천에서 빠진
과정도 지우지 않는다. 지우면 '관심'으로 표시해 둔 과정이 사라진다.
"""
import pytest

from analyze.recommend import DIRECT, FOUNDATION, Recommendation
from collect.training import Course
from report.course_notion import (
    COURSE_DB_PROPERTIES,
    FEATURED,
    INITIAL_STATUS,
    OPEN_RECOMMENDATION,
    PAST_RECOMMENDATION,
    CourseSync,
    course_properties,
)


def course(**kw):
    base = dict(course_id="C1", degree="14", institution_id="I1", title="Power BI 입문",
                institution="어느학원", address="서울 강남구", start="2026-10-14", end="2026-11-23",
                target="국민내일배움카드(일반)", weekend="3", satisfaction=89.3, capacity=20,
                applicants=3, total_fee=329850, url="https://work24.go.kr/x", own_fee=43000)
    return Course(**{**base, **kw})


def rec(kind=DIRECT, **kw):
    return Recommendation(skill_id="powerbi", skill_name="Power BI", kind=kind, course=course(**kw))


class FakeCourses:
    def __init__(self, existing=None, rows=()):
        self.existing = existing or {}
        self.rows = list(rows)
        self.created, self.updated = [], []

    def find_page_id(self, key):
        return self.existing.get(key)

    def create_page(self, properties):
        self.created.append(properties)
        return "new"

    def update_page(self, page_id, properties):
        self.updated.append((page_id, properties))

    def iter_rows(self):
        yield from self.rows


class TestProperties:
    def test_every_column_is_written_with_its_type(self):
        props = course_properties(rec(), skill_page_id="skill-1")
        assert set(props) == set(COURSE_DB_PROPERTIES)
        for name, kind in COURSE_DB_PROPERTIES.items():
            assert kind in props[name], name

    def test_status_is_only_set_on_new_rows(self):
        assert course_properties(rec(), skill_page_id="s")["상태"] == {"select": {"name": INITIAL_STATUS}}
        assert "상태" not in course_properties(rec(), skill_page_id="s", for_update=True)

    def test_relation_points_at_the_skill_row(self):
        props = course_properties(rec(), skill_page_id="skill-1")
        assert props["스킬"] == {"relation": [{"id": "skill-1"}]}

    def test_money_and_schedule(self):
        props = course_properties(rec(), skill_page_id="s")
        assert props["본인부담액"] == {"number": 43000}
        assert props["훈련비"] == {"number": 329850}
        assert "2026-10-14" in props["기간"]["rich_text"][0]["text"]["content"]
        assert props["수업 방식"] == {"select": {"name": "주중"}}
        assert props["연결"] == {"select": {"name": DIRECT}}

    def test_remote_courses_say_online(self):
        props = course_properties(rec(target="실업자원격훈련", weekend="9"), skill_page_id="s")
        assert props["수업 방식"] == {"select": {"name": "온라인"}}

    def test_unknown_own_fee_is_null_not_missing(self):
        """속성을 빼면 어제 값이 남는다."""
        assert course_properties(rec(own_fee=None), skill_page_id="s")["본인부담액"] == {"number": None}

    def test_listing_starts_as_open(self):
        assert course_properties(rec(), skill_page_id="s")["추천 현황"] == {"select": {"name": OPEN_RECOMMENDATION}}

    def test_featured_marks_only_the_courses_shown_on_the_front_page(self):
        assert course_properties(rec(), skill_page_id="s", featured=True)["첫 화면"] == {"select": {"name": FEATURED}}

    def test_featured_is_cleared_when_a_course_drops_off_the_front_page(self):
        """빈 값으로 되돌린다. 안 그러면 어제 고른 과정이 첫 화면에 남는다."""
        assert course_properties(rec(), skill_page_id="s")["첫 화면"] == {"select": None}
        assert course_properties(rec(), skill_page_id="s", for_update=True)["첫 화면"] == {"select": None}


class TestPush:
    def test_creates_and_updates_by_key(self):
        fake = FakeCourses(existing={"powerbi:C1:14:I1": "page-1"})
        result = CourseSync(fake).push([rec(), rec(course_id="C2")], {"powerbi": "skill-1"})
        assert (result.created, result.updated, result.failed) == (1, 1, 0)
        assert fake.updated[0][0] == "page-1"
        assert "상태" not in fake.updated[0][1]

    def test_a_skill_without_a_notion_row_is_skipped(self):
        fake = FakeCourses()
        result = CourseSync(fake).push([rec()], {})
        assert (result.created, result.failed) == (0, 0)
        assert fake.created == []

    def test_one_failure_does_not_stop_the_rest(self):
        class Flaky(FakeCourses):
            def create_page(self, properties):
                if properties["과정명"]["title"][0]["text"]["content"] == "나쁜 과정":
                    raise OSError("boom")
                return super().create_page(properties)

        fake = Flaky()
        result = CourseSync(fake).push([rec(title="나쁜 과정"), rec(course_id="C9")], {"powerbi": "s"})
        assert (result.created, result.failed) == (1, 1)

    def test_the_same_pair_is_written_once(self):
        fake = FakeCourses()
        CourseSync(fake).push([rec(), rec()], {"powerbi": "s"})
        assert len(fake.created) == 1


class TestRetire:
    def test_rows_that_left_the_list_become_past(self):
        fake = FakeCourses(rows=[("p1", "powerbi:C1:14:I1", OPEN_RECOMMENDATION),
                                 ("p2", "powerbi:C9:1:I1", OPEN_RECOMMENDATION)])
        changed = CourseSync(fake).retire({"powerbi:C1:14:I1"})
        assert changed == 1
        assert fake.updated == [("p2", {"추천 현황": {"select": {"name": PAST_RECOMMENDATION}}})]

    def test_already_past_rows_are_left_alone(self):
        fake = FakeCourses(rows=[("p2", "powerbi:C9:1:I1", PAST_RECOMMENDATION)])
        assert CourseSync(fake).retire(set()) == 0
        assert fake.updated == []

    def test_rows_without_a_key_are_left_alone(self):
        fake = FakeCourses(rows=[("p3", "", OPEN_RECOMMENDATION)])
        assert CourseSync(fake).retire(set()) == 0
        assert fake.updated == []
