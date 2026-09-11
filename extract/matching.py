"""별칭 하나를 정규식으로 바꾸는 규칙.

스킬 사전과 직군 사전이 같은 문제를 푼다 — 한국어와 영문이 섞인 짧은
별칭 목록으로 본문·제목을 찾는 것. 경계 처리를 각자 구현하면 한쪽만
고쳐지는 사고가 난다. 규칙을 여기 한 벌만 둔다.
"""
from __future__ import annotations

import re


def is_ascii_edge(ch: str) -> bool:
    return ch.isascii() and (ch.isalnum() or ch in "+#")


def alias_to_pattern(alias: str) -> str:
    """별칭 하나를 정규식 조각으로 바꾼다.

    공백은 가변 공백으로 풀어 'Google  Analytics'와 '데이터엔지니어링'을
    함께 잡고, 양끝이 아스키 문자일 때만 단어 경계를 붙인다. 한글에
    단어 경계를 붙이면 조사 때문에 미탐이 난다.
    """
    parts = alias.split()
    if not parts:
        # 빈 패턴은 무엇에나 매칭된다. 사전에 공백 별칭 하나가 섞이면
        # 그 항목이 모든 공고에 걸리고, 예외도 로그도 남지 않는다.
        raise ValueError(f"비어 있는 별칭: {alias!r}")

    body = r"\s*".join(re.escape(part) for part in parts)
    prefix = r"(?<![A-Za-z0-9])" if is_ascii_edge(alias[0]) else ""
    suffix = r"(?![A-Za-z0-9])" if is_ascii_edge(alias[-1]) else ""
    return f"{prefix}{body}{suffix}"
