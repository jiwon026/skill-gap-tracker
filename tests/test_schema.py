"""Posting 표준 레코드의 계약을 고정한다.

수집 계층의 경계에서 소스별 차이를 흡수하는 것이 Posting의 역할이므로,
불변성과 입력 검증이 여기서 깨지면 아래 계층 전체가 오염된다.
"""
import dataclasses
import json

import pytest

from collect.schema import Posting


def _valid(**overrides):
    base = dict(
        source="greenhouse",
        source_id="4123456",
        company="쿠팡",
        title="Business Analyst, Retail",
        location="Seoul, Korea",
        url="https://boards.greenhouse.io/coupang/jobs/4123456",
        body_html="<p>SQL, Python</p>",
        posted_at="2026-09-01T00:00:00Z",
        fetched_at="2026-09-09T00:00:00Z",
    )
    base.update(overrides)
    return Posting(**base)


def test_is_frozen():
    p = _valid()
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.title = "다른 제목"


def test_key_is_source_scoped():
    """소스가 다르면 같은 id라도 다른 공고다."""
    a = _valid(source="greenhouse", source_id="1")
    b = _valid(source="worknet", source_id="1")
    assert a.key != b.key
    assert a.key == "greenhouse:1"


@pytest.mark.parametrize("field", ["source", "source_id", "title", "url"])
def test_required_fields_reject_blank(field):
    with pytest.raises(ValueError, match=field):
        _valid(**{field: "   "})


def test_body_may_be_empty():
    """본문이 없는 소스도 수집은 되어야 한다. 추출 단계에서 걸러진다."""
    assert _valid(body_html="").body_html == ""


def test_jsonl_roundtrip():
    p = _valid()
    restored = Posting.from_json(json.loads(p.to_json()))
    assert restored == p


def test_to_json_is_single_line():
    """JSONL 스냅샷이므로 레코드에 개행이 섞이면 안 된다."""
    p = _valid(body_html="<p>줄1\n줄2</p>")
    assert "\n" not in p.to_json()


def test_deadline_fields_roundtrip():
    p = _valid(deadline="2026-09-30", deadline_note=None)
    assert Posting.from_json(json.loads(p.to_json())) == p


def test_company_id_roundtrip():
    p = _valid(company_id="C05422")
    assert Posting.from_json(json.loads(p.to_json())) == p


def test_snapshots_written_before_company_id_still_load():
    data = json.loads(_valid().to_json())
    data.pop("company_id")
    assert Posting.from_json(data).company_id is None


def test_snapshots_written_before_the_deadline_fields_still_load():
    """스냅샷은 재계산의 입구다. 필드가 늘었다고 옛 스냅샷을 못 읽으면 안 된다."""
    data = json.loads(_valid().to_json())
    data.pop("deadline")
    data.pop("deadline_note")
    restored = Posting.from_json(data)
    assert restored.deadline is None and restored.deadline_note is None


def test_notice_segments_are_recognised():
    """수집기가 본문 자리에 넣는 표식은 메타데이터다. 추출은 이것으로 거른다."""
    from collect.schema import NOTICE_PREFIX, is_notice

    assert is_notice(f"{NOTICE_PREFIX} 본문이 없습니다")
    assert is_notice(f"   {NOTICE_PREFIX} 앞에 공백")
    assert not is_notice("SQL 활용 경험이 있으신 분")
