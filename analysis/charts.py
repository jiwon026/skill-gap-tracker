"""집계 결과를 README 에 넣을 SVG 로 그린다.

    .venv/Scripts/python -m analysis.charts

의존성을 늘리지 않으려고 문자열로 SVG 를 만든다. 차트 하나에 값이 열 몇 개뿐이라
그리기 라이브러리를 들일 이유가 없다. 색은 두 가지만 쓴다. 보유한 것과 아닌 것을
가르는 게 이 그림들의 목적이고, 색을 더 쓰면 그 대비가 묻힌다.
"""
from __future__ import annotations

import html
from pathlib import Path
from typing import Sequence

from analysis.market import Market, collect_market

OUT_DIR = Path(__file__).resolve().parent.parent / "docs" / "images"

INK = "#1b2430"
MUTED = "#98a2b3"
POINT = "#2f5de0"
PALE = "#c9d6f5"
LINE = "#dde2ea"

FONT = "'Malgun Gothic','Apple SD Gothic Neo','Noto Sans KR',sans-serif"


def _text(x: float, y: float, value: str, *, size: float = 12, fill: str = INK,
          anchor: str = "start", weight: str = "normal") -> str:
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-family="{FONT}" font-size="{size}" '
            f'fill="{fill}" text-anchor="{anchor}" font-weight="{weight}">{html.escape(value)}</text>')


def _svg(width: float, height: float, body: str, title: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width:.0f} {height:.0f}" '
        f'width="{width:.0f}" height="{height:.0f}" role="img" aria-label="{html.escape(title)}">'
        f'<rect width="{width:.0f}" height="{height:.0f}" fill="#ffffff"/>{body}</svg>'
    )


def demand_chart(market: Market, *, highlight: Sequence[str] = ()) -> str:
    """스킬 요구 빈도 가로 막대. 값이 긴 이름이라 가로로 눕힌다."""
    rows = market.ratios
    row_h, top, left, label_w = 26.0, 62.0, 16.0, 138.0
    width, bar_w = 720.0, 720.0 - 138.0 - 16.0 - 72.0
    height = top + row_h * len(rows) + 22

    parts = [
        _text(left, 26, "데이터 직무 공고가 요구한 스킬", size=15, weight="bold"),
        _text(left, 46, f"스냅샷 {market.days}일치, 스킬을 요구한 공고 {market.scored}건 기준",
              size=11, fill=MUTED),
    ]
    top_value = max((pct for _, _, pct in rows), default=1.0)
    for i, (name, count, pct) in enumerate(rows):
        y = top + i * row_h
        length = bar_w * (pct / top_value) if top_value else 0
        color = POINT if (not highlight or name in highlight) else PALE
        parts.append(_text(left + label_w - 8, y + 13, name, size=11.5, anchor="end"))
        parts.append(f'<rect x="{left + label_w}" y="{y + 2:.1f}" width="{length:.1f}" '
                     f'height="14" rx="2" fill="{color}"/>')
        parts.append(_text(left + label_w + length + 8, y + 13,
                           f"{pct:.0f}%  ({count}건)", size=11, fill=MUTED))
    return _svg(width, height, "".join(parts), "스킬 요구 빈도")


def funnel_chart(market: Market) -> str:
    """수집에서 지원 후보까지 줄어드는 과정. 규칙이 얼마나 걸러내는지 보인다."""
    steps = [
        ("수집한 공고", market.total, "스냅샷 누적, 중복 제거"),
        ("데이터 직무", market.data_roles, "제목과 부서로 판정"),
        ("수도권", market.metro, "제목의 지역 표기 우선"),
        ("신입 지원 가능", market.entry_level, "본문 자격 요건의 연차"),
    ]
    row_h, top, left, label_w = 46.0, 64.0, 16.0, 118.0
    width = 720.0
    bar_w = width - label_w - left - 96
    height = top + row_h * len(steps) + 16

    parts = [
        _text(left, 26, "규칙이 걸러낸 뒤 남는 공고", size=15, weight="bold"),
        _text(left, 46, f"{market.days}일 누적 {market.total}건에서 지원 후보까지", size=11, fill=MUTED),
    ]
    for i, (name, value, note) in enumerate(steps):
        y = top + i * row_h
        length = bar_w * (value / market.total) if market.total else 0
        parts.append(_text(left + label_w - 8, y + 15, name, size=11.5, anchor="end"))
        parts.append(f'<rect x="{left + label_w}" y="{y + 2:.1f}" width="{max(length, 2):.1f}" '
                     f'height="20" rx="2" fill="{POINT if i else PALE}"/>')
        parts.append(_text(left + label_w + max(length, 2) + 8, y + 16, f"{value}건", size=11.5))
        parts.append(_text(left + label_w, y + 36, note, size=10, fill=MUTED))
    return _svg(width, height, "".join(parts), "공고 필터 퍼널")


def entry_chart(market: Market) -> str:
    """데이터 직무 공고 중 신입이 지원할 수 있는 비율."""
    width, height = 720.0, 168.0
    left, bar_y, bar_h = 16.0, 78.0, 28.0
    bar_w = width - left * 2
    share = (market.entry_level / market.data_roles) if market.data_roles else 0.0
    senior = market.data_roles - market.entry_level

    parts = [
        _text(left, 26, "데이터 직무 공고 중 신입이 지원할 수 있는 것", size=15, weight="bold"),
        _text(left, 46, f"{market.days}일 누적 데이터 직무 {market.data_roles}건", size=11, fill=MUTED),
        f'<rect x="{left}" y="{bar_y}" width="{bar_w:.1f}" height="{bar_h}" rx="3" fill="{LINE}"/>',
        f'<rect x="{left}" y="{bar_y}" width="{bar_w * share:.1f}" height="{bar_h}" rx="3" fill="{POINT}"/>',
        _text(left, bar_y + bar_h + 24, f"신입 지원 가능 {market.entry_level}건 ({share * 100:.0f}%)",
              size=12, fill=POINT, weight="bold"),
        _text(width - left, bar_y + bar_h + 24, f"경력 요건 불일치 {senior}건", size=12,
              fill=MUTED, anchor="end"),
    ]
    return _svg(width, height, "".join(parts), "신입 지원 가능 비율")


def build(out_dir: Path = OUT_DIR) -> list[Path]:
    market = collect_market()
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, svg in (
        ("skill-demand.svg", demand_chart(market)),
        ("funnel.svg", funnel_chart(market)),
        ("entry-level.svg", entry_chart(market)),
    ):
        path = out_dir / name
        path.write_text(svg, encoding="utf-8")
        written.append(path)
    return written


def main() -> int:
    for path in build():
        print(f"그렸습니다: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
