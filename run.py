"""Skill Gap Tracker 파이프라인 진입점.

수집 → 스냅샷 → 직군 필터 → 상용구 제거 → 스킬 갭 → Notion 적재.

스냅샷은 필터 이전의 원본을 담는다. 사전을 고치면 과거 스냅샷으로 다시
돌릴 수 있어야 하기 때문이다. 좁히는 일은 전부 스냅샷 이후에 한다.

소스 하나가 죽어도 나머지 결과로 리포트가 나와야 하므로, 소스 단위로
예외를 가둔다. 자격증명이 없는 소스(사람인)와 목적지(Notion)는 경고만
남기고 건너뛴다 — 키 발급을 기다리는 동안에도 파이프라인이 돌아야 한다.
"""
from __future__ import annotations

import importlib
import os
import pkgutil
import sys
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType
from typing import Callable, Iterable, Mapping

import yaml

from analyze.gap import AnalyzedPosting, compute_gap
from analyze.priority import assign_priority, priority_rank
from collect import greenhouse, saramin, woowahan

#: 저장소에 넣지 않는 개인용 수집기를 두는 패키지(.gitignore). 모듈마다
#: DEFAULT_SPEC, fetch_payload(spec, cache), parse_board, LABEL 을 갖고,
#: payload["complete"] 로 목록을 끝까지 봤는지 알린다. 선택 속성:
#: EXHAUSTIVE(목록이 전체인가), fetch_company(company_id)(규모 조회).
#: 모듈 이름이 곧 Posting.source 다.
LOCAL_PACKAGE = "collect.local"


def _local_sources(package: str = LOCAL_PACKAGE) -> list[ModuleType]:
    """개인용 수집기 목록. 폴더가 없으면 빈 목록이고 공개 소스만으로 돈다.

    폴더는 있는데 그 안의 import 가 깨졌으면 그대로 올린다 — 조용히 건너뛰면
    그 소스가 왜 비었는지 알 수 없다.
    """
    try:
        root = importlib.import_module(package)
    except ModuleNotFoundError as exc:
        if exc.name is not None and package.startswith(exc.name):
            return []
        raise
    return [
        importlib.import_module(f"{package}.{info.name}")
        for info in pkgutil.iter_modules(root.__path__)
        if not info.name.startswith("_")
    ]


def _source_name(module: ModuleType) -> str:
    return module.__name__.rsplit(".", 1)[-1]
from collect import company_cache
from collect.schema import CompanyProfile, Posting, is_notice
from collect.snapshot import write_snapshot
from extract.boilerplate import find_boilerplate, strip_boilerplate
from extract.companies import CompanyBook
from extract.company_size import classify_size
from extract.experience import ExperienceBook, match_experiences
from extract.normalize import to_segments
from extract.region import RegionRule, in_region
from extract.relevance import RelevanceDict, classify
from extract.seniority import SeniorityRule, is_entry_level, years_from_requirements
from extract.skills import SkillDict, extract_skills
from report.notion import NotionSync

ROOT = Path(__file__).parent
KST = timezone(timedelta(hours=9))


CONFIG_DIR = ROOT / "config"
#: 개인 설정. 저장소에는 같은 형식의 더미(<이름>.example.yaml)만 있다.
PERSONAL_CONFIGS = ("profile.yaml", "experience.yaml", "companies.yaml")
#: 이 환경변수가 있으면 개인 설정이 있어도 더미로 돈다. 테스트가 쓴다 —
#: 테스트 결과가 누구의 이력이 들어 있느냐에 따라 달라지면 안 된다.
EXAMPLE_ENV = "SKILL_GAP_EXAMPLE_CONFIG"


def _config_path(name: str) -> Path:
    path = CONFIG_DIR / name
    if name in PERSONAL_CONFIGS and (os.environ.get(EXAMPLE_ENV) or not path.exists()):
        return CONFIG_DIR / f"{Path(name).stem}.example.yaml"
    return path


def missing_personal_configs() -> tuple[str, ...]:
    """더미로 대신 도는 개인 설정. 실행 시 경고로 알린다."""
    return tuple(name for name in PERSONAL_CONFIGS if _config_path(name).name != name)


