"""경력 수준 판정.

재료가 소스마다 다르다는 것이 이 계층의 핵심 문제다. 우아한형제들과
사람인은 최소 요구 경력을 숫자로 주지만 Greenhouse는 주지 않는다.
숫자가 있으면 숫자를 믿고, 없을 때만 제목의 직급 표기로 추정한다.

추정은 틀릴 수 있으므로, 어느 쪽으로 틀리는 것이 나은지를 정해 두었다 —
판단 근거가 전혀 없으면 통과시킨다. 지원 가능한 공고를 놓치는 쪽이
쓸데없는 공고를 하나 더 보는 것보다 손해다.
"""
from pathlib import Path

import pytest
import yaml

from extract.seniority import SeniorityRule, is_entry_level, years_from_requirements

CONFIG = Path(__file__).parent.parent / "config" / "seniority.yaml"


@pytest.fixture(scope="module")
def rule():
    return SeniorityRule.from_config(yaml.safe_load(CONFIG.read_text(encoding="utf-8")))


class TestNumericYearsWin:
    """숫자가 있으면 제목을 보지 않는다. 숫자가 더 믿을 만하다."""

    @pytest.mark.parametrize("years", [None, 0, 1])
    def test_entry_level_years_pass(self, years, rule):
        assert is_entry_level("데이터 분석가", years, rule)

    @pytest.mark.parametrize("years", [2, 3, 5, 10])
    def test_experienced_years_are_rejected(self, years, rule):
        assert not is_entry_level("데이터 분석가", years, rule)

    def test_years_override_a_junior_marker_in_the_title(self):
        """'신입'이라 적혀 있어도 최소경력 3년이면 신입 공고가 아니다."""
        rule = SeniorityRule.from_config(
            {"max_years": 1, "senior_markers": ["Senior"], "junior_markers": ["신입"]}
        )
        assert not is_entry_level("신입 데이터 분석가", 3, rule)


class TestTitleFallback:
    """숫자가 없는 소스(Greenhouse)는 제목이 유일한 단서다."""

    @pytest.mark.parametrize(
        "title",
        [
            "Senior, Data Analyst (Audit Automation)",
            "Senior~Staff, Data Analyst (Catalog)",
            "Staff Data Analyst (Category Analytics)",
            "[쿠팡로지스틱스서비스] Principal, Business Analyst (OPEX)",
            "Director of Data Analytics",
            "Director and Head of CRM Analytics and Science",
            "[쿠팡] 시니어 운영 프로세스 개선 분석가",
            "배민 기존업주 영업관리(리더급/대전광주)",
        ],
    )
    def test_senior_titles_are_rejected(self, title, rule):
        assert not is_entry_level(title, None, rule)

    @pytest.mark.parametrize(
        "title",
        [
            "[Coupang] Business Analyst",
            "[CES] Business Analyst (푸드 딜리버리 운영)",
            "[CLS] 물류 데이터 분석 및 운영 전략 담당자",
            "데이터 분석가",
        ],
    )
    def test_plain_titles_pass(self, title, rule):
        assert is_entry_level(title, None, rule)

    @pytest.mark.parametrize(
        "title",
        [
            "[쿠팡] 카탈로그 데이터 품질 운영 담당자 (Junior,신입 가능)",
            "데이터 분석 인턴",
            "Data Analyst Intern",
            "데이터 분석가 (신입/경력)",
        ],
    )
    def test_junior_markers_pass(self, title, rule):
        assert is_entry_level(title, None, rule)

    def test_junior_marker_beats_a_senior_marker_in_the_same_title(self, rule):
        """'신입/경력' 동시 채용은 직급 표기가 있어도 지원할 수 있다."""
        assert is_entry_level("Senior Data Analyst (신입 가능)", None, rule)


class TestAmbiguity:
    def test_no_signal_at_all_passes(self, rule):
        """판단 근거가 없으면 통과시킨다. 놓치는 쪽이 더 손해다."""
        assert is_entry_level("", None, rule)

    def test_sr_abbreviation_is_caught(self, rule):
        assert not is_entry_level("Sr. Staff, Data Analyst", None, rule)

    def test_senior_marker_respects_word_boundary(self, rule):
        """'Lead'가 'Leadership'이나 'Leading'에서 잡히면 멀쩡한 공고가 죽는다."""
        assert is_entry_level("Data Analyst, Leading Indicators", None, rule)


