"""사전 기반 스킬 추출.

LLM을 쓰지 않기로 한 결정(§2-1)이 성립하려면 이 추출기가 재현 가능하고
오탐이 통제돼야 한다. 짧은 스킬명(R, Java, dbt)과 한국어 조사 결합이
오탐·미탐의 두 축이다.
"""
import pytest

from extract.skills import SkillDict, extract_skills

DICT = SkillDict.from_entries([
    {"id": "sql", "name": "SQL", "category": "query", "aliases": ["SQL"]},
    {"id": "python", "name": "Python", "category": "language", "aliases": ["Python", "파이썬"]},
    {"id": "ga4", "name": "GA4", "category": "pa",
     "aliases": ["GA4", "Google Analytics", "구글 애널리틱스"]},
    {"id": "funnel", "name": "퍼널", "category": "method", "aliases": ["퍼널", "funnel"]},
    {"id": "java", "name": "Java", "category": "language", "pattern": r"\bJava\b(?!\s*Script)"},
    {"id": "dbt", "name": "dbt", "category": "pipeline", "pattern": r"\bdbt\b"},
])


def test_alias_maps_to_canonical_id():
    assert extract_skills("구글 애널리틱스 운영 경험", DICT) == {"ga4"}


def test_matching_is_case_insensitive():
    assert extract_skills("sql 쿼리 작성", DICT) == {"sql"}


def test_returns_each_skill_once():
    found = extract_skills("Python, python, 파이썬 모두 가능", DICT)
    assert found == {"python"}


def test_english_alias_respects_word_boundary():
    """'SQL'이 'NoSQL', 'MySQL' 안에서 잡히면 커버리지가 부풀려진다."""
    assert extract_skills("NoSQL 경험", DICT) == set()
    assert extract_skills("MySQL 운영", DICT) == set()


def test_korean_alias_ignores_particles():
    """한글은 조사가 붙어도 같은 단어다. 단어 경계를 적용하면 미탐이 난다."""
    assert extract_skills("퍼널을 개선한 경험", DICT) == {"funnel"}
    assert extract_skills("파이썬으로 전처리", DICT) == {"python"}


def test_pattern_overrides_aliases():
    assert extract_skills("Java 백엔드", DICT) == {"java"}
    assert extract_skills("JavaScript 프론트엔드", DICT) == set()


def test_lowercase_tool_name_needs_boundary():
    assert extract_skills("dbt 모델링", DICT) == {"dbt"}
    assert extract_skills("adbtx 는 무관", DICT) == set()


def test_multiword_alias_tolerates_extra_spaces():
    assert extract_skills("Google  Analytics 4 연동", DICT) == {"ga4"}


def test_empty_text_yields_nothing():
    assert extract_skills("", DICT) == set()


def test_unknown_tokens_are_reported_for_dictionary_growth():
    """사전에 없는 기술 토큰을 모아 다음 주 사전에 반영한다(unmatched 루프)."""
    unknown = DICT.unknown_tokens("요구 기술은 Airflow 와 Snowflake 운영, SQL 필수")
    assert "airflow" in unknown
    assert "snowflake" in unknown
    assert "sql" not in unknown


def test_unknown_tokens_ignore_plain_english_prose():
    assert DICT.unknown_tokens("we build great products and join the team") == frozenset()


def test_sentence_initial_capital_is_not_a_product_name():
    """'Please'와 'Airflow'는 형태가 같다. 대문자의 위치가 둘을 가른다."""
    assert DICT.unknown_tokens("Please apply now. Consider the role.") == frozenset()


def test_mid_sentence_capital_is_a_product_name():
    assert "airflow" in DICT.unknown_tokens("경험 with Airflow 운영")


def test_all_caps_acronym_survives_sentence_start():
    assert "etl" in DICT.unknown_tokens("ETL 파이프라인 구축")


def test_unknown_tokens_keep_versioned_names():
    assert "s3" in DICT.unknown_tokens("S3 버킷 운영")


def test_dictionary_rejects_duplicate_ids():
    with pytest.raises(ValueError, match="중복"):
        SkillDict.from_entries([
            {"id": "sql", "name": "SQL", "category": "q", "aliases": ["SQL"]},
            {"id": "sql", "name": "SQL2", "category": "q", "aliases": ["SQL2"]},
        ])


class TestEmptyAliasIsRejected:
    """공백 별칭 하나가 '무엇에나 매칭되는' 빈 패턴을 만든다.

    그러면 해당 스킬이 모든 공고의 요구 스킬이 되고, 예외도 로그도
    남지 않아 커버리지 지표가 조용히 무의미해진다.
    """

    def test_blank_alias_raises_instead_of_matching_everything(self):
        from extract.matching import alias_to_pattern

        with pytest.raises(ValueError, match="비어 있는 별칭"):
            alias_to_pattern("   ")

    def test_empty_alias_raises(self):
        from extract.matching import alias_to_pattern

        with pytest.raises(ValueError, match="비어 있는 별칭"):
            alias_to_pattern("")

    def test_dictionary_with_a_blank_alias_does_not_match_everything(self):
        try:
            dictionary = SkillDict.from_entries(
                [{"id": "sql", "name": "SQL", "aliases": ["SQL", "  "]}]
            )
        except ValueError:
            return  # 설정 로드 시점에 거부하는 것도 옳다
        assert not extract_skills("배달 라이더 모집", dictionary)
