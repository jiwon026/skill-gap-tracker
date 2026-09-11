"""회사 정보 캐시.

회사 규모는 공고와 달리 거의 바뀌지 않는다. 매일 다시 물으면 소스 서버에
쓸데없는 요청이 쌓이므로, 한 번 받은 정보는 30일 동안 다시 묻지 않는다.
실패는 캐시하지 않는다 — 하루 장애가 한 달 동안 '기타'로 굳으면 안 된다.
"""
import json
from datetime import date, timedelta

import pytest

from collect.company_cache import TTL_DAYS, lookup
from collect.schema import CompanyProfile

TODAY = date(2026, 9, 11)
SME = CompanyProfile(size_class="중소", headcount_max=232, founded_year=2005)


class Recorder:
    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def __call__(self, company_id):
        self.calls.append(company_id)
        answer = self.answers.get(company_id)
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.fixture
def cache(tmp_path):
    return tmp_path / "companies" / "board.json"


def test_fetches_unknown_companies_and_returns_their_profiles(cache):
    fetch = Recorder({"C1": SME})
    assert lookup(["C1"], fetch, cache, today=TODAY) == {"C1": SME}
    assert fetch.calls == ["C1"]


def test_cached_profile_is_not_fetched_again(cache):
    lookup(["C1"], Recorder({"C1": SME}), cache, today=TODAY)
    again = Recorder({})
    assert lookup(["C1"], again, cache, today=TODAY + timedelta(days=TTL_DAYS - 1)) == {"C1": SME}
    assert again.calls == []


def test_stale_profile_is_fetched_again(cache):
    lookup(["C1"], Recorder({"C1": SME}), cache, today=TODAY)
    newer = CompanyProfile(size_class="중견", headcount_max=400)
    again = Recorder({"C1": newer})
    assert lookup(["C1"], again, cache, today=TODAY + timedelta(days=TTL_DAYS)) == {"C1": newer}
    assert again.calls == ["C1"]


def test_failures_are_skipped_and_not_cached(cache):
    """한 회사가 실패해도 나머지는 계속된다. 실패는 다음 실행에서 다시 묻는다."""
    fetch = Recorder({"C1": OSError("timeout"), "C2": SME, "C3": None})
    assert lookup(["C1", "C2", "C3"], fetch, cache, today=TODAY) == {"C2": SME}

    again = Recorder({"C1": SME, "C3": SME})
    lookup(["C1", "C2", "C3"], again, cache, today=TODAY)
    assert again.calls == ["C1", "C3"]


def test_each_company_is_asked_once(cache):
    fetch = Recorder({"C1": SME})
    lookup(["C1", "C1", "C1"], fetch, cache, today=TODAY)
    assert fetch.calls == ["C1"]


def test_broken_cache_file_is_treated_as_empty(cache):
    cache.parent.mkdir(parents=True)
    cache.write_text("{not json", encoding="utf-8")
    assert lookup(["C1"], Recorder({"C1": SME}), cache, today=TODAY) == {"C1": SME}
    assert json.loads(cache.read_text(encoding="utf-8"))["C1"]["fetched_on"] == TODAY.isoformat()


def test_nothing_to_look_up_touches_nothing(cache):
    assert lookup([], Recorder({}), cache, today=TODAY) == {}
    assert not cache.exists()
