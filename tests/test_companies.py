"""회사 식별.

쿠팡이 '쿠팡풀필먼트서비스'로, 신세계가 'SSG.COM'으로 공고를 올린다.
표기가 갈라져도 같은 회사로 묶여야 tier 정렬과 사람인 결과 좁히기가
동작한다. 반대로 짧은 영문 별칭이 다른 단어 안에서 잡히면 안 된다.
"""
from pathlib import Path

import pytest
import yaml

from extract.companies import CompanyBook

CONFIG = Path(__file__).parent.parent / "config"
#: 지원 대상 목록은 저장소에 없다. 동작은 더미로 검증하고, 개인 목록은 있을
#: 때만 별칭이 겹치지 않는지 따로 본다.
PERSONAL = CONFIG / "companies.yaml"


def _load(path):
    return CompanyBook.from_config(yaml.safe_load(path.read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def book():
    return _load(CONFIG / "companies.example.yaml")


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Coupang", "쿠팡"),
        ("쿠팡풀필먼트서비스", "쿠팡"),
        ("[CLS] 쿠팡로지스틱스서비스", "쿠팡"),
        ("SSG.COM", "신세계그룹"),
        ("지마켓글로벌", "신세계그룹"),
        ("우아한형제들", "우아한형제들"),
        ("주식회사 컬리", "컬리"),
        ("당근마켓", "당근"),
        ("29CM", "무신사"),
    ],
)
def test_identifies_company_across_alias_forms(raw, expected, book):
    company = book.identify(raw)
    assert company is not None
    assert company.name == expected


def test_reports_tier_and_segment(book):
    company = book.identify("Coupang")
    assert company.tier == "대기업"
    assert company.segment == "종합몰"


def test_unknown_company_returns_none(book):
    assert book.identify("모르는회사") is None
    assert book.identify("") is None


def test_short_ascii_alias_respects_word_boundary(book):
    """'CLS'가 'CLSTER' 안에서 잡히면 전혀 다른 회사가 쿠팡이 된다."""
    assert book.identify("CLSTER Inc") is None


def test_tier_rank_orders_대기업_first(book):
    """리포트 정렬에 쓰인다. 값이 아니라 순서가 계약이다."""
    assert book.tier_rank("대기업") < book.tier_rank("중견")
    assert book.tier_rank("중견") < book.tier_rank(None)


def test_config_rejects_duplicate_company_names():
    with pytest.raises(ValueError, match="중복"):
        CompanyBook.from_config(
            {
                "companies": [
                    {"name": "쿠팡", "tier": "대기업", "aliases": ["쿠팡"]},
                    {"name": "쿠팡", "tier": "중견", "aliases": ["Coupang"]},
                ]
            }
        )


def test_config_requires_aliases():
    with pytest.raises(ValueError, match="aliases"):
        CompanyBook.from_config({"companies": [{"name": "쿠팡", "tier": "대기업"}]})


def test_config_rejects_unknown_tier():
    with pytest.raises(ValueError, match="tier"):
        CompanyBook.from_config(
            {"companies": [{"name": "쿠팡", "tier": "소기업", "aliases": ["쿠팡"]}]}
        )


def test_config_rejects_overlapping_aliases():
    """겹치는 별칭은 YAML 순서에 따라 조용히 다른 회사로 귀속된다."""
    with pytest.raises(ValueError, match="별칭 중복"):
        CompanyBook.from_config(
            {
                "companies": [
                    {"name": "쿠팡", "tier": "대기업", "aliases": ["쿠팡", "CLS"]},
                    {"name": "컬리", "tier": "대기업", "aliases": ["컬리", "cls"]},
                ]
            }
        )


@pytest.mark.skipif(not PERSONAL.exists(), reason="개인 회사 목록이 없다(저장소에는 더미만 있다)")
def test_personal_config_has_no_overlapping_aliases():
    """실제 목록이 이 규칙을 지키는지 — 회사가 늘어날 때마다 여기서 걸린다.
    겹치면 from_config 가 예외를 낸다."""
    assert _load(PERSONAL).companies