class TestYearsFromRequirements:
    """Greenhouse 는 경력을 숫자로 주지 않지만 본문 자격요건에는 적혀 있다.

    제목만 보던 시절, 직군 필터를 통과한 쿠팡 공고 7건이 전부 2~5년 경력직
    이었는데 신입 대상 목록에 올라왔다. 제목에 Senior 가 없었을 뿐이다.

    이 함수가 틀리면 지원 가능한 공고가 조용히 사라진다. 그래서 필수 자격
    섹션 안에서만 읽는다 — 우대사항의 "3년 이상 우대"를 필수로 오인하면 안 된다.
    """

    def test_reads_years_under_the_required_header(self):
        segs = ("업무 내용", "SQL 로 지표 분석", "자격 요건", "최소 3년 이상의 데이터 분석 경험을 보유한 자")
        assert years_from_requirements(segs) == 3

    @pytest.mark.parametrize(
        "line, years",
        [
            ("최소 5년 이상의 데이터 분석 경험을 보유한 자", 5),
            ("Business Analyst 등 직무에서 3년 이상의 경력이 있으신 분", 3),
            ("유관 경력 2년 이상 보유 하신 분", 2),
            ("데이터 분석 또는 이에 준하는 업무 경험을 7년 이상 보유하신 분", 7),
            ("2년 이상의 SQL을 활용한 데이터 추출 및 분석 실무 경험이 있으신 분", 2),
            ("8+ years of experience in data science", 8),
            ("4+ years of relevant experience using business intelligence tools", 4),
            ("8-10 years of experience in Category Analytics", 8),
            ("경력 3~5년의 데이터 분석 경험", 3),
        ],
    )
    def test_real_phrasings(self, line, years):
        """모두 실수집 공고의 자격요건에서 옮긴 문장이다."""
        assert years_from_requirements(("자격 요건", line)) == years

    def test_english_required_header(self):
        segs = ("Basic Qualifications", "10+ years of experience in analytics")
        assert years_from_requirements(segs) == 10

    def test_bracketed_header(self):
        segs = ("[지원 자격]", "최소 5년 이상의 데이터 분석 경험을 보유한 자")
        assert years_from_requirements(segs) == 5

    def test_four_year_degree_is_not_four_years_of_experience(self):
        """'4년제 대학교'를 경력 4년으로 읽으면 안 된다."""
        line = "4년제 대학교 이상 졸업 & 유관 경력 2년 이상 보유 하신 분"
        assert years_from_requirements(("자격 요건", line)) == 2

    def test_alternative_paths_take_the_easier_one(self):
        """'대졸 2년 또는 고졸 5년'이면 지원 가능한 쪽은 2년이다."""
        segs = (
            "자격 요건",
            "4년제 대학교 이상 졸업 & 유관 경력 2년 이상 보유 하신 분",
            "또는 고등학교 이상 졸업 & CX 관련 경력 5년 이상을 보유하신 분",
        )
        assert years_from_requirements(segs) == 2

    def test_years_under_the_preferred_header_are_ignored(self):
        segs = ("자격 요건", "SQL 사용 가능하신 분", "우대 사항", "3년 이상 이커머스 분석 경력")
        assert years_from_requirements(segs) is None

    @pytest.mark.parametrize("header", ["우대 사항", "[우대 조건]", "Preferred Qualifications"])
    def test_preferred_headers_close_the_required_section(self, header):
        segs = ("자격 요건", "SQL 가능자", header, "5+ years of experience")
        assert years_from_requirements(segs) is None

    def test_preferred_marker_inside_a_required_line_is_ignored(self):
        """필수 섹션 안이라도 그 줄 자체가 '우대'라고 말하면 필수가 아니다."""
        segs = ("자격 요건", "SQL 가능자", "3년 이상 경력자 우대")
        assert years_from_requirements(segs) is None

    def test_line_ending_in_preferred_is_not_a_header(self):
        """헤더는 키워드로 시작한다. 끝에 붙은 'preferred'는 내용이다."""
        segs = ("Basic Qualifications", "English proficiency preferred", "5+ years of experience")
        assert years_from_requirements(segs) == 5

    def test_other_headers_close_the_required_section(self):
        segs = ("자격 요건", "SQL 가능자", "전형 절차 및 안내 사항", "서류 접수 후 3년간 보관합니다")
        assert years_from_requirements(segs) is None

    def test_years_outside_any_required_section_are_ignored(self):
        """근거가 없으면 통과시킨다. 자격요건 헤더가 없는 공고는 추정하지 않는다."""
        segs = ("회사 소개", "창립 10년, 5년 연속 성장한 경험 많은 회사입니다")
        assert years_from_requirements(segs) is None

    def test_numbers_without_experience_context_are_ignored(self):
        segs = ("자격 요건", "2026년 하반기 입사 가능하신 분")
        assert years_from_requirements(segs) is None

    def test_no_segments_yields_none(self):
        assert years_from_requirements(()) is None


class TestStatedYears:
    """본문에서 읽은 연수를 판정에 쓰는 순서.

    소스가 준 숫자 > 제목의 신입 표기 > 본문 자격요건의 연수 > 제목의 직급 표기
    """

    def test_stated_years_reject_an_experienced_posting(self, rule):
        assert not is_entry_level("[Coupang] Business Analyst", None, rule, stated_years=5)

    @pytest.mark.parametrize("years", [0, 1])
    def test_stated_entry_years_pass(self, years, rule):
        assert is_entry_level("데이터 분석가", None, rule, stated_years=years)

    def test_junior_title_beats_stated_years(self, rule):
        """'신입/경력' 공고의 '3년 이상'은 경력 트랙 이야기다."""
        assert is_entry_level("데이터 분석가 (신입/경력)", None, rule, stated_years=3)

    def test_source_years_beat_stated_years(self, rule):
        """소스가 구조화해서 준 숫자가 본문 추정보다 정확하다."""
        assert is_entry_level("데이터 분석가", 0, rule, stated_years=5)

    def test_without_stated_years_the_title_rule_still_applies(self, rule):
        assert not is_entry_level("Senior Data Analyst", None, rule)


class TestConfig:
    def test_max_years_is_required(self):
        with pytest.raises(ValueError, match="max_years"):
            SeniorityRule.from_config({"senior_markers": ["Senior"]})

    def test_negative_max_years_is_rejected(self):
        with pytest.raises(ValueError, match="max_years"):
            SeniorityRule.from_config({"max_years": -1})
