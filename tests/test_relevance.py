"""직군 판정.

수집 소스를 늘리면 대부분이 무관한 공고다. 이 계층이 지켜야 할 것은
두 가지다. 붙여 쓴 한글 직군명('데이터엔지니어링')을 놓치지 않는 것,
그리고 본문을 보지 않는 것 — 본문에는 어느 회사나 '데이터 기반'이라고
써 두어서, 본문까지 보면 전 직군이 통과한다.
"""
from pathlib import Path

import pytest
import yaml

from extract.relevance import RelevanceDict, classify

CONFIG = Path(__file__).parent.parent / "config" / "relevance.yaml"


@pytest.fixture(scope="module")
def dictionary():
    return RelevanceDict.from_config(yaml.safe_load(CONFIG.read_text(encoding="utf-8")))


@pytest.mark.parametrize(
    "title",
    [
        "데이터 분석가 (이커머스)",
        "Data Analyst - Retail",
        "Business Analyst(간편픽업)",
        "프로덕트 애널리스트",
        "데이터과학(딜리버리)",
    ],
)
def test_core_titles_are_core(title, dictionary):
    assert classify(title, (), dictionary).relevant


@pytest.mark.parametrize(
    "title",
    [
        "데이터엔지니어링(데이터서비스 AI)",
        "Data Engineer",
        "머신러닝 엔지니어",
        "추천시스템 개발자",
        "Software Engineer, Backend - 피드 (ML Data Platform)",
        "Analytics Engineer",
        "BI 엔지니어",
    ],
)
def test_developer_roles_are_excluded(title, dictionary):
    """개발·연구직은 수집 대상이 아니다(2026-09-09 결정).

    스킬은 겹치지만 직무가 다르다. 실수집 36건 중 19건이 여기였다.
    'Analytics Engineer'가 core의 짧은 항목('Analytics')으로 새어 들어오지
    않는지가 여기서 지켜진다.
    """
    assert classify(title, (), dictionary).relevant is False


@pytest.mark.parametrize(
    "title",
    [
        "배민 기존업주 영업관리(리더급/대전광주)",
        "사업관리(딜리버리)",
        "iOS Developer",
        "법무(사내변호사)",
    ],
)
def test_unrelated_titles_are_rejected(title, dictionary):
    verdict = classify(title, (), dictionary)
    assert not verdict.relevant
    assert not verdict.matched


def test_spaceless_korean_title_still_matches(dictionary):
    """붙여 쓴 '데이터엔지니어링'도 사전의 '데이터 엔지니어'로 잡혀야 한다.

    이게 안 되면 제외가 통째로 새서 개발직이 다시 들어온다.
    """
    assert classify("데이터엔지니어링", (), dictionary).relevant is False


def test_exclusion_wins_even_when_a_core_term_is_also_present(dictionary):
    """제외는 core 판정보다 먼저 돈다."""
    assert classify("데이터 분석 / 데이터 엔지니어 통합 채용", (), dictionary).relevant is False


def test_exclusions_beat_matches(dictionary):
    assert classify("데이터센터 운영 담당자", (), dictionary).relevant is False
    assert classify("데이터 분석 인재풀", (), dictionary).relevant is False


def test_title_exclusion_still_rejects_the_whole_posting(dictionary):
    """제목이 직무를 정한다. 제목이 엔지니어면 부서에 '데이터 분석'이 있어도 제외."""
    assert classify("Data Engineer", ("데이터 분석",), dictionary).relevant is False


def test_excluded_department_is_dropped_not_fatal(dictionary):
    """공채는 여러 직무를 부서로 싣는다. '데이터 엔지니어'가 섞였다고 '데이터
    분석가'까지 버리면 데이터 직무가 포함된 공채가 전부 사라진다. 사람인
    직무코드도 같은 모양이다."""
    verdict = classify(
        "2026 하반기 신입·경력 집중 채용 #공채",
        ("퍼포먼스 마케터", "데이터 분석가", "데이터 엔지니어"),
        dictionary,
    )
    assert verdict.relevant


