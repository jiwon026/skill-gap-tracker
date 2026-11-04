"""소스 간 같은 공고.

다른 사이트 공고를 다시 올리는 소스가 있다. 같은 공고가 두 소스로 들어오면
key 가 달라 Notion 에 행이 두 개 생기고, 한쪽에 적은 '상태'가 다른 쪽에는 없다.
다시 올리는 소스 쪽을 뺀다. 원래 소스가 회사 번호, 경력 숫자 같은 정보를 더 준다.
"""
from collect.schema import Posting
from extract.duplicates import reposted_duplicates


def posting(source, source_id, company, title):
    return Posting(
        source=source, source_id=source_id, company=company, title=title, location="서울",
        url=f"https://example.com/{source}/{source_id}", body_html="", posted_at=None,
        fetched_at="2026-11-04T10:30:00+09:00",
    )


REPOST = frozenset({"mirror"})


def test_bracketed_company_prefix_is_ignored():
    rows = [
        posting("board", "1", "테스트커머스", "데이터 분석 체험형 인턴 채용"),
        posting("mirror", "9", "테스트커머스", "[테스트커머스] 데이터 분석 체험형 인턴 채용"),
    ]
    assert reposted_duplicates(rows, REPOST) == {"mirror:9"}


def test_company_written_two_ways_still_matches():
    """한 사이트는 '씨제이커머스(CJ커머스)', 다른 사이트는 'CJ커머스'로 적는다."""
    rows = [
        posting("board", "1", "씨제이커머스(CJ커머스)", "Product Data Analyst"),
        posting("mirror", "9", "CJ커머스", "[CJ커머스] Product Data Analyst"),
    ]
    assert reposted_duplicates(rows, REPOST) == {"mirror:9"}


def test_one_title_inside_the_other_matches():
    rows = [
        posting("board", "1", "테스트커머스", "Data Analyst (전환형)"),
        posting("mirror", "9", "테스트커머스", "[인턴] Data Analyst(전환형)"),
    ]
    assert reposted_duplicates(rows, REPOST) == {"mirror:9"}


def test_different_postings_of_one_company_stay():
    rows = [
        posting("board", "1", "테스트커머스", "데이터 분석 체험형 인턴 채용"),
        posting("mirror", "9", "테스트커머스", "[테스트커머스] 데이터 사업 담당자"),
    ]
    assert reposted_duplicates(rows, REPOST) == frozenset()


def test_same_title_at_another_company_stays():
    rows = [
        posting("board", "1", "테스트커머스", "데이터 분석가"),
        posting("mirror", "9", "다른회사", "데이터 분석가"),
    ]
    assert reposted_duplicates(rows, REPOST) == frozenset()


def test_short_titles_need_an_exact_match():
    """'인턴'이 'Data 인턴'에 들어 있다고 같은 공고는 아니다."""
    rows = [
        posting("board", "1", "테스트커머스", "Data 인턴 채용"),
        posting("mirror", "9", "테스트커머스", "인턴"),
    ]
    assert reposted_duplicates(rows, REPOST) == frozenset()


def test_only_the_reposting_side_is_dropped():
    rows = [
        posting("board", "1", "테스트커머스", "데이터 분석가"),
        posting("other", "2", "테스트커머스", "데이터 분석가"),
    ]
    assert reposted_duplicates(rows, REPOST) == frozenset()


def test_without_reposting_sources_nothing_is_dropped():
    rows = [
        posting("board", "1", "테스트커머스", "데이터 분석가"),
        posting("mirror", "9", "테스트커머스", "데이터 분석가"),
    ]
    assert reposted_duplicates(rows, frozenset()) == frozenset()


def test_a_long_identical_title_tolerates_a_differently_named_company():
    """한 사이트는 법인명, 다른 사이트는 브랜드명으로 적어도 긴 제목이 같으면 같은 공고다."""
    rows = [
        posting("board", "1", "테스트회계법인", "컨설팅부문 수요예측 데이터 분석 인턴 채용"),
        posting("mirror", "9", "EY테스트", "[EY테스트] 컨설팅부문 수요예측 데이터 분석 인턴"),
    ]
    assert reposted_duplicates(rows, REPOST) == {"mirror:9"}


def test_a_shared_word_is_not_enough_for_a_short_title():
    rows = [
        posting("board", "1", "테스트회계법인", "데이터 분석가"),
        posting("mirror", "9", "EY테스트", "데이터 분석가 채용"),
    ]
    assert reposted_duplicates(rows, REPOST) == frozenset()
