"""스킬 DB 에 올릴 행.

역량 갭 페이지와 공고 DB 의 '부족 스킬'이 같은 말을 해야 한다. 보유 판정이
갈라지면 한쪽은 부족, 한쪽은 보유라고 말하게 되고 둘 다 믿을 수 없다.
"""
from analyze.gap import AnalyzedPosting, Gap
from analyze.skill_board import TOOL_EVIDENCE, build_skill_rows
from collect.schema import Posting
from extract.experience import ExperienceBook

ENTRIES = [
    {"id": "sql", "name": "SQL", "category": "query"},
    {"id": "powerbi", "name": "Power BI", "category": "bi"},
    {"id": "ab_test", "name": "A/B 테스트", "category": "method"},
    {"id": "retention", "name": "리텐션 분석", "category": "method"},
    {"id": "spark", "name": "Apache Spark", "category": "engine"},
]

BOOK = ExperienceBook.from_config({"experiences": [{
    "id": "funnel_example", "kind": "project", "name": "예시 퍼널 분석",
    "pitch": "퍼널에서 이탈 구간을 찾았다", "proves": ["sql", "retention"], "signals": ["퍼널"],
}]})


def analyzed(source_id, company, matched=(), missing=()):
    posting = Posting(
        source="greenhouse", source_id=source_id, company=company, title="데이터 분석가",
        location="서울", url=f"https://example.com/{source_id}", body_html="<p>본문</p>",
        posted_at=None, fetched_at="2026-09-15T00:00:00+09:00",
    )
    return AnalyzedPosting(posting=posting, company=None, gap=Gap(matched=matched, missing=missing))


def rows_by_id(rows):
    return {r.skill_id: r for r in rows}


def build(postings):
    return build_skill_rows(postings, skill_entries=ENTRIES, tool_ids={"sql"}, experiences=BOOK)


def test_owned_means_tool_or_proven_by_an_experience():
    rows = rows_by_id(build([]))
    assert rows["sql"].owned and rows["retention"].owned
    assert rows["sql"].evidence == (TOOL_EVIDENCE, "예시 퍼널 분석")
    assert rows["retention"].evidence == ("예시 퍼널 분석",)


def test_demand_counts_postings_that_require_the_skill_either_way():
    """보유 스킬도 요구 공고 수를 센다. '나의 스킬'에서 쓸모를 보여 주는 숫자다."""
    rows = rows_by_id(build([
        analyzed("1", "쿠팡", matched=("sql",), missing=("powerbi",)),
        analyzed("2", "컬리", matched=("sql",), missing=("powerbi", "ab_test")),
    ]))
    assert rows["sql"].demand == 2 and rows["sql"].owned
    assert rows["powerbi"].demand == 2 and not rows["powerbi"].owned
    assert rows["powerbi"].companies == tuple(sorted(("쿠팡", "컬리")))
    assert rows["ab_test"].evidence == ()


def test_skills_neither_owned_nor_required_are_left_out():
    assert "spark" not in rows_by_id(build([analyzed("1", "쿠팡", missing=("powerbi",))]))


def test_name_and_category_come_from_the_dictionary():
    row = rows_by_id(build([analyzed("1", "쿠팡", missing=("powerbi",))]))["powerbi"]
    assert (row.name, row.category) == ("Power BI", "bi")


def test_unknown_id_falls_back_to_the_id():
    row = rows_by_id(build([analyzed("1", "쿠팡", missing=("mystery",))]))["mystery"]
    assert (row.name, row.category) == ("mystery", "")


def test_missing_skills_come_first_by_demand():
    rows = build([
        analyzed("1", "쿠팡", missing=("powerbi", "ab_test")),
        analyzed("2", "컬리", missing=("powerbi",)),
    ])
    assert [r.skill_id for r in rows][:2] == ["powerbi", "ab_test"]
    assert all(not r.owned for r in rows[:2]) and all(r.owned for r in rows[2:])
