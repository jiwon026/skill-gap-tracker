"""회사 상용구 제거.

같은 회사 공고에는 회사 소개·법무 부록·복지 안내가 토씨까지 같게 반복된다.
이걸 남겨두면 사전 보강 루프가 법률 용어에 잠기고, 스킬 빈도도 부풀려진다.
스톱워드를 늘리는 대신 '여러 공고에 그대로 반복되는 문단'이라는 성질로 지운다.
"""
from extract.boilerplate import find_boilerplate, strip_boilerplate

FOOTER = "본 공고는 개인정보보호법에 따라 처리되며 문의는 채용팀으로 주시기 바랍니다"
INTRO = "우리는 세상을 바꾸는 하이퍼커넥티드 커머스 기업입니다"


def _docs():
    return [
        (INTRO, "SQL 과 Python 을 활용한 지표 분석", FOOTER),
        (INTRO, "Airflow 파이프라인 운영 경험", FOOTER),
        (INTRO, "Tableau 대시보드 구축", FOOTER),
        (INTRO, "고객 응대 및 매장 운영", FOOTER),
    ]


def test_finds_segments_repeated_across_documents():
    found = find_boilerplate(_docs())
    assert FOOTER in found
    assert INTRO in found


def test_keeps_segments_unique_to_one_document():
    found = find_boilerplate(_docs())
    assert "Airflow 파이프라인 운영 경험" not in found


def test_strip_removes_only_boilerplate():
    docs = _docs()
    found = find_boilerplate(docs)
    assert strip_boilerplate(docs[0], found) == ("SQL 과 Python 을 활용한 지표 분석",)


def test_short_segments_are_never_boilerplate():
    """'SQL' 같은 짧은 조각은 여러 공고에 나와도 상용구가 아니다."""
    docs = [("SQL", "본문 A 입니다"), ("SQL", "본문 B 입니다"), ("SQL", "본문 C 입니다")]
    assert "SQL" not in find_boilerplate(docs, min_chars=10)


def test_threshold_controls_aggressiveness():
    docs = [("공통 문단이 여기에 아주 길게 들어갑니다", "A"), ("공통 문단이 여기에 아주 길게 들어갑니다", "B"),
            ("혼자만 있는 문단이 여기에 들어갑니다", "C"), ("또 다른 단독 문단이 들어갑니다", "D")]
    assert "공통 문단이 여기에 아주 길게 들어갑니다" in find_boilerplate(docs, threshold=0.5)
    assert "공통 문단이 여기에 아주 길게 들어갑니다" not in find_boilerplate(docs, threshold=0.9)


def test_a_paragraph_must_repeat_in_two_postings():
    """표본이 작으면 비율 기준(0.5 × 2 = 1)만으로는 한 번 나온 문단도 상용구가
    된다. min_sample 을 낮춰도 고유 문단은 지워지지 않아야 한다."""
    docs = [
        (INTRO, "A 공고만의 자격요건 문단입니다 SQL 활용 경험"),
        (INTRO, "B 공고만의 자격요건 문단입니다 Tableau 활용 경험"),
    ]
    assert find_boilerplate(docs, min_sample=2) == frozenset({INTRO})


JD = (
    "그로스 데이터 분석가로서 퍼널과 리텐션을 분석합니다",
    "SQL 을 활용한 데이터 추출 경험이 있으신 분",
    "A/B 테스트를 설계하고 결과를 해석해 보신 분",
    "Python 으로 데이터를 전처리해 보신 분",
    "Tableau 로 대시보드를 만들어 보신 분",
    "지표를 정의하고 모니터링 체계를 만들어 보신 분",
    "유관 부서와 협업해 문제를 풀어 보신 분",
    "가설을 세우고 데이터로 검증해 보신 분",
    "이커머스 도메인에 관심이 있으신 분",
    "주도적으로 과제를 끝까지 완수하시는 분",
)