def _config(name: str) -> dict:
    return yaml.safe_load(_config_path(name).read_text(encoding="utf-8")) or {}


def _enabled(entries: Iterable[dict]) -> list[dict]:
    return [e for e in entries if e.get("enabled", True)]


def _raw_cache(name: str, run_date: str) -> Path:
    return ROOT / "store" / "raw" / f"{name}-{run_date}.json"


#: 전체 목록을 주는 소스. 여기서 공고가 사라졌다면 마감으로 볼 수 있다.
#: 사람인은 키워드 검색이고 페이지 상한이 있어서, 목록에 없다고 마감이 아니다.
#: 우아한형제들은 MAX_PAGES(20) 안에서 끝까지 훑는다 — 현재 54건이라 여유가
#: 크지만, 공고가 그 상한을 넘으면 뒷장 공고가 마감으로 찍히므로 다시 볼 것.
#: 개인용 수집기는 EXHAUSTIVE 로 스스로 밝히고, 목록을 끝까지 못 본 날은
#: complete=False 를 줘서 그날 실패로 기록된다.
EXHAUSTIVE_SOURCES = frozenset({"greenhouse", "woowahan"})


def _exhaustive(local_modules: Iterable[ModuleType]) -> frozenset[str]:
    return EXHAUSTIVE_SOURCES | {
        _source_name(m) for m in local_modules if getattr(m, "EXHAUSTIVE", False)
    }


def _complete_sources(
    outcomes: Iterable[tuple[str, bool, int]],
    exhaustive: frozenset[str] = EXHAUSTIVE_SOURCES,
) -> frozenset[str]:
    """(소스, 수집 성공 여부, 건수) 목록에서 '목록이 완전한' 소스를 고른다.

    보드 하나라도 실패하면 그 소스 전체를 불완전으로 본다. 수집이 성공했는데
    0건인 것도 장애로 본다 — 200 에 빈 목록이 오는 날 그 소스의 공고가 전부
    마감으로 찍히면 안 된다.
    """
    by_source: dict[str, list[tuple[bool, int]]] = {}
    for source, ok, count in outcomes:
        by_source.setdefault(source, []).append((ok, count))
    return frozenset(
        source
        for source, rows in by_source.items()
        if source in exhaustive and all(ok and count > 0 for ok, count in rows)
    )


def collect(run_date: str, fetched_at: str) -> tuple[tuple[Posting, ...], frozenset[str]]:
    """공고와, 목록이 완전하게 수집된 소스 집합을 함께 돌려준다."""
    sources = _config("sources.yaml")
    collected: list[Posting] = []
    outcomes: list[tuple[str, bool, int]] = []

    for entry in _enabled(sources.get("greenhouse", ())):
        spec = greenhouse.BoardSpec(**entry)
        try:
            payload = greenhouse.fetch_payload(spec, _raw_cache(f"greenhouse-{spec.token}", run_date))
        except OSError as exc:
            print(f"  ! {spec.token}: 수집 실패, 건너뜁니다 — {exc}", file=sys.stderr)
            outcomes.append(("greenhouse", False, 0))
            continue
        result = greenhouse.parse_board(payload, spec, fetched_at=fetched_at)
        outcomes.append(("greenhouse", True, len(result.postings) + len(result.skipped)))
        collected.extend(result.postings)
        _report_source(spec.company, result)

    for entry in _enabled(sources.get("woowahan", ())):
        spec = woowahan.BoardSpec(**entry)
        try:
            payload = woowahan.fetch_payload(spec, _raw_cache("woowahan", run_date))
        except OSError as exc:
            print(f"  ! 우아한형제들: 수집 실패, 건너뜁니다 — {exc}", file=sys.stderr)
            outcomes.append(("woowahan", False, 0))
            continue
        result = woowahan.parse_board(payload, spec, fetched_at=fetched_at)
        outcomes.append(("woowahan", True, len(result.postings) + len(result.skipped)))
        collected.extend(result.postings)
        _report_source(spec.company, result)

    local_modules = _local_sources()
    for module in local_modules:
        name, label = _source_name(module), getattr(module, "LABEL", _source_name(module))
        try:
            payload = module.fetch_payload(module.DEFAULT_SPEC, _raw_cache(name, run_date))
        except OSError as exc:
            print(f"  ! {label}: 수집 실패, 건너뜁니다 — {exc}", file=sys.stderr)
            outcomes.append((name, False, 0))
            continue
        result = module.parse_board(payload, module.DEFAULT_SPEC, fetched_at=fetched_at)
        # 목록을 끝까지 못 봤으면 '없음'을 마감으로 믿을 수 없다.
        outcomes.append((name, bool(payload.get("complete")), len(result.postings) + len(result.skipped)))
        collected.extend(result.postings)
        _report_source(label, result)

    access_key = saramin.access_key_from_env()
    saramin_specs = _enabled(sources.get("saramin", ()))
    if saramin_specs and not access_key:
        print("  - 사람인: SARAMIN_ACCESS_KEY 가 없어 건너뜁니다", file=sys.stderr)
    elif access_key:
        for entry in saramin_specs:
            spec = saramin.KeywordSpec(**entry)
            slug = spec.keyword.replace(" ", "")
            try:
                payload = saramin.fetch_payload(
                    spec, _raw_cache(f"saramin-{slug}", run_date), access_key
                )
            except OSError as exc:
                print(f"  ! 사람인/{spec.keyword}: 수집 실패 — {exc}", file=sys.stderr)
                continue
            result = saramin.parse_search(payload, spec, fetched_at=fetched_at)
            collected.extend(result.postings)
            _report_source(f"사람인 '{spec.keyword}'", result)

    return _dedupe(collected), _complete_sources(outcomes, _exhaustive(local_modules))


