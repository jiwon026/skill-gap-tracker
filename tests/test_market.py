"""누적 스냅샷 집계.

README 에 내는 숫자라, 분모를 잘못 잡으면 공개된 글이 조용히 틀린 말을 한다.
"""
import json

import pytest

from analysis.market import Market, ratios, summarize, unique_postings
from collect.schema import Posting


def posting(**kw):
    base = dict(
        source="board",
        source_id="1",
        title="데이터 분석가",
        url="https://example.com/1",
        company="예시회사",
        location="서울",
        body_html="<p>SQL 과 Python 을 씁니다.</p>",
        experience_min=0,
        posted_at="2026-09-01T00:00:00+09:00",
        fetched_at="2026-09-01T10:00:00+09:00",
    )
    base.update(kw)
    return Posting(**base)


class TestRatios:
    def test_percentage_uses_the_given_denominator(self):
        assert ratios([("SQL", 3)], 4) == (("SQL", 3, 75.0),)

    def test_zero_denominator_does_not_divide(self):
        """수집이 통째로 실패한 날에도 집계가 터지지 않아야 한다."""
        assert ratios([("SQL", 0)], 0) == (("SQL", 0, 0.0),)


class TestSummarize:
    def test_counts_unique_companies_not_postings(self):
        rows = [posting(source_id="1"), posting(source_id="2"), posting(source_id="3", company="다른회사")]
        market = summarize(rows, days=1)
        assert (market.total, market.companies) == (3, 2)

    def test_non_data_roles_are_left_out_of_every_count(self):
        rows = [posting(source_id="1"), posting(source_id="2", title="백엔드 개발자")]
        market = summarize(rows, days=1)
        assert market.total == 2 and market.data_roles == 1

    def test_postings_without_a_body_are_not_in_the_denominator(self):
        """본문을 주지 않는 소스를 분모에 넣으면 '요구가 없어서 0개' 와
        '읽을 본문이 없어서 0개' 가 뒤섞여 비율이 낮게 나온다."""
        rows = [posting(source_id="1"), posting(source_id="2", body_html="")]
        market = summarize(rows, days=1)
        assert market.data_roles == 2 and market.scored == 1
        assert market.ratios[0][2] == 100.0

    def test_demand_is_ordered_and_capped(self):
        rows = [posting(source_id="1"), posting(source_id="2", body_html="<p>SQL 만 씁니다.</p>")]
        market = summarize(rows, days=1, top_n=1)
        assert market.demand == (("SQL", 2),)

    def test_entry_level_is_counted_inside_data_roles(self):
        rows = [posting(source_id="1", experience_min=0), posting(source_id="2", experience_min=5)]
        market = summarize(rows, days=1)
        assert market.data_roles == 2 and market.entry_level == 1


class TestUniquePostings:
    def test_the_same_posting_on_two_days_counts_once_and_keeps_the_later_one(self, tmp_path):
        """공고는 수정된다. 마지막 스냅샷의 내용을 써야 지금 요구를 센다."""
        (tmp_path / "2026-09-01.jsonl").write_text(
            json.dumps(json.loads(posting(title="옛 제목").to_json()), ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        (tmp_path / "2026-09-02.jsonl").write_text(
            json.dumps(json.loads(posting(title="새 제목").to_json()), ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        rows, days = unique_postings(tmp_path)
        assert days == 2
        assert [p.title for p in rows] == ["새 제목"]

    def test_an_empty_directory_gives_nothing_rather_than_failing(self, tmp_path):
        assert unique_postings(tmp_path) == ([], 0)
