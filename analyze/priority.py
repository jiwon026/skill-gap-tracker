"""지원 우선순위.

Notion에서 무엇부터 지원할지 가르는 등급이다. 규칙은 한 문장이다.

    어필할 프로젝트 수로 정하고, 타겟 회사가 아니면 한 단계 내린다.

프로젝트 수를 기준으로 삼는 이유는 신입의 지원 여부를 가르는 것이 스킬
개수가 아니라 자소서에 쓸 근거가 있느냐이기 때문이다. 스킬 매칭은 등급을
정하지 않고, 같은 등급 안의 정렬에만 쓴다 — 매칭 스킬 몇 개부터 '높음'인지
정할 객관적 기준이 없어서, 임계값을 두면 근거 없는 정밀도가 생긴다.

활동 경험은 세지 않는다. 동아리·아르바이트 같은 경험은 거의 모든 공고에
걸려서, 세면 전부 높음이 된다.

타겟 회사 여부는 등급을 올리지 않고 내리기만 한다.
"""
from __future__ import annotations

from analyze.gap import AnalyzedPosting

#: 높은 것부터. Notion select 는 옵션 순서대로 정렬되므로 이 순서가 곧
#: Notion 에서의 정렬 순서다.
PRIORITIES = ("높음", "중간", "낮음")

#: 이 수 이상의 프로젝트로 어필할 수 있으면 '높음'에서 출발한다.
_STRONG_PITCH = 2


def _base_level(project_count: int) -> int:
    if project_count >= _STRONG_PITCH:
        return 0
    if project_count >= 1:
        return 1
    return 2


def assign_priority(analyzed: AnalyzedPosting) -> str:
    """공고 한 건의 우선순위 등급. 순수 함수다."""
    level = _base_level(len(analyzed.project_experiences))
    if analyzed.company is None:
        level += 1
    return PRIORITIES[min(level, len(PRIORITIES) - 1)]


def priority_rank(label: str) -> int:
    """정렬 키. 높음이 0이다."""
    return PRIORITIES.index(label)
