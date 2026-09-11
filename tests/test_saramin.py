"""사람인 오픈API 수집기.

이 소스의 결정적 제약은 **본문이 없다는 것**이다. API는 메타데이터와
쉼표로 구분된 `keyword`만 준다. 그래서 스킬 추출이 먹을 수 있도록
키워드·직무코드·산업명을 텍스트로 합쳐 `body_html`에 넣는다.

가짜 본문을 만드는 것이므로, 그 사실이 데이터에 남아야 한다. 아래
테스트가 그 표식을 고정한다.
"""
import json

import pytest

from collect.saramin import (
    BODY_NOTICE,
    KeywordSpec,
    access_key_from_env,
    fetch_payload,
    parse_search,
)

SPEC = KeywordSpec(keyword="데이터 분석", domain="ecommerce")
FETCHED = "2026-09-09T00:00:00+09:00"


def make_job(**kw):
    base = {
        "id": "48123456",
        "url": "https://www.saramin.co.kr/zf_user/jobs/relay/view?rec_idx=48123456",
        "active": 1,
        "company": {"detail": {"name": "무신사", "href": "https://..."}},
        "position": {
            "title": "데이터 분석가 (이커머스)",
            "industry": {"code": "301", "name": "패션·의류"},
            "location": {"code": "101050", "name": "서울 > 성동구"},
            "job-type": {"code": "1", "name": "정규직"},
            "job-code": {"code": "84", "name": "데이터분석가,BI"},
            "experience-level": {"code": 2, "min": 3, "max": 7, "name": "경력 3~7년"},
        },
        "keyword": "데이터분석,SQL,Python,Tableau,Amplitude",
        "salary": {"code": "0", "name": "회사내규에 따름"},
        "posting-date": "2026-09-01 10:00:00 +0900",
        "expiration-date": "2026-10-01 23:59:00 +0900",
    }
    base.update(kw)
    return base


@pytest.fixture
def payload():
    return {"jobs": {"count": 1, "start": 0, "total": "1", "job": [make_job()]}}


def _parse(payload, **kw):
    return parse_search(payload, SPEC, fetched_at=FETCHED, **kw)


class TestParsing:
    def test_maps_fields_onto_posting(self, payload):
        posting = _parse(payload).postings[0]
        assert posting.source == "saramin"
        assert posting.key == "saramin:48123456"
        assert posting.title == "데이터 분석가 (이커머스)"
        assert posting.company == "무신사"
        assert posting.location == "서울 > 성동구"
        assert posting.url.startswith("https://www.saramin.co.kr")

    def test_body_is_assembled_from_keywords(self, payload):
        """본문이 없으므로 키워드·직무코드·산업명을 합쳐 대신 쓴다."""
        body = _parse(payload).postings[0].body_html
        assert "SQL" in body and "Tableau" in body
        assert "데이터분석가" in body
        assert "패션·의류" in body

    def test_body_carries_a_notice_that_it_is_not_the_real_posting(self, payload):
        """가짜 본문임이 데이터에 남아야 한다. 나중에 이 표식으로 걸러낼 수 있다."""
        assert BODY_NOTICE in _parse(payload).postings[0].body_html

    def test_posted_at_is_normalized_to_iso(self, payload):
        posted = _parse(payload).postings[0].posted_at
        assert posted.startswith("2026-09-01T10:00:00")
        assert posted.endswith("+09:00")

    def test_departments_carry_the_job_code_names(self, payload):
        """직무코드는 부서가 아니지만, 직군 판정이 볼 수 있는 유일한 분류다."""
        assert _parse(payload).postings[0].departments == ("데이터분석가", "BI")

    def test_closed_postings_are_excluded(self, payload):
        payload["jobs"]["job"][0]["active"] = 0
        assert not _parse(payload).postings

    def test_one_bad_job_does_not_abort_the_search(self, payload):
        payload["jobs"]["job"].append(make_job(id="48123457"))
        payload["jobs"]["job"][0]["position"]["title"] = "  "
        result = _parse(payload)
        assert len(result.postings) == 1
        assert len(result.skipped) == 1

    def test_empty_result_is_not_an_error(self):
        assert _parse({"jobs": {"job": []}}).postings == ()

    def test_single_job_returned_as_object_is_handled(self, payload):
        """결과가 1건일 때 API가 배열 대신 객체를 주는 경우가 있다."""
        payload["jobs"]["job"] = make_job()
        assert len(_parse(payload).postings) == 1


class TestFetching:
    def test_fetch_uses_cache_and_skips_network(self, tmp_path):
        cache = tmp_path / "saramin.json"
        cache.write_text(json.dumps({"jobs": {"job": []}}), encoding="utf-8")

        def explode(url, timeout=0):  # pragma: no cover - 호출되면 실패다
            raise AssertionError(f"캐시가 있는데 네트워크를 탔다: {url}")

        assert fetch_payload(SPEC, cache, "KEY", opener=explode) == {"jobs": {"job": []}}

    def test_fetch_sends_key_and_keyword(self, tmp_path):
        calls = []

        def fake(url, timeout=0):
            calls.append(url)
            body = {"jobs": {"total": "1", "job": [make_job()]}}
            return type("R", (), {"read": lambda self: json.dumps(body).encode()})()

        fetch_payload(SPEC, tmp_path / "s.json", "SECRET", opener=fake)
        assert "access-key=SECRET" in calls[0]
        assert "keywords=" in calls[0]
        assert "count=110" in calls[0]

    def test_fetch_pages_until_total_is_covered(self, tmp_path):
        """하루 500콜 제한이 있으므로 페이지를 무한히 돌면 안 된다."""
        calls = []

        def fake(url, timeout=0):
            calls.append(url)
            start = int(url.split("start=")[1].split("&")[0])
            jobs = [make_job(id=f"{start}-{i}") for i in range(110)] if start < 2 else []
            body = {"jobs": {"total": "220", "start": start, "job": jobs}}
            return type("R", (), {"read": lambda self: json.dumps(body).encode()})()

        payload = fetch_payload(SPEC, tmp_path / "s.json", "K", opener=fake)
        assert len(calls) == 2
        assert len(payload["jobs"]["job"]) == 220