def test_duplicate_postings_are_not_mistaken_for_boilerplate():
    """같은 공고를 지역·트랙만 바꿔 여러 번 올리면 본문 전체가 '반복'이 된다.
    한 회사가 서울·부산·병역특례로 세 번 올린 공고의 자격요건이 이렇게 지워졌다."""
    docs = [
        JD + ("근무지는 서울 본사이며 출퇴근이 편리합니다",),
        JD + ("근무지는 부산 지사이며 출퇴근이 편리합니다",),
        JD + ("근무지는 서울 본사이며 출퇴근이 편리합니다",),
    ]
    found = find_boilerplate(docs)
    assert strip_boilerplate(docs[0], found) == docs[0]


def test_duplicates_are_counted_once_but_real_boilerplate_still_found():
    """한 직무를 세 번, 다른 직무를 두 번 올린 회사. 복제는 한 건으로 세고,
    세 직무에 공통인 회사 소개는 그대로 상용구로 잡는다."""
    other_b = tuple(f"B 직무만의 문단 {i} 번째 내용입니다 다른 업무" for i in range(10))
    other_c = tuple(f"C 직무만의 문단 {i} 번째 내용입니다 또 다른 업무" for i in range(10))
    docs = [(INTRO,) + JD, (INTRO,) + JD, (INTRO,) + JD, (INTRO,) + other_b, (INTRO,) + other_c]
    found = find_boilerplate(docs)
    assert INTRO in found
    assert not any(line in found for line in JD)


def test_single_document_has_no_boilerplate():
    """비교 대상이 없으면 무엇도 상용구로 단정할 수 없다."""
    assert find_boilerplate([("어떤 문단이 여기 있습니다", "또 다른 문단")]) == frozenset()


def test_min_documents_catches_low_ratio_templates():
    """일부 공고에만 붙는 법무 부록은 비율 기준을 못 넘는다."""
    annex = "본 공고는 개인정보보호법 시행령 별표에 따라 처리됩니다"
    docs = [(annex, f"고유 본문 {i} 입니다") for i in range(3)]
    docs += [(f"다른 고유 본문 {i} 입니다", f"또 다른 문단 {i} 입니다") for i in range(20)]

    assert annex not in find_boilerplate(docs)
    assert annex in find_boilerplate(docs, min_documents=3)


class TestSmallSampleGuard:
    """표본이 2건이면 컷오프가 0.5*2=1.0이 되어 '한 건에만 나온 문단'까지
    상용구가 된다. 그러면 자격요건이 통째로 지워지고 요구 스킬이 0개가 된다.
    실제로 당근 공고 2건(본문 4.8KB)에서 이 일이 일어났다.
    """

    def test_two_documents_yield_no_boilerplate(self):
        docs = [
            ["Python SQL 경험이 있으신 분을 찾습니다", "회사소개 문단입니다 우리는 좋은 회사"],
            ["Tableau Amplitude 대시보드 구축 경험자", "회사소개 문단입니다 우리는 좋은 회사"],
        ]
        assert find_boilerplate(docs) == frozenset()

    def test_three_documents_do_detect_the_repeated_paragraph(self):
        shared = "회사소개 문단입니다 우리는 좋은 회사"
        docs = [[f"고유한 자격요건 문단 {i} 입니다 정말로", shared] for i in range(3)]
        assert find_boilerplate(docs) == frozenset({shared})

    def test_min_sample_is_configurable(self):
        # min_chars(20) 를 넘어야 문단으로 센다. 똑같은 공고 두 건은 복제로
        # 한 건이 되므로, 반복 문단을 공유하는 서로 다른 공고 두 건을 쓴다.
        shared = "반복되는 문단입니다 이 문장은 충분히 길어서 문단으로 셉니다"
        docs = [
            [shared, "첫 번째 공고에만 있는 고유한 문단입니다 충분히 깁니다"],
            [shared, "두 번째 공고에만 있는 고유한 문단입니다 충분히 깁니다"],
        ]
        assert find_boilerplate(docs, min_sample=2) == frozenset({shared})
