"""경험 매칭.

스킬 갭은 "무엇이 부족한가"만 알려준다. 지원 여부를 정하려면 "무엇으로
어필할 것인가"가 필요하고, 그 재료는 프로젝트다.

이 계층이 지켜야 할 것 셋.

  - `proves`의 스킬 id가 사전에 실제로 있어야 한다. 오타 하나가 보유 스킬을
    조용히 누락시켜 갭을 실제보다 나쁘게 만든다.
  - 신호는 제목과 본문을 함께 본다. 스킬과 달리 방법론·주제어라 본문에만
    적히는 경우가 많다.
  - 어필 근거가 없으면 빈 결과를 준다. 없는 경험을 지어내면 안 된다.
"""
from pathlib import Path

import pytest
import yaml

from extract.experience import ExperienceBook, match_experiences

CONFIG = Path(__file__).parent.parent / "config"
#: 개인 경험은 저장소에 없다. 동작은 더미로 검증하고, 개인 파일은 있을 때만
#: 스킬 id 가 사전에 있는지 따로 본다.
PERSONAL = CONFIG / "experience.yaml"


def _load(path):
    return ExperienceBook.from_config(yaml.safe_load(path.read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def book():
    return _load(CONFIG / "experience.example.yaml")


@pytest.fixture(scope="module")
def skill_ids():
    config = yaml.safe_load((CONFIG / "skills.yaml").read_text(encoding="utf-8"))
    return {s["id"] for s in config["skills"]}


class TestProvenSkills:
    def test_proven_skills_are_collected(self, book):
        proven = book.proven_skills()
        assert "funnel" in proven
        assert "retention" in proven
        assert "rfm" in proven

    def test_every_proven_id_exists_in_the_skill_dictionary(self, book, skill_ids):
        """오타 하나가 보유 스킬을 조용히 누락시켜 갭을 나쁘게 만든다."""
        unknown = book.proven_skills() - skill_ids
        assert not unknown, f"config/skills.yaml 에 없는 스킬 id: {sorted(unknown)}"

    @pytest.mark.skipif(not PERSONAL.exists(), reason="개인 경험 파일이 없다(저장소에는 더미만 있다)")
    def test_personal_config_uses_only_known_skill_ids(self, skill_ids):
        unknown = _load(PERSONAL).proven_skills() - skill_ids
        assert not unknown, f"config/skills.yaml 에 없는 스킬 id: {sorted(unknown)}"

    def test_config_rejects_unknown_skill_ids_when_validated(self):
        book = ExperienceBook.from_config(
            {"experiences": [{"id": "x", "name": "X", "pitch": "p",
                              "proves": ["typo_skill"], "signals": ["신호"]}]}
        )
        with pytest.raises(ValueError, match="typo_skill"):
            book.validate_against({"sql", "python"})


class TestMatching:
    def test_retention_posting_matches_the_retention_project(self, book):
        hits = match_experiences(
            "데이터 분석가", "리텐션과 코호트 분석 경험이 있는 분을 찾습니다", book
        )
        assert "retention_analysis" in {h.id for h in hits}

    def test_segmentation_posting_matches_the_model_project(self, book):
        hits = match_experiences(
            "데이터 분석가", "이탈 예측 모델과 고객 세그먼테이션을 담당합니다", book
        )
        assert "segment_model" in {h.id for h in hits}

    def test_review_posting_matches_the_text_project(self, book):
        hits = match_experiences("데이터 분석가", "고객 VOC 리뷰 텍스트 분석", book)
        assert "text_analysis" in {h.id for h in hits}

    def test_title_alone_can_match(self, book):
        """본문이 없는 소스(사람인)도 제목만으로 어필 근거가 잡혀야 한다."""
        hits = match_experiences("리텐션 분석 담당자", "", book)
        assert hits

    def test_unrelated_posting_matches_nothing(self, book):
        assert match_experiences("법무(사내변호사)", "사내 계약 검토", book) == ()

    def test_result_carries_the_pitch(self, book):
        hits = match_experiences("데이터 분석가", "리텐션 개선", book)
        assert hits[0].pitch
        assert len(hits[0].pitch) > 20

    def test_more_signals_ranks_higher(self, book):
        """여러 신호가 걸린 경험이 더 강한 어필 근거다."""
        hits = match_experiences(
            "데이터 분석가",
            "퍼널과 리텐션, 코호트 분석으로 온보딩을 개선하고 이탈을 줄입니다",
            book,
        )
        assert hits[0].id == "retention_analysis"

    def test_empty_input_is_safe(self, book):
        assert match_experiences("", "", book) == ()


class TestConfigValidation:
    def test_id_is_required(self):
        with pytest.raises(ValueError, match="id"):
            ExperienceBook.from_config({"experiences": [{"name": "X", "signals": ["a"]}]})

    def test_duplicate_ids_are_rejected(self):
        with pytest.raises(ValueError, match="중복"):
            ExperienceBook.from_config(
                {
                    "experiences": [
                        {"id": "a", "name": "A", "pitch": "p", "signals": ["x"]},
                        {"id": "a", "name": "B", "pitch": "p", "signals": ["y"]},
                    ]
                }
            )

    def test_signals_are_required(self):
        with pytest.raises(ValueError, match="signals"):
            ExperienceBook.from_config(
                {"experiences": [{"id": "a", "name": "A", "pitch": "p"}]}
            )

    def test_pitch_is_required(self):
        """Notion 의 '어필 포인트'에 그대로 실린다. 비면 칸이 빈다."""
        with pytest.raises(ValueError, match="pitch"):
            ExperienceBook.from_config(
                {"experiences": [{"id": "a", "name": "A", "signals": ["x"]}]}
            )
