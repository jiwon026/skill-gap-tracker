"""근무지 판정 — 수도권만 (2026-09-10 결정).

소스마다 지역 표기가 제각각이다('서울특별시', '경기도', 'Seoul, South Korea',
'South Korea', '대한민국'). 수도권이 적혀 있으면 통과, 비수도권만 적혀 있으면
제외, 알 수 없으면 통과한다 — 우아한형제들은 근무지를 아예 안 줘서 '모름'을
제외로 치면 전부 사라진다.

제목을 지역 필드보다 먼저 본다. '…, 부산' 공고의 지역 필드가 서울 본사
주소인 경우가 있었다. 다만 제목에서는 지역명이 독립된 단어일 때만
센다 — '제주항공'·'부산은행'을 근무지로 읽으면 수도권 공고가 사라진다.
"""
from pathlib import Path

import pytest
import yaml

from extract.region import RegionRule, in_region

CONFIG = Path(__file__).parent.parent / "config" / "region.yaml"


@pytest.fixture(scope="module")
def rule():
    return RegionRule.from_config(yaml.safe_load(CONFIG.read_text(encoding="utf-8")))


class TestLocationField:
    @pytest.mark.parametrize(
        "location",
        [
            "서울 강남구 테헤란로 4길 14",
            "서울특별시 중구 세종대로",
            "서울시 마포구",
            "경기도 성남시 분당구",
            "경기 성남시 수정구",
            "인천 연수구 송도동",
            "Seoul, South Korea",
            "SEOUL",
            "서울 > 성동구",
        ],
    )
    def test_capital_area_passes(self, location, rule):
        assert in_region("데이터 분석가", location, rule)

    @pytest.mark.parametrize(
        "location",
        [
            "부산광역시 부산진구 서면로",
            "울산광역시 중구 종가6길",
            "대전 유성구",
            "광주광역시 서구",
            "제주특별자치도 제주시",
            "Busan, South Korea",
        ],
    )
    def test_other_regions_are_excluded(self, location, rule):
        assert not in_region("데이터 분석가", location, rule)

    def test_gyeonggi_gwangju_is_capital_area(self, rule):
        """'경기 광주시'는 수도권, '광주광역시'는 아니다. 수도권 표기를 먼저 본다."""
        assert in_region("데이터 분석가", "경기 광주시 오포읍", rule)
        assert not in_region("데이터 분석가", "광주 광산구", rule)

    @pytest.mark.parametrize("location", ["South Korea", "대한민국", "", "원격 근무"])
    def test_unknown_region_passes(self, location, rule):
        """근무지를 모르면 통과한다. 우아한형제들은 근무지를 아예 주지 않는다."""
        assert in_region("데이터 분석가", location, rule)


class TestTitle:
    def test_title_region_beats_a_headquarters_address(self, rule):
        """지역 필드가 본사 주소여도 제목이 '부산'이면 부산 근무다."""
        title = "[병역특례] 데이터 분석가 (Growth Data Analyst), 부산"
        assert not in_region(title, "서울특별시 강남구 영동대로", rule)

    def test_capital_area_in_the_title_passes(self, rule):
        title = "[주니어] 데이터 분석가 (Growth Data Analyst), 서울"
        assert in_region(title, "서울특별시 강남구 영동대로", rule)

    @pytest.mark.parametrize(
        "title",
        [
            "데이터 분석가 (부산)",
            "데이터 분석가 [부산]",
            "물류 데이터 분석 (울산1 캠프)",
            "부산센터 데이터 분석 담당",
        ],
    )
    def test_delimited_region_in_the_title_counts(self, title, rule):
        assert not in_region(title, "", rule)

    @pytest.mark.parametrize(
        "title",
        [
            "[제주항공] 데이터 분석가",
            "[BNK부산은행] 데이터 분석가",
            "서울대학교병원 데이터 분석",
        ],
    )
    def test_region_inside_a_name_is_not_a_work_site(self, title, rule):
        """회사·기관 이름 안의 지역명은 근무지가 아니다. 지역 필드로 판단한다."""
        assert in_region(title, "서울 강서구", rule)

    def test_multiple_sites_including_seoul_pass(self, rule):
        assert in_region("데이터 분석가 (서울/부산)", "", rule)


class TestConfig:
    def test_include_is_required(self):
        with pytest.raises(ValueError, match="include"):
            RegionRule.from_config({"exclude": ["부산"]})

    def test_exclude_is_required(self):
        with pytest.raises(ValueError, match="exclude"):
            RegionRule.from_config({"include": ["서울"]})
