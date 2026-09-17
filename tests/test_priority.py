"""지원 우선순위.

Notion에서 무엇부터 지원할지 가르는 열이다. 규칙은 한 문장으로 설명돼야
한다 — 사용자가 등급을 보고 "왜 이게 높음이지?"를 되짚을 수 있어야 쓴다.

    어필할 프로젝트 수로 정하고, 타겟 회사가 아니면 한 단계 내린다.
"""
import re

from analyze.gap import AnalyzedPosting, Gap
from analyze.priority import PRIORITIES, assign_priority, priority_rank
from collect.schema import Posting
from extract.companies import Company
from extract.experience import Match

TARGET = Company(name="쿠팡", tier="대기업", segment="종합몰", matcher=re.compile("쿠팡"))


def _match(kind, n):
    return Match(id=f"{kind}{n}", name=f"{kind}{n}", kind=kind, pitch="", signals=())


def analyzed(projects=0, activities=0, *, company=TARGET):
    experiences = tuple(_match("project", i) for i in range(projects))
    experiences += tuple(_match("activity", i) for i in range(activities))
    posting = Posting(
        source="woowahan", source_id="1", company="쿠팡", title="데이터 분석가",
        location="Seoul", url="https://example.com/1", body_html="",
        posted_at=None, fetched_at="2026-09-10T00:00:00+09:00",
    )
    return AnalyzedPosting(
        posting=posting, company=company,
        gap=Gap(matched=("sql",), missing=()), experiences=experiences,
    )


class TestBaseLevel:
    def test_two_projects_at_a_target_company_is_high(self):
        assert assign_priority(analyzed(projects=2)) == "높음"

    def test_more_than_two_projects_is_still_high(self):
        assert assign_priority(analyzed(projects=4)) == "높음"

    def test_one_project_is_medium(self):
        assert assign_priority(analyzed(projects=1)) == "중간"

    def test_no_project_is_low(self):
        assert assign_priority(analyzed(projects=0)) == "낮음"

    def test_activities_do_not_count_as_projects(self):
        """활동 경험은 거의 모든 공고에 걸린다. 세면 전부 높음이 된다."""
        assert assign_priority(analyzed(projects=0, activities=3)) == "낮음"


class TestStepDown:
    def test_non_target_company_steps_down_one_level(self):
        assert assign_priority(analyzed(projects=2, company=None)) == "중간"

    def test_never_falls_below_low(self):
        assert assign_priority(analyzed(projects=0, company=None)) == "낮음"


class TestOrdering:
    def test_labels_run_from_high_to_low(self):
        """Notion select 는 옵션 순서대로 정렬된다. 이 순서가 곧 정렬 순서다."""
        assert PRIORITIES == ("높음", "중간", "낮음")

    def test_rank_puts_high_first(self):
        assert priority_rank("높음") < priority_rank("중간") < priority_rank("낮음")

    def test_every_assigned_label_has_a_rank(self):
        for projects in range(4):
            label = assign_priority(analyzed(projects=projects))
            assert label in PRIORITIES
            priority_rank(label)
