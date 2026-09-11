"""우아한형제들 수집기.

이 소스의 고유한 형태는 두 가지다. 본문이 목록에 없어 상세를 한 번 더
불러야 한다는 것, 그리고 상세 조회 키가 `recruitSeq`(숫자)가 아니라
`recruitNumber`(R2609008)라는 것. 숫자를 넣으면 조용히 code 9002가 온다.

상세 호출은 공고 수만큼 발생하므로, 한 건이 실패해도 나머지가 살아야 한다.
"""
import json
from pathlib import Path

import pytest

from collect.woowahan import (
    DETAIL_URL,
    LIST_URL,
    BoardSpec,
    fetch_payload,
    parse_board,
)

FIXTURES = Path(__file__).parent / "fixtures"
SPEC = BoardSpec(company="우아한형제들", domain="ecommerce")
FETCHED = "2026-09-09T00:00:00+09:00"


@pytest.fixture
def listing():
    return json.loads((FIXTURES / "woowahan_list.json").read_text(encoding="utf-8"))


@pytest.fixture
def detail():
    return json.loads((FIXTURES / "woowahan_detail.json").read_text(encoding="utf-8"))


@pytest.fixture
def payload(listing, detail):
    """어댑터가 받는 형태 — 목록과, recruitNumber로 색인된 상세들."""
    return {"listing": listing, "details": {"R2609008": detail}}


def _parse(payload, **kw):
    return parse_board(payload, SPEC, fetched_at=FETCHED, **kw)


def test_maps_fields_onto_posting(payload):
    posting = next(p for p in _parse(payload).postings if p.source_id == "R2609008")
    assert posting.source == "woowahan"
    assert posting.key == "woowahan:R2609008"
    assert posting.title == "데이터엔지니어링(데이터서비스 AI)"
    assert posting.company == "우아한형제들"
    assert posting.fetched_at == FETCHED


def test_source_id_is_recruit_number_not_seq(payload):
    """상세 조회가 recruitNumber로만 되므로, 식별자도 그것으로 통일한다."""
    ids = {p.source_id for p in _parse(payload).postings}
    assert "R2609008" in ids
    assert "25761" not in ids


def test_url_points_at_the_public_posting_page(payload):
    posting = next(p for p in _parse(payload).postings if p.source_id == "R2609008")
    assert posting.url == "https://career.woowahan.com/recruitment/R2609008/detail"


def test_body_comes_from_the_detail_response(payload):
    """목록의 recruitContents는 항상 null이다. 본문은 상세에서만 온다."""
    posting = next(p for p in _parse(payload).postings if p.source_id == "R2609008")
    assert len(posting.body_html) > 1000
    assert "<" in posting.body_html


def test_posting_without_detail_still_collected_with_empty_body(payload):
    """상세 호출이 실패해도 제목·URL은 살아 있다. 버리는 것보다 낫다."""
    payload["details"] = {}
    postings = _parse(payload).postings
    assert len(postings) == 3
    assert all(p.body_html == "" for p in postings)


def test_location_defaults_to_korea(payload):
    """응답에 근무지 필드가 없다. 전 직군이 국내라 상수로 채운다."""
    assert all(p.location == "대한민국" for p in _parse(payload).postings)


def test_posted_at_is_normalized_to_iso(payload):
    """API는 '2026-09-05 10:20:30' 형태로 준다. 소스 간 비교가 되려면 ISO여야 한다."""
    posting = next(p for p in _parse(payload).postings if p.source_id == "R2609008")
    assert posting.posted_at is not None
    assert "T" in posting.posted_at
    assert posting.posted_at.endswith("+09:00")


def test_one_bad_posting_does_not_abort_the_board(payload):
    payload["listing"]["data"]["list"][0]["recruitName"] = "  "
    result = _parse(payload)
    assert len(result.postings) == 2
    assert len(result.skipped) == 1
    assert "title" in result.skipped[0].reason


def test_hidden_postings_are_excluded(payload):
    payload["listing"]["data"]["list"][0]["isHidden"] = True
    assert len(_parse(payload).postings) == 2


def _cache_payload(rows, details):
    return {"listing": {"data": {"list": rows}}, "details": details}


def test_fetch_uses_cache_and_skips_network(tmp_path):
    cache = tmp_path / "woowahan.json"
    cached = _cache_payload(
        [{"recruitNumber": "R1", "recruitName": "공고1"}],
        {"R1": {"data": {"recruitContents": "<p>본문</p>"}}},
    )
    cache.write_text(json.dumps(cached), encoding="utf-8")

    def explode(url, timeout=0):  # pragma: no cover - 호출되면 실패다
        raise AssertionError(f"캐시가 있는데 네트워크를 탔다: {url}")

    assert fetch_payload(SPEC, cache, opener=explode) == cached


def test_cache_with_too_few_bodies_is_refetched(tmp_path):
    """상세가 대부분 실패한 결과가 하루치 캐시로 굳으면 복구 수단이 없다."""
    cache = tmp_path / "woowahan.json"
    cache.write_text(
        json.dumps(_cache_payload([{"recruitNumber": f"R{i}"} for i in range(10)], {})),
        encoding="utf-8",
    )
    calls = []

    def fake(url, timeout=0):
        calls.append(url)
        body = ({"data": {"totalPageNumber": 1, "list": [{"recruitNumber": "R1"}]}}
                if "/w1/recruits?" in url
                else {"data": {"recruitContents": "<p>본문</p>"}})
        return type("R", (), {"read": lambda self: json.dumps(body).encode()})()

    payload = fetch_payload(SPEC, cache, opener=fake)
    assert calls, "확보율이 낮은 캐시인데 네트워크를 타지 않았다"
    assert payload["details"]