def _dedupe(postings: Iterable[Posting]) -> tuple[Posting, ...]:
    """같은 key를 한 번만 남긴다.

    사람인은 키워드마다 따로 검색하므로 '데이터 분석'과 '데이터 분석가'가
    같은 공고를 함께 준다. 중복을 그대로 두면 Notion이 같은 공고에 대해
    create를 두 번 하고(쿼리 인덱스가 즉시 갱신되지 않는다), 그중 하나에
    사용자가 적어 둔 '상태'가 다음 실행부터 보이지 않게 된다.
    """
    seen: set[str] = set()
    unique: list[Posting] = []
    for posting in postings:
        if posting.key in seen:
            continue
        seen.add(posting.key)
        unique.append(posting)

    dropped = len(list(postings)) - len(unique) if isinstance(postings, list) else 0
    if dropped:
        print(f"  - 중복 {dropped}건 제거 (소스·키워드 간 겹침)")
    return tuple(unique)


def _report_source(label: str, result) -> None:
    note = f" (검증 실패 {len(result.skipped)}건)" if result.skipped else ""
    print(f"  {label:18s} {len(result.postings):4d}건{note}")


def analyze(postings: Iterable[Posting]) -> tuple[AnalyzedPosting, ...]:
    """직군으로 좁히고, 회사를 묶고, 스킬 갭을 계산한다."""
    relevance = RelevanceDict.from_config(_config("relevance.yaml"))
    book = CompanyBook.from_config(_config("companies.yaml"))
    skill_entries = _config("skills.yaml")["skills"]
    dictionary = SkillDict.from_entries(skill_entries)

    # 보유 스킬 = profile.yaml 의 도구 + 프로젝트가 증명하는 것.
    # 후자를 빠뜨리면 실제로 해 본 분석까지 '부족'으로 잡혀 갭이 나쁘게 나온다.
    experience_book = ExperienceBook.from_config(_config("experience.yaml"))
    experience_book.validate_against({s["id"] for s in skill_entries})
    owned = {s["id"] for s in _config("profile.yaml")["skills"]} | experience_book.proven_skills()

    # 상용구 판정은 직군 필터 '이전' 전체 공고로 한다. 필터를 먼저 걸면 회사당
    # 표본이 2~6건으로 떨어지고, 그 표본에서는 실제 자격요건까지 반복으로
    # 보여 본문이 통째로 지워진다(당근 2건에서 요구 스킬 0개로 확인).
    segments_by_key: dict[str, tuple[str, ...]] = {}
    corpus: dict[str, list[tuple[str, ...]]] = {}
    for posting in postings:
        # 수집 표식은 '본문이 없다'는 메타데이터다. 본문으로 읽으면 표식 문구의
        # 단어가 가짜 스킬·어필을 만든다(collect/schema.py NOTICE_PREFIX).
        segments = tuple(s for s in to_segments(posting.body_html) if not is_notice(s))
        segments_by_key[posting.key] = segments
        corpus.setdefault(posting.company, []).append(segments)

    boilerplate_by_company = {
        company: find_boilerplate(documents) for company, documents in corpus.items()
    }

    seniority = SeniorityRule.from_config(_config("seniority.yaml"))
    region = RegionRule.from_config(_config("region.yaml"))

    by_company: dict[str, list[Posting]] = {}
    skipped_senior = 0
    skipped_region = 0
    for posting in postings:
        verdict = classify(posting.title, posting.departments, relevance)
        if not verdict.relevant:
            continue
        if not in_region(posting.title, posting.location, region):
            skipped_region += 1
            continue
        # 경력을 숫자로 주지 않는 소스(Greenhouse)는 본문 자격요건에서 읽는다.
        stated = (
            years_from_requirements(segments_by_key[posting.key])
            if posting.experience_min is None
            else None
        )
        if not is_entry_level(
            posting.title, posting.experience_min, seniority, stated_years=stated
        ):
            skipped_senior += 1
            continue
        by_company.setdefault(posting.company, []).append(posting)

    if skipped_senior:
        print(f"  - 경력 요건이 맞지 않아 제외 {skipped_senior}건")
    if skipped_region:
        print(f"  - 수도권이 아니어서 제외 {skipped_region}건")

    analyzed: list[AnalyzedPosting] = []
    for company_raw, rows in by_company.items():
        boilerplate = boilerplate_by_company.get(company_raw, frozenset())
        company = book.identify(company_raw)

        for posting in rows:
            segments = segments_by_key[posting.key]
            text = " ".join(strip_boilerplate(segments, boilerplate))
            required = extract_skills(text, dictionary)
            analyzed.append(
                AnalyzedPosting(
                    posting=posting,
                    company=company,
                    gap=compute_gap(required, owned),
                    experiences=match_experiences(posting.title, text, experience_book),
                )
            )

    # 우선순위를 맨 앞에 둔다. 콘솔 순서와 Notion 의 우선순위 열이 서로
    # 다른 말을 하면 어느 쪽도 믿을 수 없게 된다. 나머지는 같은 등급 안의
    # 정렬이다.
    #
    # coverage만으로 정렬하면 요구 스킬이 한두 개뿐인 공고가 100%로 맨 위에
    # 올라온다. 실제로 겹치는 스킬 수를 먼저 보고, 같을 때 비율로 가른다.
    analyzed.sort(
        key=lambda a: (
            priority_rank(assign_priority(a)),
            book.tier_rank(a.company.tier if a.company else None),
            # 자소서에 쓸 게 있는 공고를 먼저 본다. 신입에게는 스킬 개수보다
            # 어필 근거가 있는지가 지원 여부를 가른다.
            -len(a.project_experiences),
            -len(a.gap.matched),
            -a.gap.coverage,
        )
    )
    return tuple(analyzed)


