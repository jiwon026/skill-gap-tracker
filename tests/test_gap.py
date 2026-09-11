"""스킬 갭 계산.

coverage 는 비율이라 분모가 작으면 부풀려진다. 요구 스킬이 하나뿐인
공고가 '100%'로 정렬 맨 위에 오는 것을 막는 책임은 호출부에 있지만,
그 함정이 실재한다는 것은 여기서 못 박아 둔다.
"""
from analyze.gap import Gap, compute_gap


def test_matched_and_missing_are_split():
    gap = compute_gap({"sql", "airflow"}, {"sql", "tableau"})
    assert gap.matched == ("sql",)
    assert gap.missing == ("airflow",)


def test_owned_but_not_required_is_not_counted():
    """이 공고에 대해서는 정보가 아니다."""
    assert compute_gap({"sql"}, {"sql", "r", "excel"}).matched == ("sql",)


def test_coverage_is_zero_when_nothing_is_required():
    """요구 0건에 1.0을 주면 스킬을 안 적은 공고가 정렬 맨 위로 온다."""
    assert compute_gap(set(), {"sql"}).coverage == 0.0


def test_coverage_of_a_single_requirement_is_a_known_trap():
    """요구 1건을 갖추면 100%다. 비율만으로 정렬하면 안 되는 이유다."""
    assert compute_gap({"sql"}, {"sql"}).coverage == 1.0


def test_labeled_falls_back_to_the_id():
    gap = Gap(matched=("sql",), missing=("unknown_skill",))
    assert gap.labeled({"sql": "SQL"}, "missing") == ("unknown_skill",)
    assert gap.labeled({"sql": "SQL"}, "matched") == ("SQL",)


def test_results_are_sorted_for_stable_output():
    gap = compute_gap({"zeta", "alpha", "mid"}, set())
    assert gap.missing == ("alpha", "mid", "zeta")
