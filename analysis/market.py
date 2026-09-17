"""누적 스냅샷으로 채용 시장을 집계한다.

파이프라인은 '오늘 지원할 공고'를 고르지만, 여기서는 쌓인 스냅샷 전체로
'이 직무가 무엇을 요구하나'를 본다. 스냅샷을 원본 그대로 남겨 둔 덕에
집계 기준을 바꿔도 다시 계산할 수 있다.

    .venv/Scripts/python -m analysis.market

공개해도 되는 것만 센다. 소스 이름과 회사 이름은 내지 않는다.
"""
from __future__ import annotations

import collections
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import run
from collect.schema import Posting, is_notice
from collect.snapshot import read_snapshot
from extract.boilerplate import find_boilerplate, strip_boilerplate
from extract.normalize import to_segments
from extract.region import RegionRule, in_region
from extract.relevance import RelevanceDict, classify
from extract.seniority import SeniorityRule, is_entry_level, years_from_requirements
from extract.skills import SkillDict, extract_skills

SNAPSHOT_DIR = run.ROOT / "store" / "snapshots"

#: 표에 낼 스킬 개수. 꼬리가 길어서 전부 내면 읽히지 않는다.
TOP_N = 12


@dataclass(frozen=True, slots=True)
class Market:
    """집계 결과. 숫자만 담는다."""

    days: int
    total: int
    companies: int
    data_roles: int
    metro: int
    entry_level: int
    scored: int
    skills_per_posting: float
    demand: tuple[tuple[str, int], ...]

    @property
    def ratios(self) -> tuple[tuple[str, int, float], ...]:
        return ratios(self.demand, self.scored)


def ratios(demand: Sequence[tuple[str, int]], scored: int) -> tuple[tuple[str, int, float], ...]:
    """(스킬, 건수, 비율%). 분모가 0이면 비율도 0 으로 둔다.

    분모는 '스킬을 하나 이상 요구한 공고' 다. 본문을 주지 않는 소스를 분모에
    넣으면 '요구가 없어서 0개' 와 '읽을 본문이 없어서 0개' 가 뒤섞인다.
    """
    if scored <= 0:
        return tuple((name, count, 0.0) for name, count in demand)
    return tuple((name, count, count / scored * 100) for name, count in demand)


def unique_postings(directory: Path = SNAPSHOT_DIR) -> tuple[list[Posting], int]:
    """스냅샷 전체를 훑어 유니크 공고와 스냅샷 날짜 수를 돌려준다.

    같은 공고가 여러 날 잡히면 마지막 것을 쓴다. 공고는 수정되기도 한다.
    """
    latest: dict[str, Posting] = {}
    files = sorted(directory.glob("*.jsonl"))
    for path in files:
        for posting in read_snapshot(path):
            latest[posting.key] = posting
    return list(latest.values()), len(files)


def summarize(postings: Sequence[Posting], *, days: int, top_n: int = TOP_N) -> Market:
    """공고 묶음을 집계한다. 파이프라인과 같은 판정 규칙, 같은 순서를 쓴다."""
    relevance = RelevanceDict.from_config(run._config("relevance.yaml"))
    region_rule = RegionRule.from_config(run._config("region.yaml"))
    seniority = SeniorityRule.from_config(run._config("seniority.yaml"))
    entries = run._config("skills.yaml")["skills"]
    names = {s["id"]: s.get("name", s["id"]) for s in entries}
    dictionary = SkillDict.from_entries(entries)

    # 상용구 판정은 직군 필터 이전 전체 공고로 한다(run.analyze 와 같은 이유).
    segments_by_key: dict[str, tuple[str, ...]] = {}
    corpus: dict[str, list[tuple[str, ...]]] = {}
    for posting in postings:
        segments = tuple(s for s in to_segments(posting.body_html) if not is_notice(s))
        segments_by_key[posting.key] = segments
        corpus.setdefault(posting.company, []).append(segments)
    boilerplate = {company: find_boilerplate(docs) for company, docs in corpus.items()}

    data_roles: list[Posting] = []
    metro = entry_level = 0
    for posting in postings:
        if not classify(posting.title, posting.departments, relevance).relevant:
            continue
        data_roles.append(posting)
        if in_region(posting.title, posting.location, region_rule):
            metro += 1
        stated = (
            years_from_requirements(segments_by_key[posting.key])
            if posting.experience_min is None
            else None
        )
        if is_entry_level(posting.title, posting.experience_min, seniority, stated_years=stated):
            entry_level += 1

    demand: collections.Counter[str] = collections.Counter()
    scored = 0
    required_counts: list[int] = []
    for posting in data_roles:
        segments = segments_by_key[posting.key]
        if not segments:
            continue
        text = " ".join(strip_boilerplate(segments, boilerplate.get(posting.company, frozenset())))
        required = extract_skills(text, dictionary)
        if not required:
            continue
        scored += 1
        required_counts.append(len(required))
        demand.update(required)

    return Market(
        days=days,
        total=len(postings),
        companies=len({p.company for p in postings}),
        data_roles=len(data_roles),
        metro=metro,
        entry_level=entry_level,
        scored=scored,
        skills_per_posting=(sum(required_counts) / len(required_counts)) if required_counts else 0.0,
        demand=tuple((names.get(i, i), n) for i, n in demand.most_common(top_n)),
    )


def collect_market(directory: Path = SNAPSHOT_DIR, *, top_n: int = TOP_N) -> Market:
    postings, days = unique_postings(directory)
    return summarize(postings, days=days, top_n=top_n)


def as_lines(market: Market) -> list[str]:
    share = (market.entry_level / market.data_roles * 100) if market.data_roles else 0.0
    lines = [
        f"스냅샷 {market.days}일치",
        f"유니크 공고 {market.total}건, {market.companies}개사",
        f"데이터 직무 {market.data_roles}건",
        f"  수도권 {market.metro}건",
        f"  신입 지원 가능 {market.entry_level}건 (데이터 직무의 {share:.0f}%)",
        f"스킬을 요구한 공고 {market.scored}건, 공고당 평균 {market.skills_per_posting:.1f}개",
        "",
        f"요구 빈도 상위 {len(market.demand)} (분모 {market.scored}건)",
    ]
    lines += [f"  {name:22s} {count:4d}건  {pct:5.1f}%" for name, count, pct in market.ratios]
    return lines


def main() -> int:
    for line in as_lines(collect_market()):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