class TestCredentials:
    def test_returns_none_when_key_is_absent(self, monkeypatch):
        monkeypatch.delenv("SARAMIN_ACCESS_KEY", raising=False)
        assert access_key_from_env() is None

    def test_returns_none_for_blank_key(self, monkeypatch):
        monkeypatch.setenv("SARAMIN_ACCESS_KEY", "   ")
        assert access_key_from_env() is None

    def test_returns_the_key_when_present(self, monkeypatch):
        monkeypatch.setenv("SARAMIN_ACCESS_KEY", "abc123")
        assert access_key_from_env() == "abc123"


class TestMalformedResponses:
    """공고 한 건의 결함이 파이프라인 전체를 죽이면 안 된다.

    `run.py`는 `parse_search`를 try 밖에서 부르므로, 여기서 예외가 나가면
    greenhouse·우아한형제들 결과까지 함께 날아간다.
    """

    @pytest.mark.parametrize("active", [None, "", "null", [], {}])
    def test_unparseable_active_does_not_raise(self, payload, active):
        payload["jobs"]["job"][0]["active"] = active
        result = _parse(payload)
        assert len(result.postings) == 1, "판정 불가는 '열려 있음'으로 본다"

    @pytest.mark.parametrize("active", [0, "0", "false", "N"])
    def test_closed_markers_are_all_recognized(self, payload, active):
        payload["jobs"]["job"][0]["active"] = active
        assert not _parse(payload).postings

    def test_non_object_job_is_skipped_not_raised(self, payload):
        payload["jobs"]["job"].append("이건 문자열이다")
        result = _parse(payload)
        assert len(result.postings) == 1
        assert len(result.skipped) == 1

    def test_missing_company_falls_back_instead_of_empty(self, payload):
        """빈 회사명은 run.analyze 에서 서로 다른 회사를 하나로 뭉치게 한다."""
        payload["jobs"]["job"][0]["company"] = {"detail": {}}
        assert _parse(payload).postings[0].company.startswith("(미상)")

    def test_unparseable_date_becomes_none_not_the_raw_string(self, payload):
        """원본을 흘려보내면 Notion이 그 행만 400으로 떨군다."""
        payload["jobs"]["job"][0]["posting-date"] = "상시채용"
        assert _parse(payload).postings[0].posted_at is None


class TestCallBudget:
    def test_max_pages_bounds_the_daily_call_usage(self):
        """하루 500콜 제한. 이 상한이 계약이다."""
        from collect.saramin import MAX_PAGES

        assert MAX_PAGES <= 5

    def test_truncation_at_max_pages_is_reported(self, tmp_path, capsys):
        def fake(url, timeout=0):
            body = {"jobs": {"total": "5000", "job": [make_job(id=str(i)) for i in range(110)]}}
            return type("R", (), {"read": lambda self: json.dumps(body).encode()})()

        from collect.saramin import MAX_PAGES

        fetch_payload(SPEC, tmp_path / "s.json", "K", opener=fake)
        assert "잘렸습니다" in capsys.readouterr().err

    def test_missing_total_is_reported_not_silent(self, tmp_path, capsys):
        """total 이 사라지면 110건에서 조용히 멈춘다. 커버리지가 1/N로 떨어진다."""
        def fake(url, timeout=0):
            body = {"jobs": {"job": [make_job(id=str(i)) for i in range(110)]}}
            return type("R", (), {"read": lambda self: json.dumps(body).encode()})()

        fetch_payload(SPEC, tmp_path / "s.json", "K", opener=fake)
        assert "total" in capsys.readouterr().err


class TestDeadline:
    def _one(self, **kw):
        payload = {"jobs": {"count": 1, "start": 0, "total": "1", "job": [make_job(**kw)]}}
        return _parse(payload).postings[0]

    def test_expiration_date_is_the_deadline(self):
        p = self._one()
        assert p.deadline == "2026-10-01" and p.deadline_note is None

    @pytest.mark.parametrize(
        "code, note", [("2", "채용시 마감"), ("3", "상시채용"), ("4", "수시채용")]
    )
    def test_close_types_without_a_fixed_date(self, code, note):
        """날짜 없이 끝나는 공고. 사람인이 먼 미래 마감일을 붙여 보내도 믿지 않는다."""
        p = self._one(**{"close-type": {"code": code, "name": "x"}})
        assert p.deadline_note == note and p.deadline is None

    def test_unparseable_expiration_is_ignored(self):
        p = self._one(**{"expiration-date": "상시"})
        assert p.deadline is None


def test_body_notice_uses_the_shared_prefix():
    """머리말이 없으면 추출 단계가 표식을 본문으로 읽는다. 사람인 표식의
    '키워드'·'API'가 경험 신호어에 걸려 모든 공고에 가짜 어필이 붙었다."""
    from collect.schema import is_notice

    assert is_notice(BODY_NOTICE)
