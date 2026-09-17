"""고용24 훈련과정 수집기.

목록 API 의 특징 두 가지가 여기서 흡수된다. 같은 과정이 회차마다 한 줄씩
나오고(trprId 가 같다), 한 번에 100회차까지만 준다. 그리고 공고 수집기와
같은 계약을 지킨다 - 캐시 우선, 파싱은 순수 함수, 한 줄이 깨져도 나머지는 산다.
"""
import json
from pathlib import Path

import pytest

from collect.training import (
    Course,
    WEEKEND_LABELS,
    fetch_own_fee,
    parse_courses,
    prune_cache,
    search_courses,
    with_own_fee,
)

FIXTURE = Path(__file__).parent / "fixtures" / "work24_powerbi.json"


@pytest.fixture
def payload():
    with FIXTURE.open(encoding="utf-8") as handle:
        return json.load(handle)


class _Response:
    def __init__(self, payload):
        self._raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def read(self):
        return self._raw


def opener_for(pages):
    """페이지 순서대로 응답을 준다. 요청 URL 도 기록한다."""
    calls = []

    def opener(request, timeout=None):
        calls.append(request.full_url)
        return _Response(pages[min(len(calls) - 1, len(pages) - 1)])

    return opener, calls


class TestParse:
    def test_every_row_becomes_a_course(self, payload):
        courses = parse_courses(payload)
        assert len(courses) == len(payload["srchList"])
        first = courses[0]
        assert isinstance(first, Course)
        assert first.title and first.institution and first.address.startswith("서울")
        assert first.course_id.startswith("AIG") or first.course_id.startswith("AVA")
        assert first.url.startswith("https://")

    def test_numbers_are_numbers_and_blanks_are_none(self, payload):
        rows = [dict(payload["srchList"][0], stdgScor="", yardMan="", regCourseMan="7", realMan="272610")]
        course = parse_courses({"srchList": rows})[0]
        assert course.satisfaction is None
        assert course.capacity is None
        assert course.applicants == 7
        assert course.total_fee == 272610

    def test_a_broken_row_does_not_kill_the_rest(self, payload):
        rows = [{"trprId": "x"}, *payload["srchList"]]
        assert len(parse_courses({"srchList": rows})) == len(payload["srchList"])

    def test_empty_payload(self):
        assert parse_courses({}) == ()

    def test_weekend_labels_cover_the_codes(self):
        assert WEEKEND_LABELS["1"] == "주말"
        assert WEEKEND_LABELS["3"] == "주중"
        assert set(WEEKEND_LABELS) >= {"1", "2", "3", "9"}