def _company_fetchers() -> dict[str, Callable[[str], CompanyProfile]]:
    """회사 정보를 주는 소스 — 개인용 수집기 중 fetch_company 가 있는 것."""
    return {
        _source_name(m): m.fetch_company
        for m in _local_sources()
        if callable(getattr(m, "fetch_company", None))
    }


def attach_company_sizes(
    analyzed: Iterable[AnalyzedPosting],
    *,
    today: date,
    fetchers: Mapping[str, Callable[[str], CompanyProfile | None]] | None = None,
    cache_dir: Path | None = None,
) -> tuple[AnalyzedPosting, ...]:
    """화이트리스트 밖 회사의 규모를 소스 회사 정보로 채운다.

    분석이 끝난 공고만 묻는다. 수집한 공고 전부의 회사를 물으면 하루 수백
    건이 되는데, 규모가 필요한 것은 Notion 에 오르는 십여 건뿐이다. 우선순위와
    정렬은 건드리지 않는다 — 타겟 회사 여부는 화이트리스트가 정한다.
    """
    rows = tuple(analyzed)
    fetchers = _company_fetchers() if fetchers is None else fetchers
    cache_dir = cache_dir or ROOT / "store" / "companies"

    pending: dict[str, list[str]] = {}
    for row in rows:
        source, company_id = row.posting.source, row.posting.company_id
        if row.company is None and company_id and source in fetchers:
            pending.setdefault(source, []).append(company_id)

    sizes: dict[tuple[str, str], str] = {}
    for source, ids in pending.items():
        profiles = company_cache.lookup(ids, fetchers[source], cache_dir / f"{source}.json", today=today)
        for company_id, profile in profiles.items():
            size = classify_size(profile, current_year=today.year)
            if size:
                sizes[(source, company_id)] = size

    return tuple(
        replace(row, company_size=sizes[key])
        if (key := (row.posting.source, row.posting.company_id or "")) in sizes
        else row
        for row in rows
    )


