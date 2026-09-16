"""부족 스킬과 훈련과정을 잇는 판정.

여기서 틀리면 들을 수 없는 과정을 권하게 된다. 지켜야 할 것 셋.
  - 재직자용 과정을 권하지 않는다.
  - 같은 과정의 여러 회차를 여러 건으로 세지 않는다.
  - 도구를 직접 다루는 과정과 기반 역량 과정을 구분해서 보여 준다.
"""
from analyze.recommend import DIRECT, FOUNDATION, pick, recommend, search_terms, target_skills, usable
from analyze.skill_board import SkillRow
from collect.training import Course

FILTERS = dict(
    metro_prefixes=("서울", "경기", "인천"),
    exclude_targets=("근로자원격훈련",),
    exclude_title_words=("재직자",),
)


def course(course_id="C1", degree="1", title="Power BI 입문", address="서울 강남구",
           target="국민내일배움카드(일반)", start="2026-10-14", satisfaction=90.0, **kw):
    base = dict(course_id=course_id, degree=degree, institution_id="I1", title=title,
                institution="어느학원", address=address, start=start, end="2026-11-23",
                target=target, weekend="3", satisfaction=satisfaction, capacity=20,
                applicants=3, total_fee=329850, url="https://work24.go.kr/x")
    return Course(**{**base, **kw})


def skill(skill_id="powerbi", demand=3, name="Power BI"):
    return SkillRow(skill_id=skill_id, name=name, category="bi", owned=False,
                    evidence=(), demand=demand, companies=("쿠팡",))


class TestTargetSkills:
    def test_only_missing_skills_over_the_threshold(self):
        rows = [skill("powerbi", 3), skill("ga4", 1),
                SkillRow(skill_id="sql", name="SQL", category="query", owned=True,
                         evidence=("도구",), demand=8, companies=())]
        assert [s.skill_id for s in target_skills(rows, min_demand=2)] == ["powerbi"]

    def test_sorted_by_demand(self):
        rows = [skill("ga4", 2), skill("powerbi", 5)]
        assert [s.skill_id for s in target_skills(rows, min_demand=2)] == ["powerbi", "ga4"]


class TestSearchTerms:
    def test_direct_first_then_foundation(self):
        entry = {"training": {"direct": ["Power BI"], "foundation": ["데이터 시각화"]}}
        assert search_terms(entry) == (("Power BI", DIRECT), ("데이터 시각화", FOUNDATION))

    def test_no_training_entry(self):
        assert search_terms({}) == ()


class TestUsable:
    def test_metro_or_remote_only(self):
        assert usable(course(), today="2026-09-16", **FILTERS)
        assert not usable(course(address="부산 해운대구"), today="2026-09-16", **FILTERS)
        assert usable(course(address="부산 해운대구", target="실업자원격훈련"), today="2026-09-16", **FILTERS)

    def test_employee_only_courses_are_dropped(self):
        assert not usable(course(target="근로자원격훈련"), today="2026-09-16", **FILTERS)
        assert not usable(course(title="재직자_데이터 분석 과정"), today="2026-09-16", **FILTERS)

    def test_already_started_courses_are_dropped(self):
        assert not usable(course(start="2026-09-01"), today="2026-09-16", **FILTERS)
        assert usable(course(start="2026-09-16"), today="2026-09-16", **FILTERS)


class TestPick:
    def test_one_row_per_course_keeping_the_soonest_round(self):
        rounds = [course(degree="13", start="2026-10-10"), course(degree="14", start="2026-09-19")]
        picked = pick(rounds, per_skill=5, today="2026-09-16", **FILTERS)
        assert [(c.course_id, c.degree) for c in picked] == [("C1", "14")]

    def test_satisfaction_first_then_start_date(self):
        courses = [
            course(course_id="A", satisfaction=80.0, start="2026-09-20"),
            course(course_id="B", satisfaction=95.0, start="2026-11-01"),
            course(course_id="C", satisfaction=None, start="2026-09-18"),
        ]
        assert [c.course_id for c in pick(courses, per_skill=5, today="2026-09-16", **FILTERS)] == ["B", "A", "C"]

    def test_limit(self):
        courses = [course(course_id=f"C{i}", satisfaction=90 - i) for i in range(8)]
        assert len(pick(courses, per_skill=5, today="2026-09-16", **FILTERS)) == 5