class TestSearch:
    def test_reads_the_requested_pages_and_caches(self, payload, tmp_path):
        opener, calls = opener_for([payload, {"srchList": []}])
        courses = search_courses(
            "KEY", "Power BI", start_date="20260916", end_date="20261215", pages=2,
            cache_dir=tmp_path, today="2026-09-16", cache_days=7, opener=opener, sleep=lambda _: None,
        )
        assert len(courses) == len(payload["srchList"])
        assert len(calls) == 2
        assert "pageNum=1" in calls[0] and "pageNum=2" in calls[1]
        assert "srchTraProcessNm=" in calls[0] and "sortCol=5" in calls[0]
        assert "KEY" in calls[0]

        opener2, calls2 = opener_for([{"srchList": []}])
        again = search_courses(
            "KEY", "Power BI", start_date="20260916", end_date="20261215", pages=2,
            cache_dir=tmp_path, today="2026-09-16", cache_days=7, opener=opener2, sleep=lambda _: None,
        )
        assert calls2 == []
        assert [c.key for c in again] == [c.key for c in courses]

    def test_a_stale_cache_is_ignored(self, payload, tmp_path):
        opener, _ = opener_for([payload, {"srchList": []}])
        search_courses("KEY", "Power BI", start_date="20260916", end_date="20261215", pages=1,
                       cache_dir=tmp_path, today="2026-09-01", cache_days=7, opener=opener, sleep=lambda _: None)
        opener2, calls2 = opener_for([payload])
        search_courses("KEY", "Power BI", start_date="20260916", end_date="20261215", pages=1,
                       cache_dir=tmp_path, today="2026-09-16", cache_days=7, opener=opener2, sleep=lambda _: None)
        assert calls2, "8일 지난 캐시는 다시 받아야 한다"

    def test_stops_early_when_a_page_is_empty(self, payload, tmp_path):
        opener, calls = opener_for([payload, {"srchList": []}, payload])
        search_courses("KEY", "Power BI", start_date="20260916", end_date="20261215", pages=3,
                       cache_dir=tmp_path, today="2026-09-16", cache_days=7, opener=opener, sleep=lambda _: None)
        assert len(calls) == 2

    def test_cache_key_includes_window_and_pages(self, payload, tmp_path):
        """검색어가 같아도 기간이나 페이지 수가 다르면 캐시를 나눠 쓴다."""
        opener, calls = opener_for([payload, {"srchList": []}])
        search_courses(
            "KEY", "Power BI", start_date="20260916", end_date="20261215", pages=2,
            cache_dir=tmp_path, today="2026-09-16", cache_days=7, opener=opener, sleep=lambda _: None,
        )

        opener2, calls2 = opener_for([payload, {"srchList": []}])
        search_courses(
            "KEY", "Power BI", start_date="20260916", end_date="20270315", pages=2,
            cache_dir=tmp_path, today="2026-09-16", cache_days=7, opener=opener2, sleep=lambda _: None,
        )
        assert calls2, "종료일이 다른 요청은 이전 캐시를 쓰면 안 된다"

        opener3, calls3 = opener_for([payload])
        search_courses(
            "KEY", "Power BI", start_date="20260916", end_date="20261215", pages=1,
            cache_dir=tmp_path, today="2026-09-16", cache_days=7, opener=opener3, sleep=lambda _: None,
        )
        assert calls3, "페이지 수가 다른 요청은 이전 캐시를 쓰면 안 된다"

        opener4, calls4 = opener_for([{"srchList": []}])
        search_courses(
            "KEY", "Power BI", start_date="20260916", end_date="20261215", pages=2,
            cache_dir=tmp_path, today="2026-09-16", cache_days=7, opener=opener4, sleep=lambda _: None,
        )
        assert calls4 == [], "같은 조합의 반복 호출은 캐시를 써야 한다"

    def test_same_window_length_reuses_the_cache_even_if_dates_differ(self, payload, tmp_path):
        """창은 절대 날짜가 아니라 길이로 캐시를 나눈다. 오늘이 하루 지나면
        start_date 와 end_date 가 둘 다 하루씩 밀리므로, 길이가 같은 요청까지
        절대 날짜로 나누면 list_cache_days 가 뜻을 잃는다."""
        opener, calls = opener_for([payload, {"srchList": []}])
        search_courses(
            "KEY", "Power BI", start_date="20260916", end_date="20261215", pages=2,
            cache_dir=tmp_path, today="2026-09-16", cache_days=7, opener=opener, sleep=lambda _: None,
        )
        opener2, calls2 = opener_for([{"srchList": []}])
        search_courses(
            "KEY", "Power BI", start_date="20260917", end_date="20261216", pages=2,
            cache_dir=tmp_path, today="2026-09-16", cache_days=7, opener=opener2, sleep=lambda _: None,
        )
        assert calls2 == [], "기간 길이와 페이지 수가 같으면 종료일이 달라도 캐시를 써야 한다"

    def test_cache_from_an_earlier_today_is_reused_the_next_day(self, payload, tmp_path):
        """어제(cache_days 안) 받아 둔 캐시를 오늘 호출도 써야 한다. 창은 항상
        오늘부터라, 절대 날짜로 캐시를 나누면 매일 새로 받게 된다."""
        opener, calls = opener_for([payload, {"srchList": []}])
        search_courses(
            "KEY", "Power BI", start_date="20260915", end_date="20261214", pages=2,
            cache_dir=tmp_path, today="2026-09-15", cache_days=7, opener=opener, sleep=lambda _: None,
        )
        opener2, calls2 = opener_for([{"srchList": []}])
        search_courses(
            "KEY", "Power BI", start_date="20260916", end_date="20261215", pages=2,
            cache_dir=tmp_path, today="2026-09-16", cache_days=7, opener=opener2, sleep=lambda _: None,
        )
        assert calls2 == [], "하루 지나도 창 길이가 같으면 어제 만든 캐시를 써야 한다"

    def test_empty_result_is_not_cached(self, tmp_path):
        """빈 200 을 캐시에 남기면, 실제로는 결과가 있는 검색어도
        list_cache_days 동안 계속 빈 목록만 돌려주게 된다."""
        opener, _ = opener_for([{"srchList": []}])
        search_courses("KEY", "Power BI", start_date="20260916", end_date="20261215", pages=1,
                       cache_dir=tmp_path, today="2026-09-16", cache_days=7, opener=opener, sleep=lambda _: None)
        assert list(tmp_path.glob("*.json")) == []

        opener2, calls2 = opener_for([{"srchList": []}])
        search_courses("KEY", "Power BI", start_date="20260916", end_date="20261215", pages=1,
                       cache_dir=tmp_path, today="2026-09-16", cache_days=7, opener=opener2, sleep=lambda _: None)
        assert calls2, "빈 결과는 캐시되지 않아 다시 받아야 한다"


