"""공고 본문 HTML을 사전 매칭이 가능한 평문으로 바꾼다.

블록 태그는 문단 경계로, 인라인 태그는 흔적 없이 지운다. 블록을 그냥 지우면
'Python'과 'SQL'이 붙어 'PythonSQL'이라는 없는 토큰이 생기고, 인라인에
공백을 넣으면 '<b>Amp</b>litude'가 두 단어로 쪼개진다. 둘 다 오탐의 원인이다.
"""
from __future__ import annotations

import html
import re

_BLOCK_TAGS = (
    "p|div|br|li|ul|ol|dl|dt|dd|tr|td|th|table|thead|tbody|section|article"
    "|h[1-6]|blockquote|pre|hr|figure|figcaption|header|footer|nav|form|fieldset"
)

_DROP_ELEMENTS = re.compile(rf"<(script|style)\b[^>]*>.*?</\1\s*>", re.I | re.S)
_BLOCK = re.compile(rf"</?(?:{_BLOCK_TAGS})\b[^>]*>", re.I)
_ANY_TAG = re.compile(r"<[^>]*>")
_COMMENT = re.compile(r"<!--.*?-->", re.S)
_INLINE_SPACE = re.compile(r"[^\S\n]+|[\u00a0\u200b]+")


def to_segments(html_source: str | None) -> tuple[str, ...]:
    """블록 단위로 쪼갠 평문 문단.

    상용구 탐지가 문단 단위로 이뤄지므로 블록 경계를 잃지 않고 남긴다.
    """
    if not html_source:
        return ()

    text = _COMMENT.sub(" ", html_source)
    text = _DROP_ELEMENTS.sub(" ", text)
    text = _BLOCK.sub("\n", text)
    text = _ANY_TAG.sub("", text)
    text = html.unescape(text)

    segments = (_INLINE_SPACE.sub(" ", part).strip() for part in text.split("\n"))
    return tuple(s for s in segments if s)


def to_text(html_source: str | None) -> str:
    """HTML 본문을 한 줄 평문으로 정제한다. 순수 함수이며 네트워크를 모른다."""
    return " ".join(to_segments(html_source))