def test_only_excluded_departments_leave_nothing(dictionary):
    assert classify("2026 하반기 공채", ("데이터 엔지니어", "Backend"), dictionary).relevant is False


def test_department_is_examined_when_title_is_vague(dictionary):
    """제목이 '주니어 채용'이어도 부서가 데이터팀이면 후보다."""
    assert classify("2026 신입 공채", ("Data Analytics",), dictionary).relevant


def test_body_is_never_examined(dictionary):
    """본문은 인자로 받지도 않는다. 이 테스트는 시그니처를 고정한다."""
    with pytest.raises(TypeError):
        classify("영업관리", (), dictionary, "데이터 기반으로 일하는 팀입니다")


def test_matched_terms_are_reported(dictionary):
    """왜 통과했는지 남아야 사전을 고칠 수 있다."""
    verdict = classify("Data Analyst", (), dictionary)
    assert "Data Analyst" in verdict.matched


def test_ascii_alias_respects_word_boundary(dictionary):
    """'BA'가 'DATABASE' 안에서 잡히면 전 직군이 통과한다."""
    assert classify("DATABASE 운영", (), dictionary).relevant is False


def test_empty_title_is_rejected_without_error(dictionary):
    assert classify("", (), dictionary).relevant is False


def test_config_rejects_unknown_key():
    """adjacent 는 2026-09-11에 없앴다. 남은 설정이 조용히 무시되면 거기 적은
    판정어가 아무 일도 안 하는데 아무도 모른다."""
    with pytest.raises(ValueError, match="adjacent"):
        RelevanceDict.from_config({"core": ["데이터 분석"], "adjacent": ["X"]})


def test_config_requires_at_least_one_core_term():
    with pytest.raises(ValueError, match="core"):
        RelevanceDict.from_config({"exclude": ["Data Engineer"]})


class TestRealTitlesFromCollectedData:
    """2026-09-09 실수집 479건을 감사하면서 조정한 판정들.

    사전을 손댈 때 이 판정들이 뒤집히면 그 변경은 되돌려야 한다.
    특히 '분석가'를 core에 넣은 대가로 보안·재무 직군이 딸려오므로,
    그 제외가 살아 있는지가 여기서 지켜진다.
    """

    @pytest.mark.parametrize(
        "title",
        [
            "[쿠팡] 시니어 운영 프로세스 개선 분석가 (Catalog Ops Improvement)",
            "[쿠팡] 프로그램 매니저 (Eats Merchant Experience - Data Insights)",
            "[쿠팡] 비즈니스 분석가(Business Analyst_Retail Finance)",
            "Senior Staff, Data Scientist (Incrementality and Attribution)",
        ],
    )
    def test_data_roles_are_core(self, title, dictionary):
        assert classify(title, (), dictionary).relevant

    @pytest.mark.parametrize(
        "title",
        [
            "[CLS] Operation Research Scientist (First & Middlemile)",
            "Applied Scientist II - Moloco Commerce Media",
            "[쿠팡] 카탈로그 데이터 품질 운영 담당자 (Junior,신입 가능)",
            "Senior Staff, Machine Learning Engineer (Coupang Eats)",
            "데이터엔지니어링(마케팅플랫폼)",
        ],
    )
    def test_research_and_engineering_roles_are_excluded(self, title, dictionary):
        assert classify(title, (), dictionary).relevant is False

    @pytest.mark.parametrize(
        "title",
        [
            "Tier1 Security Analyst (계약직 - 선임/후임분석가)",
            "Security Analyst, Senior and Staff (CSOC)",
            "[CLS] Junior Financial Planning & Analysis",
            "[쿠팡풀필먼트서비스] Business Analyst (Fresh FC Fp&A)",
        ],
    )
    def test_security_and_finance_roles_are_excluded(self, title, dictionary):
        """'분석가'·'Business Analyst'를 품고 있어도 보유 스킬과 접점이 없다."""
        assert classify(title, (), dictionary).relevant is False