class TestOwnFee:
    def test_reads_the_trainee_share(self, payload, tmp_path):
        course = parse_courses(payload)[0]
        detail = {"inst_detail_info": {"tgcrGnrlTrneOwepAllt": "43000"}}
        opener, calls = opener_for([detail])
        assert fetch_own_fee("KEY", course, cache_dir=tmp_path, today="2026-09-16",
                             cache_days=30, opener=opener) == 43000
        assert "srchTrprId=" in calls[0] and "srchTorgId=" in calls[0]

    def test_missing_value_is_none_and_does_not_raise(self, payload, tmp_path):
        course = parse_courses(payload)[0]
        opener, _ = opener_for([{}])
        assert fetch_own_fee("KEY", course, cache_dir=tmp_path, today="2026-09-16",
                             cache_days=30, opener=opener) is None

    def test_second_call_uses_the_cache(self, payload, tmp_path):
        course = parse_courses(payload)[0]
        detail = {"inst_detail_info": {"tgcrGnrlTrneOwepAllt": "43000"}}
        opener, calls = opener_for([detail])
        first = fetch_own_fee("KEY", course, cache_dir=tmp_path, today="2026-09-16",
                               cache_days=30, opener=opener)

        opener2, calls2 = opener_for([detail])
        second = fetch_own_fee("KEY", course, cache_dir=tmp_path, today="2026-09-16",
                                cache_days=30, opener=opener2)
        assert second == first == 43000
        assert calls2 == []


class TestWithOwnFee:
    def test_returns_a_new_course_and_leaves_the_original_unchanged(self, payload):
        course = parse_courses(payload)[0]
        updated = with_own_fee(course, 43000)
        assert updated.own_fee == 43000
        assert course.own_fee is None
        assert updated is not course


class TestPruneCache:
    """캐시는 스스로 줄어들어야 한다.

    _fresh_cache 는 keep_days 안의 파일만 본다. 그보다 오래된 파일은 영영
    안 읽힌다. 같은 이름으로 새 파일이 쓰이면 묻히기라도 하지만, 검색어가
    skills.yaml 에서 빠지거나 과정이 추천에서 밀리면 그 이름은 다시 안 나온다.
    그래서 날짜로 쓸어낸다.
    """

    def _touch(self, folder, *names):
        for name in names:
            (folder / name).write_text("{}", encoding="utf-8")

    def test_files_older_than_the_window_are_removed(self, tmp_path):
        self._touch(tmp_path, "Power_BI__90d_p3_2026-08-01.json",
                    "Power_BI__90d_p3_2026-09-16.json")
        removed = prune_cache(tmp_path, "2026-09-17", keep_days=30)
        assert removed == 1
        assert [p.name for p in tmp_path.iterdir()] == ["Power_BI__90d_p3_2026-09-16.json"]

    def test_the_last_day_of_the_window_is_kept(self, tmp_path):
        """keep_days=30 이면 29일 전까지는 _fresh_cache 가 아직 읽는다."""
        self._touch(tmp_path, "AIG1_4_500_2026-08-19.json")
        assert prune_cache(tmp_path, "2026-09-17", keep_days=30) == 0
        self._touch(tmp_path, "AIG1_4_500_2026-08-18.json")
        assert prune_cache(tmp_path, "2026-09-17", keep_days=30) == 1

    def test_dates_in_the_future_are_kept(self, tmp_path):
        """시계가 어긋난 날 쓴 파일을 지우면 그날 받은 걸 또 받는다."""
        self._touch(tmp_path, "term__90d_p3_2026-09-20.json")
        assert prune_cache(tmp_path, "2026-09-17", keep_days=7) == 0

    def test_files_without_a_date_are_left_alone(self, tmp_path):
        """캐시가 아닌 파일이 섞여 있을 수 있다. 모르는 건 안 건드린다."""
        self._touch(tmp_path, "notes.txt", "term__90d_p3_bad.json")
        assert prune_cache(tmp_path, "2026-09-17", keep_days=7) == 0
        assert len(list(tmp_path.iterdir())) == 2

    def test_a_missing_directory_is_not_an_error(self, tmp_path):
        assert prune_cache(tmp_path / "없음", "2026-09-17", keep_days=7) == 0