class TestRecommend:
    def test_direct_courses_win_over_foundation_ones(self):
        entries = {"powerbi": {"id": "powerbi", "training": {"direct": ["Power BI"], "foundation": ["데이터 시각화"]}}}
        courses_by_term = {
            "Power BI": (course(course_id="D1", satisfaction=70.0),),
            "데이터 시각화": (course(course_id="F1", satisfaction=99.0),),
        }
        got = recommend([skill()], entries, courses_by_term, per_skill=5, today="2026-09-16", **FILTERS)
        assert [(r.course.course_id, r.kind) for r in got] == [("D1", DIRECT), ("F1", FOUNDATION)]
        assert got[0].skill_name == "Power BI"
        assert got[0].key == "powerbi:D1:1:I1"

    def test_a_course_found_twice_stays_once_with_the_stronger_kind(self):
        entries = {"powerbi": {"id": "powerbi", "training": {"direct": ["Power BI"], "foundation": ["데이터 시각화"]}}}
        both = course(course_id="X1")
        got = recommend([skill()], entries, {"Power BI": (both,), "데이터 시각화": (both,)},
                        per_skill=5, today="2026-09-16", **FILTERS)
        assert len(got) == 1 and got[0].kind == DIRECT

    def test_skills_without_terms_are_skipped(self):
        got = recommend([skill("jira")], {"jira": {"id": "jira"}}, {}, per_skill=5, today="2026-09-16", **FILTERS)
        assert got == ()

    def test_limit_counts_per_skill_not_per_term(self):
        entries = {"powerbi": {"id": "powerbi", "training": {"direct": ["Power BI"], "foundation": ["데이터 시각화"]}}}
        courses_by_term = {
            "Power BI": tuple(course(course_id=f"D{i}", satisfaction=90 - i) for i in range(4)),
            "데이터 시각화": tuple(course(course_id=f"F{i}", satisfaction=99 - i) for i in range(4)),
        }
        got = recommend([skill()], entries, courses_by_term, per_skill=5, today="2026-09-16", **FILTERS)
        assert len(got) == 5
        assert sum(1 for r in got if r.kind == DIRECT) == 4

    def test_foundation_only_skill_fills_all_slots_with_foundation(self):
        entries = {"powerbi": {"id": "powerbi", "training": {"foundation": ["데이터 시각화"]}}}
        courses_by_term = {
            "데이터 시각화": tuple(course(course_id=f"F{i}", satisfaction=99 - i) for i in range(6)),
        }
        got = recommend([skill()], entries, courses_by_term, per_skill=5, today="2026-09-16", **FILTERS)
        assert len(got) == 5
        assert all(r.kind == FOUNDATION for r in got)
        assert [r.course.course_id for r in got] == ["F0", "F1", "F2", "F3", "F4"]

    def test_a_later_round_listed_first_does_not_hide_the_earlier_one(self):
        """같은 과정의 회차 두 개가 나오면, API 순서와 무관하게 가장 빨리
        시작하는 회차만 남아야 한다(회차별 kind 가 같아도 courses 에서 빠지면 안 된다)."""
        entries = {"powerbi": {"id": "powerbi", "training": {"direct": ["Power BI"]}}}
        later_first = course(course_id="X1", degree="2", start="2026-11-01")
        earlier_second = course(course_id="X1", degree="1", start="2026-09-20")
        got = recommend([skill()], entries, {"Power BI": (later_first, earlier_second)},
                        per_skill=5, today="2026-09-16", **FILTERS)
        assert len(got) == 1
        assert got[0].course.degree == "1"
        assert got[0].course.start == "2026-09-20"

    def test_direct_pool_larger_than_quota_excludes_foundation_entirely(self):
        entries = {"powerbi": {"id": "powerbi", "training": {"direct": ["Power BI"], "foundation": ["데이터 시각화"]}}}
        courses_by_term = {
            "Power BI": tuple(course(course_id=f"D{i}", satisfaction=70 - i) for i in range(6)),
            "데이터 시각화": (
                course(course_id="F0", satisfaction=99.0),
                course(course_id="F1", satisfaction=98.0),
                course(course_id="F2", satisfaction=97.0),
            ),
        }
        got = recommend([skill()], entries, courses_by_term, per_skill=5, today="2026-09-16", **FILTERS)
        assert len(got) == 5
        assert all(r.kind == DIRECT for r in got)
        assert all(r.course.course_id.startswith("D") for r in got)