@pytest.mark.parametrize(
    "title",
    [
        "[병역특례] 데이터 분석가 (Growth Data Analyst), 서울",
        "(병역특례) 데이터 분석가",
        "[전문연구요원] 데이터 사이언티스트",
        "[산업기능요원] 데이터 분석 담당자",
    ],
)
def test_military_service_tracks_are_excluded(title, dictionary):
    """병역특례 전용 트랙은 해당자만 지원할 수 있다."""
    assert classify(title, (), dictionary).relevant is False


@pytest.mark.parametrize(
    "title",
    ["데이터 분석가 (병역특례 가능)", "데이터 분석가 (전문연구요원 편입 가능)"],
)
def test_general_postings_that_also_accept_the_track_stay(title, dictionary):
    """'병역특례 가능'은 누구나 지원하는 일반 공고에 붙는 말이다. 단어만 보고
    거르면 지원할 수 있는 공고가 사라진다. 태그 형태만 제외한다."""
    assert classify(title, (), dictionary).relevant


class TestTargetCompanyTracks:
    """대상 회사 공고에서만 통과시키는 직무(`target`).

    신입은 '데이터 분석가'가 아니라 공채 사업 직무로 뽑는 회사가 많다. 그렇다고
    마케팅, SCM 을 모든 회사에 열면 전 산업의 마케터 공고가 쏟아진다. 그래서
    지원 대상 회사일 때만 연다. 판정은 여전히 제목과 부서만 본다.
    """

    def test_tracks_pass_only_for_target_companies(self, dictionary):
        departments = ("MD", "마케팅", "디자인")
        assert classify("2026 하반기 신입사원 채용 #공채", departments, dictionary, target=True).relevant
        assert classify("2026 하반기 신입사원 채용 #공채", departments, dictionary).relevant is False

    @pytest.mark.parametrize(
        "title, departments",
        [
            ("2026 하반기 신입사원 채용", ("SCM", "재무")),
            ("2026 하반기 신입사원 채용", ("퍼포먼스 마케터",)),
            ("CRM 마케팅 담당자 (신입)", ()),
            ("2026 하반기 커머스부문 신입사원 채용", ("일반MD", "데이터 엔지니어")),
            ("Data Engineer (신입)", ()),
        ],
    )
    def test_target_tracks(self, title, departments, dictionary):
        verdict = classify(title, departments, dictionary, target=True)
        assert verdict.relevant, verdict

    def test_an_exclusion_inside_the_track_is_waived(self, dictionary):
        """'데이터 엔지니어'는 제외어 '엔지니어'를 품고 있다. 대상 회사면 그 제외는 면제다."""
        verdict = classify("데이터 엔지니어 (신입)", (), dictionary, target=True)
        assert verdict.matched == ("데이터 엔지니어",)

    @pytest.mark.parametrize(
        "title",
        [
            "Analytics Engineer",      # 트랙 밖의 엔지니어
            "SCM 엔지니어",             # 엔지니어는 SCM 이 잡은 범위 밖이다
            "마케팅 인재풀",            # 상시 더미 항목은 대상 회사여도 뺀다
            "[병역특례] 데이터 엔지니어",
            "운영지원(배민상회 정산/SCM)",  # 운영직은 넓히지 않는다(2026-09-21)
            "[어시스턴트] 그로스 마케팅 업무 운영 지원 채용",
            "[어시스턴트] 오프라인 결제 마케팅 업무 지원 채용",  # 업무 보조직
            "Marketing Design Assistant (인턴)",  # 디자인 직무
        ],
    )
    def test_other_exclusions_still_win(self, title, dictionary):
        assert classify(title, (), dictionary, target=True).relevant is False

    def test_excluded_department_is_still_dropped_for_targets(self, dictionary):
        verdict = classify("2026 공채", ("Backend개발", "SCM"), dictionary, target=True)
        assert verdict.matched == ("SCM",)

    def test_non_target_postings_keep_the_old_rules(self, dictionary):
        """대상 회사가 아니면 예전과 똑같다. 데이터 엔지니어는 여전히 제외."""
        assert classify("데이터 엔지니어 (신입)", (), dictionary).relevant is False
        assert classify("2026 공채", ("SCM",), dictionary).relevant is False