def test_partial_detail_failure_is_not_cached(tmp_path):
    """다음 실행이 반쪽짜리 데이터를 재사용하지 않도록 캐시를 만들지 않는다."""
    cache = tmp_path / "woowahan.json"

    def fake(url, timeout=0):
        if "/w1/recruits?" in url:
            rows = [{"recruitNumber": f"R{i}"} for i in range(10)]
            body = {"data": {"totalPageNumber": 1, "list": rows}}
            return type("R", (), {"read": lambda self: json.dumps(body).encode()})()
        raise OSError("상세 조회 실패")

    payload = fetch_payload(SPEC, cache, opener=fake)
    assert not cache.exists()
    assert len(payload["listing"]["data"]["list"]) == 10


def test_duplicate_recruit_numbers_across_pages_are_dropped(tmp_path):
    """요청은 0-기반인데 응답 pageNumber는 1이다. 규약이 어긋나도 안전해야 한다."""
    cache = tmp_path / "woowahan.json"

    def fake(url, timeout=0):
        if "/w1/recruits?" in url:
            # 어느 페이지를 물어도 같은 목록을 주는 1-기반 API를 흉내낸다.
            body = {"data": {"totalPageNumber": 3,
                             "list": [{"recruitNumber": "R1"}, {"recruitNumber": "R2"}]}}
        else:
            body = {"data": {"recruitContents": "<p>본문</p>"}}
        return type("R", (), {"read": lambda self: json.dumps(body).encode()})()

    payload = fetch_payload(SPEC, cache, opener=fake)
    numbers = [r["recruitNumber"] for r in payload["listing"]["data"]["list"]]
    assert numbers == ["R1", "R2"]


def test_fetch_walks_pages_then_details(tmp_path):
    """목록을 페이지 끝까지 훑고, 각 공고의 상세를 이어서 부른다."""
    cache = tmp_path / "woowahan.json"
    calls = []

    def fake(url, timeout=0):
        calls.append(url)
        if "/w1/recruits?" in url:
            page = int(url.split("page=")[1].split("&")[0])
            body = {
                "data": {
                    "totalPageNumber": 2,
                    "list": [{"recruitNumber": f"R{page}", "recruitName": f"공고{page}"}],
                }
            }
        else:
            body = {"data": {"recruitContents": "<p>본문</p>"}}
        return type("R", (), {"read": lambda self: json.dumps(body).encode()})()

    payload = fetch_payload(SPEC, cache, opener=fake)

    assert [c for c in calls if "/w1/recruits?" in c] == [
        LIST_URL.format(page=0),
        LIST_URL.format(page=1),
    ]
    assert DETAIL_URL.format(number="R0") in calls
    assert DETAIL_URL.format(number="R1") in calls
    assert set(payload["details"]) == {"R0", "R1"}
    assert cache.exists()


def test_fetch_survives_when_a_row_has_no_recruit_number(tmp_path):
    cache = tmp_path / "woowahan.json"

    def fake(url, timeout=0):
        if "/w1/recruits?" in url:
            body = {"data": {"totalPageNumber": 1,
                             "list": [{"recruitName": "번호 없는 공고"},
                                      {"recruitNumber": "R1", "recruitName": "공고1"}]}}
        else:
            body = {"data": {"recruitContents": "<p>본문</p>"}}
        return type("R", (), {"read": lambda self: json.dumps(body).encode()})()

    payload = fetch_payload(SPEC, cache, opener=fake)
    assert set(payload["details"]) == {"R1"}


def test_fetch_survives_a_failing_detail_call(tmp_path):
    """상세 하나가 죽어도 목록 전체를 버리지 않는다."""
    cache = tmp_path / "woowahan.json"

    def fake(url, timeout=0):
        if "/w1/recruits?" in url:
            body = {
                "data": {
                    "totalPageNumber": 1,
                    "list": [
                        {"recruitNumber": "R1", "recruitName": "공고1"},
                        {"recruitNumber": "R2", "recruitName": "공고2"},
                    ],
                }
            }
            return type("R", (), {"read": lambda self: json.dumps(body).encode()})()
        if url.endswith("R1"):
            raise OSError("상세 조회 실패")
        body = {"data": {"recruitContents": "<p>본문</p>"}}
        return type("R", (), {"read": lambda self: json.dumps(body).encode()})()

    payload = fetch_payload(SPEC, cache, opener=fake)
    assert set(payload["details"]) == {"R2"}
    assert len(payload["listing"]["data"]["list"]) == 2


class TestDeadline:
    """우아한형제들은 상시채용을 isUnlimitedEndDate 와 마감일 9999-12-31 로 표시한다.
    9999 를 날짜로 옮기면 Notion 에 9999년 마감으로 뜬다."""

    def _row(self, payload):
        return payload["listing"]["data"]["list"][0]

    def test_year_9999_is_rolling(self, payload):
        row = self._row(payload)
        row["isUnlimitedEndDate"], row["recruitEndDate"] = None, "9999-12-31 00:00:00"
        p = _parse(payload).postings[0]
        assert p.deadline is None and p.deadline_note == "상시채용"

    def test_unlimited_flag_is_rolling(self, payload):
        row = self._row(payload)
        row["isUnlimitedEndDate"], row["recruitEndDate"] = True, "2026-10-15 23:59:59"
        assert _parse(payload).postings[0].deadline_note == "상시채용"

    def test_real_end_date_becomes_the_deadline(self, payload):
        row = self._row(payload)
        row["isUnlimitedEndDate"], row["recruitEndDate"] = False, "2026-10-15 23:59:59"
        p = _parse(payload).postings[0]
        assert p.deadline == "2026-10-15" and p.deadline_note is None