def publish(
    analyzed: Iterable[AnalyzedPosting],
    *,
    collected: Iterable[str],
    complete_sources: frozenset[str],
) -> None:
    rows = tuple(analyzed)
    names = {s["id"]: s.get("name", s["id"]) for s in _config("skills.yaml")["skills"]}

    print(f"\n[분석] 대상 {len(rows)}건")
    for row in rows[:25]:
        scale = row.scale
        missing = ", ".join(row.gap.labeled(names, "missing")[:4]) or "-"
        required = len(row.gap.matched) + len(row.gap.missing)
        # 비율만 쓰면 '요구 1개 중 1개'가 100%로 보인다. 분모를 함께 낸다.
        print(
            f"  [{assign_priority(row)}] {scale:4s} {row.company_name[:10]:12s}"
            f" {row.posting.title[:38]:40s}"
            f" {len(row.gap.matched):2d}/{required:<2d}"
            f" · 어필: {', '.join(m.name for m in row.experiences[:2]) or '-'}"
        )
    if len(rows) > 25:
        print(f"  … 외 {len(rows) - 25}건")

    sync = NotionSync.from_env()
    if sync is None:
        print("\n[Notion] NOTION_TOKEN / NOTION_DATABASE_ID 가 없어 건너뜁니다", file=sys.stderr)
        return

    result = sync.push(rows, names)
    print(f"\n[Notion] 신규 {result.created} · 갱신 {result.updated} · 실패 {result.failed}")

    # push 뒤에 한다. 오늘 결과에 든 행은 push 가 모집중으로 이미 썼다.
    retired = sync.retire(
        active={row.posting.key for row in rows},
        collected=collected,
        complete_sources=complete_sources,
    )
    print(
        f"[Notion] 공고 현황 — 제외됨 {retired.excluded} · 마감 {retired.closed}"
        f" · 변화 없음 {retired.unchanged} · 판단 보류 {retired.skipped}"
        f" · 실패 {retired.failed}"
    )
    if complete_sources:
        print(f"         (마감 판정한 소스: {', '.join(sorted(complete_sources))})")


def _force_utf8_output() -> None:
    """Windows 콘솔 기본 코덱(cp949)이 한글·기호에서 터지는 것을 막는다."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    _force_utf8_output()
    run_date = datetime.now(KST).date().isoformat()
    fetched_at = datetime.now(KST).isoformat(timespec="seconds")
    print(f"[수집] {run_date}")
    missing = missing_personal_configs()
    if missing:
        print(
            f"  ! 개인 설정이 없어 더미(.example.yaml)로 돕니다: {', '.join(missing)}"
            " — config/ 에 복사해 채우세요",
            file=sys.stderr,
        )

    postings, complete_sources = collect(run_date, fetched_at)
    if not postings:
        print("수집된 공고가 없습니다.", file=sys.stderr)
        return 1

    snapshot = ROOT / "store" / "snapshots" / f"{run_date}.jsonl"
    print(f"[스냅샷] {snapshot.relative_to(ROOT)} — {write_snapshot(postings, snapshot)}건")

    publish(
        attach_company_sizes(analyze(postings), today=datetime.now(KST).date()),
        collected={posting.key for posting in postings},
        complete_sources=complete_sources,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
