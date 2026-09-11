"""Greenhouse 수집기.

이 계층이 지켜야 할 것은 두 가지다. 소스 고유의 형태(HTML 엔티티 이중
인코딩, 테스트용 더미 공고)를 밖으로 흘리지 않는 것, 그리고 공고 한 건이
깨져도 나머지 수집이 계속되는 것.
"""
import json
from pathlib import Path

import pytest

from collect.greenhouse import BoardSpec, fetch_payload, parse_board

FIXTURE = Path(__file__).parent / "fixtures" / "greenhouse_daangn.json"
SPEC = BoardSpec(token="daangn", company="당근", domain="ecommerce")
FETCHED = "2026-09-09T00:00:00+09:00"


@pytest.fixture
def payload():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _parse(payload, **kw):
    return parse_board(payload, SPEC, fetched_at=FETCHED, **kw)


def test_maps_fields_onto_posting(payload):
    result = _parse(payload)
    p = result.postings[0]
    assert p.source == "greenhouse"
    assert p.key == "greenhouse:7666768003"
    assert p.title == "Account Manager (인턴) - 로컬 잡스"
    assert p.location == "SEOUL"
    assert p.url.startswith("https://")
    assert p.fetched_at == FETCHED


def test_prefers_company_name_from_response(payload):
    """응답이 회사명을 주면 그걸 쓴다. 설정값은 폴백이다."""
    assert _parse(payload).postings[0].company == "당근마켓"


def test_unescapes_double_encoded_html(payload):
    """Greenhouse의 content는 HTML 엔티티로 이스케이프돼서 온다."""
    body = _parse(payload).postings[0].body_html
    assert "&lt;" not in body
    assert body.lstrip().startswith("<div")


def test_filters_locations_outside_korea(payload):
    payload["jobs"][0]["location"]["name"] = "Taipei, Taiwan"
    result = _parse(payload)
    assert len(result.postings) == 1
    assert result.postings[0].location == "SEOUL"


def test_excludes_template_postings(payload):
    """쿠팡 보드에는 'z-Test & Templates Only' 더미가 21건 섞여 있다."""
    payload["jobs"][0]["location"]["name"] = "z-Test & Templates Only"
    assert len(_parse(payload).postings) == 1


def test_one_bad_job_does_not_abort_the_board(payload):
    payload["jobs"][0]["title"] = "  "
    result = _parse(payload)
    assert len(result.postings) == 1
    assert len(result.skipped) == 1
    assert "title" in result.skipped[0].reason


def test_fetch_uses_cache_and_skips_network(tmp_path):
    cache = tmp_path / "daangn.json"
    cache.write_text(json.dumps({"jobs": []}), encoding="utf-8")

    def explode(url, timeout=0):  # pragma: no cover - 호출되면 실패다
        raise AssertionError(f"캐시가 있는데 네트워크를 탔다: {url}")

    assert fetch_payload(SPEC, cache, opener=explode) == {"jobs": []}


def test_fetch_writes_cache_on_miss(tmp_path):
    cache = tmp_path / "daangn.json"
    calls = []

    def fake(url, timeout=0):
        calls.append(url)
        return type("R", (), {"read": lambda self: b'{"jobs": []}'})()

    assert fetch_payload(SPEC, cache, opener=fake) == {"jobs": []}
    assert cache.exists()
    assert "content=true" in calls[0]
    assert "daangn" in calls[0]


def test_application_deadline_becomes_the_deadline(payload):
    payload["jobs"][0]["application_deadline"] = "2026-10-31T23:59:00Z"
    posting = _parse(payload).postings[0]
    assert posting.deadline == "2026-10-31" and posting.deadline_note is None


def test_missing_deadline_is_unknown_not_rolling(payload):
    """Greenhouse 는 마감 필드를 거의 안 쓴다(쿠팡 701건 전부 비어 있음).
    비었다고 상시채용이라 단정하지 않는다."""
    payload["jobs"][0]["application_deadline"] = None
    posting = _parse(payload).postings[0]
    assert posting.deadline is None and posting.deadline_note is None
