"""회사 규모 판정.

Notion '규모' 열은 화이트리스트(companies.yaml)가 아는 회사만 대기업·중견으로
나오고 나머지는 전부 '기타'였다. 소스가 주는 회사 정보로 나머지를 가른다.

    소스가 법정 규모(대기업·중견·중소)를 주면 그대로 쓴다.
    없으면 사원수 300명 이하를 중소로 본다. 그 이상은 모른다.
    중소 중 설립 10년 미만은 스타트업이다.

사원수로 중견·대기업을 추정하지 않는 이유는 법정 규모가 매출·자산 기준이라
사원수와 어긋나기 때문이다. 300명은 반대 방향으로는 안전하다 — 300명 이하
중견·대기업은 드물고, 그런 회사는 대개 법정 규모를 주는 소스에 있다.

'스타트업' 태그처럼 회사가 스스로 붙이는 표시는 쓰지 않는다. 2004년에
설립된 회사에도 붙어 있다.
"""
from __future__ import annotations

from collect.schema import CompanyProfile

#: 큰 것부터. Notion select 는 옵션 순서대로 정렬되므로 이 순서가 곧
#: Notion 에서의 정렬 순서다.
SIZES = ("대기업", "중견", "중소", "스타트업")
#: 규모를 알 수 없을 때.
UNKNOWN = "기타"

#: 사원수만 알 때 중소로 보는 상한.
SME_MAX_HEADCOUNT = 300
#: 설립 후 이 햇수 미만인 중소기업이 스타트업이다. 채용 사이트들이 흔히 쓰는
#: 설립 구간('설립4~9년' / '설립10년이상')과 경계가 같다.
STARTUP_MAX_AGE = 10


def classify_size(profile: CompanyProfile, *, current_year: int) -> str | None:
    """회사 정보 → 규모. 판단할 수 없으면 None 이다. 순수 함수다."""
    size = profile.size_class
    if size is None and profile.headcount_max is not None:
        if profile.headcount_max <= SME_MAX_HEADCOUNT:
            size = "중소"
    if (
        size == "중소"
        and profile.founded_year is not None
        and current_year - profile.founded_year < STARTUP_MAX_AGE
    ):
        return "스타트업"
    return size
