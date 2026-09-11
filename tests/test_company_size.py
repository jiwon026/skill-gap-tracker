"""회사 규모 판정.

Notion 의 '규모'는 화이트리스트(companies.yaml)가 아는 회사만 대기업·중견으로
나오고 나머지는 전부 '기타'였다. 소스가 주는 회사 정보로 나머지를 가른다.

규칙은 세 줄이다.
    소스가 법정 규모(대기업·중견·중소)를 주면 그대로 쓴다.
    없으면 사원수 300명 이하를 중소로 본다. 그 이상은 모른다.
    중소 중 설립 10년 미만은 스타트업이다.
"""
import pytest

from collect.schema import CompanyProfile
from extract.company_size import SIZES, UNKNOWN, classify_size

YEAR = 2026


def size(**kw):
    return classify_size(CompanyProfile(**kw), current_year=YEAR)


def test_labels_run_from_large_to_small():
    """Notion select 는 옵션 순서대로 정렬된다. 모르는 값은 맨 뒤다."""
    assert SIZES == ("대기업", "중견", "중소", "스타트업")
    assert UNKNOWN == "기타"


class TestLegalSize:
    @pytest.mark.parametrize("legal", ["대기업", "중견", "중소"])
    def test_legal_size_is_used_as_is(self, legal):
        assert size(size_class=legal) == legal

    def test_legal_size_beats_headcount(self):
        """법정 규모는 매출·자산 기준이다. 사원이 적어도 중견일 수 있다."""
        assert size(size_class="중견", headcount_max=50) == "중견"

    def test_unknown_legal_label_is_rejected(self):
        """'중견기업'처럼 소스 표기가 그대로 들어오면 규모 열이 갈라진다.
        소스 표기를 맞추는 일은 수집기가 한다."""
        with pytest.raises(ValueError, match="size_class"):
            CompanyProfile(size_class="중견기업")


class TestHeadcount:
    def test_up_to_300_is_sme(self):
        assert size(headcount_max=300) == "중소"

    def test_above_300_is_unknown(self):
        """사원수로는 중견과 대기업을 가를 수 없다. 추정하지 않는다."""
        assert size(headcount_max=1000) is None

    def test_nothing_known_is_none(self):
        assert size() is None


class TestStartup:
    def test_young_sme_is_a_startup(self):
        assert size(size_class="중소", founded_year=2018) == "스타트업"

    def test_nine_years_is_still_a_startup(self):
        assert size(size_class="중소", founded_year=2017) == "스타트업"

    def test_ten_years_is_no_longer_a_startup(self):
        """채용 사이트들이 흔히 쓰는 설립 구간('설립4~9년' / '설립10년이상')과 같은 경계다."""
        assert size(size_class="중소", founded_year=2016) == "중소"

    def test_young_but_mid_sized_is_not_a_startup(self):
        assert size(size_class="중견", founded_year=2020) == "중견"

    def test_small_headcount_and_young_is_a_startup(self):
        assert size(headcount_max=50, founded_year=2023) == "스타트업"

    def test_unknown_founding_stays_sme(self):
        assert size(headcount_max=50) == "중소"


@pytest.mark.parametrize(
    "profile, expected",
    [
        # 법정 규모를 주는 소스
        (dict(size_class="중소", headcount_max=232, founded_year=2005), "중소"),
        (dict(size_class="중견", headcount_max=1602), "중견"),
        # 사원수 구간만 주는 소스
        (dict(headcount_max=300, founded_year=2014), "중소"),
        (dict(headcount_max=300, founded_year=2018), "스타트업"),
        (dict(headcount_max=50, founded_year=2004), "중소"),
        (dict(headcount_max=50, founded_year=2023), "스타트업"),
    ],
)
def test_profiles_seen_in_collected_data(profile, expected):
    """2026-09-11 실수집에서 나온 회사 정보의 모양들."""
    assert size(**profile) == expected
