"""HTML 공고 본문을 사전 매칭이 가능한 평문으로 바꾼다.

여기서 원문의 단어 경계가 뭉개지면 추출 단계에서 오탐이 난다.
블록 태그가 사라지면서 앞뒤 단어가 붙어버리는 것이 가장 흔한 사고다.
"""
import pytest

from extract.normalize import to_text


def test_strips_tags():
    assert to_text("<p>SQL, Python</p>") == "SQL, Python"


def test_block_tags_become_boundaries():
    """붙어버리면 'PythonSQL' 같은 없는 토큰이 생긴다."""
    assert to_text("<li>Python</li><li>SQL</li>") == "Python SQL"
    assert to_text("<p>Python</p><div>SQL</div>") == "Python SQL"


def test_br_is_a_boundary():
    assert to_text("Python<br/>SQL") == "Python SQL"


def test_decodes_entities():
    assert to_text("<p>A&amp;B&nbsp;C</p>") == "A&B C"


def test_drops_script_and_style_content():
    assert to_text("<style>.a{color:red}</style><p>SQL</p>") == "SQL"
    assert to_text("<script>var x='Python'</script><p>SQL</p>") == "SQL"


def test_collapses_whitespace():
    assert to_text("<p>SQL   \n\n  Python</p>") == "SQL Python"


def test_preserves_inline_words():
    """인라인 태그는 단어를 쪼개면 안 된다."""
    assert to_text("<p><b>Amp</b>litude</p>") == "Amplitude"


@pytest.mark.parametrize("value", ["", None])
def test_empty_input_is_empty_output(value):
    assert to_text(value) == ""
