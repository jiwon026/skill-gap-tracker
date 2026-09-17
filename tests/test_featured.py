"""첫 화면에 올릴 상위 몇 개를 고른다.

Notion 보기는 '상위 N 개'를 못 건다(2026-09-17 확인: 차트 그룹 제한도,
표 행 제한도 없다). 그래서 파이프라인이 미리 골라 표시해 두고, 보기는
그 표시를 조건으로 건다.
"""
from analyze.featured import FEATURED_LIMIT, featured_courses, top_missing_skills


class FakeGap:
    def __init__(self, missing):
        self._missing = missing

    def labeled(self, names, which):
        assert which == "missing"
        return tuple(names.get(i, i) for i in self._missing)


class FakeRow:
    def __init__(self, missing):
        self.gap = FakeGap(missing)


NAMES = {"powerbi": "Power BI", "ab": "A/B 테스트", "spark": "Apache Spark",
         "gcp": "GCP", "looker": "Looker", "snowflake": "Snowflake"}


def test_top_missing_skills_counts_postings_and_cuts_at_the_limit():
    rows = [
        FakeRow(["powerbi", "ab"]),
        FakeRow(["powerbi", "gcp"]),
        FakeRow(["powerbi"]),
        FakeRow(["ab", "looker"]),
        FakeRow(["spark"]),
        FakeRow(["snowflake"]),
    ]
    # 3위는 1건짜리 동률이라 이름순으로 'Apache Spark' 가 온다.
    assert top_missing_skills(rows, NAMES, limit=3) == ("Power BI", "A/B 테스트", "Apache Spark")


def test_top_missing_skills_breaks_ties_by_name_so_the_chart_is_stable():
    """동률이면 이름순. 안 그러면 사전 순서가 바뀔 때마다 막대가 갈아끼워진다."""
    rows = [FakeRow(["looker"]), FakeRow(["gcp"]), FakeRow(["spark"])]
    assert top_missing_skills(rows, NAMES, limit=2) == ("Apache Spark", "GCP")


def test_top_missing_skills_defaults_to_five():
    rows = [FakeRow([k]) for k in NAMES]
    assert len(top_missing_skills(rows, NAMES)) == FEATURED_LIMIT == 5


class FakeRec:
    def __init__(self, skill_id, key):
        self.skill_id = skill_id
        self.key = f"{skill_id}:{key}"


def test_featured_courses_take_turns_so_one_skill_cannot_fill_the_screen():
    """Power BI 과정 6개가 다섯 자리를 다 먹으면 다른 역량이 안 보인다."""
    recs = [FakeRec("powerbi", f"p{i}") for i in range(6)]
    recs += [FakeRec("ab", f"a{i}") for i in range(5)]

    picked = featured_courses(recs, limit=4)

    assert len(picked) == 4
    assert sorted(picked) == ["ab:a0", "ab:a1", "powerbi:p0", "powerbi:p1"]


def test_featured_courses_keep_the_recommendation_order_within_a_skill():
    recs = [FakeRec("powerbi", "first"), FakeRec("powerbi", "second"), FakeRec("ab", "only")]
    assert featured_courses(recs, limit=2) == frozenset({"powerbi:first", "ab:only"})


def test_featured_courses_fall_back_to_one_skill_when_there_is_nothing_else():
    recs = [FakeRec("powerbi", f"p{i}") for i in range(4)]
    assert featured_courses(recs, limit=3) == frozenset({"powerbi:p0", "powerbi:p1", "powerbi:p2"})


def test_featured_courses_ignore_duplicate_keys():
    recs = [FakeRec("powerbi", "same"), FakeRec("powerbi", "same"), FakeRec("ab", "x")]
    assert featured_courses(recs, limit=3) == frozenset({"powerbi:same", "ab:x"})


def test_featured_courses_handle_an_empty_recommendation():
    assert featured_courses([], limit=5) == frozenset()
