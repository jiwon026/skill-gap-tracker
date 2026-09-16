"""훈련 검색어 사전.

도구 이름은 과정명에 거의 나오지 않는다(2026-09-16 실측: A/B, 에어플로우,
스파크 모두 0건). 그래서 스킬마다 '직접 다루는 과정' 검색어와 '기반 역량
과정' 검색어를 사람이 직접 적는다. 이 파일은 그 사전이 형식을 지키는지 본다.
"""
import yaml

CONFIG = "config/training.yaml"
SKILLS = "config/skills.yaml"

REQUIRED_KEYS = {
    "window_days", "min_demand", "per_skill", "pages",
    "list_cache_days", "detail_cache_days",
    "metro_prefixes", "exclude_targets", "exclude_title_words",
}


def load(path):
    with open(path, encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def test_training_config_has_every_key():
    config = load(CONFIG)
    assert REQUIRED_KEYS <= set(config)
    assert config["min_demand"] >= 1 and config["per_skill"] >= 1
    assert config["pages"] >= 1
    assert "근로자원격훈련" in config["exclude_targets"]
    assert "재직자" in config["exclude_title_words"]
    assert config["metro_prefixes"] == ["서울", "경기", "인천"]


def test_search_terms_are_non_empty_strings():
    for entry in load(SKILLS)["skills"]:
        training = entry.get("training")
        if training is None:
            continue
        assert set(training) <= {"direct", "foundation"}, entry["id"]
        terms = [*training.get("direct", ()), *training.get("foundation", ())]
        assert terms, entry["id"]
        for term in terms:
            assert isinstance(term, str) and term.strip(), entry["id"]


def test_skills_that_need_courses_have_terms():
    """실측에서 부족으로 잡힌 스킬에는 검색어가 있어야 한다.

    검색어가 없으면 그 스킬은 조용히 추천에서 빠진다.
    """
    entries = {e["id"]: e for e in load(SKILLS)["skills"]}
    for skill_id in ("powerbi", "ab_test", "ga4", "airflow", "spark", "redash", "bigquery", "tableau"):
        assert entries[skill_id].get("training"), skill_id
