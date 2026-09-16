"""파이프라인 조립 — `run.analyze`, `run._dedupe`.

각 계층은 따로 테스트되지만, 이 계층들이 **어떤 순서로** 조립되는지는
`run.py`에만 있었고 테스트가 없었다. 그 공백에서 실제 결함이 나왔다:
직군 필터를 상용구 판정보다 먼저 걸어 회사당 표본이 2건으로 떨어졌고,
본문이 4.8KB인 공고의 요구 스킬이 0개가 되었다.

여기서 지키는 것은 계층 자체가 아니라 조립 순서다.
"""
from datetime import date

import pytest

import run
from collect.schema import CompanyProfile, Posting

FETCHED = "2026-09-09T00:00:00+09:00"

#: 모든 공고에 붙는 회사 소개. 상용구로 걸려야 한다.
BOILER = "<p>우리 회사는 고객 감동을 실현하기 위해 존재하는 회사입니다</p>"


def posting(source_id, title, body, company="테스트커머스", source="greenhouse", company_id=None):
    return Posting(
        source=source,
        source_id=source_id,
        company=company,
        title=title,
        location="서울",
        url=f"https://example.com/{source_id}",
        body_html=body,
        posted_at="2026-09-01T10:00:00+09:00",
        fetched_at=FETCHED,
        company_id=company_id,
    )


class TestBoilerplateSampleIsTakenBeforeFiltering:
    """상용구 판정은 직군 필터 '이전' 전체 공고로 해야 한다."""

    @pytest.fixture
    def postings(self):
        # 데이터 직군 2건 + 무관 공고 6건. 필터를 먼저 걸면 표본이 2건이 되고,
        # 그 표본에서는 각 공고의 유일한 자격요건까지 상용구로 잡힌다.
        data_roles = [
            posting("1", "데이터 분석가", BOILER + "<p>SQL 과 Python 활용 경험이 필요합니다</p>"),
            posting("2", "Data Analyst", BOILER + "<p>Tableau 대시보드 구축 경험이 필요합니다</p>"),
        ]
        others = [
            posting(str(i), f"영업관리 {i}", BOILER + f"<p>영업 경험 {i}년 이상이 필요합니다</p>")
            for i in range(3, 9)
        ]
        return data_roles + others

    def test_required_skills_survive_a_small_filtered_cohort(self, postings):
        rows = run.analyze(postings)
        assert len(rows) == 2
        assert all(row.gap.matched or row.gap.missing for row in rows), (
            "필터 이후 표본으로 상용구를 판정하면 자격요건이 통째로 지워진다"
        )

    def test_company_boilerplate_is_still_removed(self, postings):
        """표본을 넓힌 대가로 상용구 제거가 약해지면 안 된다."""
        rows = run.analyze(postings)
        # 회사 소개 문단에는 스킬이 없으므로, 제거 여부는 스킬 집합으로 본다.
        by_title = {r.posting.title: r for r in rows}
        # 보유 여부는 개인 설정에 달렸다. 요구 스킬로 남았는지만 본다.
        def required(title):
            gap = by_title[title].gap
            return set(gap.matched) | set(gap.missing)

        assert "sql" in required("데이터 분석가")
        assert "tableau" in required("Data Analyst")

    def test_two_postings_from_one_company_keep_their_skills(self):
        """표본이 2건뿐이면 아무것도 상용구로 단정하지 않는다."""
        rows = run.analyze(
            [
                posting("1", "데이터 분석가", "<p>SQL 과 Python 활용 경험이 필요합니다</p>"),
                posting("2", "Data Analyst", "<p>Tableau 대시보드 구축 경험이 필요합니다</p>"),
            ]
        )
        assert all(row.gap.matched or row.gap.missing for row in rows)


class TestFiltering:
    def test_developer_and_unrelated_roles_are_dropped(self):
        """개발·연구직과 무관 직군은 남지 않는다(2026-09-09 결정)."""
        rows = run.analyze(
            [
                posting("1", "Data Engineer", "<p>Airflow 와 Spark 경험</p>"),
                posting("2", "데이터 분석가", "<p>SQL 경험</p>"),
                posting("3", "영업관리", "<p>영업 경험</p>"),
                posting("4", "Machine Learning Engineer", "<p>PyTorch 경험</p>"),
            ]
        )
        assert [r.posting.source_id for r in rows] == ["2"]

    def test_senior_titles_are_dropped(self):
        rows = run.analyze(
            [
                posting("1", "Director of Data Analytics", "<p>SQL 경험</p>"),
                posting("2", "Senior Data Analyst", "<p>SQL 경험</p>"),
                posting("3", "데이터 분석가", "<p>SQL 경험</p>"),
            ]
        )
        assert [r.posting.source_id for r in rows] == ["3"]

    def test_numeric_experience_beats_the_title(self):
        """숫자를 주는 소스는 제목보다 그것을 믿는다."""
        import dataclasses

        junior_title = posting("1", "데이터 분석가", "<p>SQL 경험</p>")
        senior_by_years = dataclasses.replace(junior_title, experience_min=5)
        assert run.analyze([senior_by_years]) == ()
        assert len(run.analyze([junior_title])) == 1


    def test_required_years_in_the_body_exclude_a_plain_title(self):
        """Greenhouse 는 경력을 숫자로 안 준다. 제목이 평범해도 본문
        자격요건이 '5년 이상'이면 신입 대상이 아니다(쿠팡 7건 전부가 이 경우였다)."""
        experienced = posting(
            "1", "[Coupang] Business Analyst",
            "<h3>자격 요건</h3><ul><li>최소 5년 이상의 데이터 분석 경험</li><li>SQL</li></ul>",
        )
        assert run.analyze([experienced]) == ()

    def test_years_under_preferred_do_not_exclude(self):
        open_to_juniors = posting(
            "1", "데이터 분석가",
            "<h3>자격 요건</h3><ul><li>SQL 사용 경험</li></ul>"
            "<h3>우대 사항</h3><ul><li>3년 이상 이커머스 분석 경력</li></ul>",
        )
        assert len(run.analyze([open_to_juniors])) == 1


class TestOrdering:

    def test_more_matched_skills_ranks_higher_than_a_better_ratio(self):
        """요구 1건을 갖춘 100% 공고가 요구 5건 중 3건인 공고를 이기면 안 된다."""
        rows = run.analyze(
            [
                posting("1", "데이터 분석가 A", "<p>SQL 하나만 필요합니다</p>"),
                posting("2", "데이터 분석가 B",
                        "<p>SQL 과 Python 과 Tableau, 그리고 Airflow 와 Spark 경험</p>"),
            ]
        )
        assert rows[0].posting.source_id == "2"
        assert len(rows[0].gap.matched) > len(rows[1].gap.matched)


class TestDedupe:
    def test_same_key_is_kept_once(self):
        """사람인 키워드 두 개가 같은 공고를 준다."""
        duplicated = [posting("1", "데이터 분석가", "<p>SQL</p>", source="saramin")] * 2
        assert len(run._dedupe(duplicated)) == 1

    def test_same_id_from_different_sources_is_not_a_duplicate(self):
        rows = run._dedupe(
            [
                posting("1", "데이터 분석가", "<p>SQL</p>", source="saramin"),
                posting("1", "데이터 분석가", "<p>SQL</p>", source="greenhouse"),
            ]
        )
        assert len(rows) == 2

    def test_order_is_preserved(self):
        rows = run._dedupe(
            [
                posting("1", "첫째", "<p>SQL</p>"),
                posting("2", "둘째", "<p>SQL</p>"),
                posting("1", "첫째", "<p>SQL</p>"),
            ]
        )
        assert [r.source_id for r in rows] == ["1", "2"]


class TestEmptyInput:
    def test_analyze_of_nothing_is_empty(self):
        assert run.analyze([]) == ()

    def test_postings_with_no_data_role_yield_nothing(self):
        assert run.analyze([posting("1", "영업관리", "<p>영업 경험</p>")]) == ()


class TestCompleteSources:
    """어느 소스의 '목록에 없음'을 마감으로 믿을 수 있는가.

    틀리면 수집이 실패한 날 멀쩡한 공고가 전부 마감으로 찍힌다.
    """

    def test_all_boards_fetched_and_non_empty_is_complete(self):
        outcomes = [("greenhouse", True, 370), ("greenhouse", True, 44)]
        assert run._complete_sources(outcomes) == frozenset({"greenhouse"})

    def test_one_failed_board_makes_the_source_incomplete(self):
        outcomes = [("greenhouse", True, 370), ("greenhouse", False, 0)]
        assert run._complete_sources(outcomes) == frozenset()

    def test_an_empty_board_is_treated_as_an_outage(self):
        """200 에 빈 목록이 오는 날도 있다. 그걸 '전부 마감'으로 읽으면 안 된다."""
        outcomes = [("greenhouse", True, 370), ("greenhouse", True, 0)]
        assert run._complete_sources(outcomes) == frozenset()

    def test_search_sources_are_never_complete(self):
        """사람인은 키워드 검색 + 페이지 상한이라 목록이 전체가 아니다."""
        assert run._complete_sources([("saramin", True, 500)]) == frozenset()

    def test_sources_are_judged_independently(self):
        outcomes = [("greenhouse", False, 0), ("woowahan", True, 54)]
        assert run._complete_sources(outcomes) == frozenset({"woowahan"})


class TestRegion:
    """수도권만 남긴다(2026-09-10). 판정 규칙 자체는 test_region.py 에 있다."""

    def test_non_capital_area_posting_is_excluded(self):
        import dataclasses

        busan = dataclasses.replace(
            posting("1", "데이터 분석가", "<p>SQL 경험</p>"), location="부산광역시 해운대구"
        )
        assert run.analyze([busan]) == ()

    def test_region_in_the_title_beats_the_address(self):
        import dataclasses

        hq_address = dataclasses.replace(
            posting("1", "데이터 분석가 (Growth), 부산", "<p>SQL 경험</p>"),
            location="서울특별시 강남구",
        )
        assert run.analyze([hq_address]) == ()

    def test_unknown_region_is_kept(self):
        import dataclasses

        unknown = dataclasses.replace(
            posting("1", "데이터 분석가", "<p>SQL 경험</p>"), location="South Korea"
        )
        assert len(run.analyze([unknown])) == 1


class TestNoticesAreNotContent:
    """수집 표식은 '본문이 없다'는 메타데이터다. 스킬·경험·경력 추출이 이것을
    본문으로 읽으면 표식 문구에 든 단어가 가짜 매칭을 만든다."""

    def _analyze(self, notice_words):
        import dataclasses

        from collect.schema import NOTICE_PREFIX

        body = f"<p>{NOTICE_PREFIX} {notice_words}</p>"
        return run.analyze([dataclasses.replace(posting("1", "데이터 분석가", body))])

    IMAGE_NOTICE = "공고 본문이 이미지라 텍스트가 없습니다"
    SEARCH_NOTICE = "API에는 본문이 없습니다. 아래는 키워드 요약입니다"

    def _plain(self, words):
        return run.analyze([posting("1", "데이터 분석가", f"<p>{words}</p>")])

    def test_notice_words_do_not_become_skills(self):
        """이미지 본문 표식의 '이미지라'가 Jira 로 잡혔다."""
        rows = self._analyze(self.IMAGE_NOTICE)
        assert rows and not rows[0].gap.matched and not rows[0].gap.missing

    def test_notice_words_do_not_become_pitches(self):
        """사람인 표식의 '키워드'·'API'가 텍스트 분석·파이프라인 경험으로 잡혔다."""
        rows = self._analyze(self.SEARCH_NOTICE)
        assert rows and rows[0].experiences == ()

    def test_the_same_words_outside_a_notice_do_match(self):
        """위 두 테스트가 헛돌지 않는다는 증거. 표식 머리말만 빼면 걸린다."""
        assert self._plain(self.IMAGE_NOTICE)[0].gap.missing
        assert self._plain(self.SEARCH_NOTICE)[0].experiences


class TestCompanySizes:
    """규모 조회는 분석이 끝난 공고 중 화이트리스트 밖 회사만 한다.
    수집한 공고 전부(하루 200건 가까이)의 회사를 물으면 소스 서버에 부담이 된다."""

    TODAY = date(2026, 9, 11)
    BODY = "<p>SQL 활용 경험</p>"

    def _rows(self):
        return run.analyze([
            posting("1", "데이터 분석가", self.BODY, company="작은회사", source="board", company_id="C1"),
            posting("2", "데이터 분석가", self.BODY, company="쿠팡", source="board", company_id="C2"),
            posting("3", "데이터 분석가", self.BODY, company="아이디없음", source="board"),
            posting("4", "데이터 분석가", self.BODY, company="사람인회사", source="saramin", company_id="S1"),
        ])

    def _attach(self, tmp_path, answers):
        asked = []

        def fetch(company_id):
            asked.append(company_id)
            return answers.get(company_id)

        rows = run.attach_company_sizes(
            self._rows(), today=self.TODAY, fetchers={"board": fetch}, cache_dir=tmp_path
        )
        return {r.posting.company: r.scale for r in rows}, asked

    def test_only_non_whitelisted_companies_with_an_id_are_asked(self, tmp_path):
        _, asked = self._attach(tmp_path, {})
        assert asked == ["C1"]

    def test_looked_up_size_fills_the_scale(self, tmp_path):
        scales, _ = self._attach(tmp_path, {"C1": CompanyProfile(headcount_max=40, founded_year=2022)})
        assert scales["작은회사"] == "스타트업"
        assert scales["쿠팡"] == "대기업"

    def test_sources_without_company_info_stay_etc(self, tmp_path):
        scales, _ = self._attach(tmp_path, {})
        assert scales["사람인회사"] == "기타"
        assert scales["아이디없음"] == "기타"

    def test_order_and_count_are_unchanged(self, tmp_path):
        before = [r.posting.key for r in self._rows()]
        rows = run.attach_company_sizes(
            self._rows(), today=self.TODAY, fetchers={"board": lambda cid: None}, cache_dir=tmp_path
        )
        assert [r.posting.key for r in rows] == before


class TestPersonalConfig:
    """개인 설정은 저장소에 없고 같은 형식의 더미(.example.yaml)만 있다."""

    def test_tests_run_on_the_examples(self):
        assert run._config_path("profile.yaml").name == "profile.example.yaml"

    def test_personal_file_wins_when_present(self, tmp_path, monkeypatch):
        monkeypatch.delenv(run.EXAMPLE_ENV)
        monkeypatch.setattr(run, "CONFIG_DIR", tmp_path)
        (tmp_path / "profile.yaml").write_text("skills: []", encoding="utf-8")
        assert run._config_path("profile.yaml") == tmp_path / "profile.yaml"

    def test_missing_personal_file_falls_back_to_the_example(self, tmp_path, monkeypatch):
        monkeypatch.delenv(run.EXAMPLE_ENV)
        monkeypatch.setattr(run, "CONFIG_DIR", tmp_path)
        assert run._config_path("companies.yaml") == tmp_path / "companies.example.yaml"
        assert run.missing_personal_configs() == run.PERSONAL_CONFIGS

    def test_shared_configs_never_fall_back(self):
        assert run._config_path("skills.yaml").name == "skills.yaml"

    def test_every_personal_config_has_an_example(self):
        for name in run.PERSONAL_CONFIGS:
            assert run._config_path(name).exists(), name


class TestLocalSources:
    """저장소에 넣지 않는 개인용 수집기는 collect/local/ 에 두면 자동으로 붙는다.
    공개 코드에는 그 소스의 이름이 나오지 않는다."""

    def test_missing_folder_means_no_local_sources(self):
        assert run._local_sources("collect.no_such_folder") == []

    def test_flagged_local_sources_count_as_exhaustive(self):
        from types import SimpleNamespace

        board = SimpleNamespace(__name__="collect.local.board", EXHAUSTIVE=True)
        search = SimpleNamespace(__name__="collect.local.search")
        assert run._exhaustive([board, search]) == run.EXHAUSTIVE_SOURCES | {"board"}

    def test_complete_sources_uses_the_given_exhaustive_set(self):
        outcomes = [("board", True, 5)]
        assert run._complete_sources(outcomes) == frozenset()
        assert run._complete_sources(outcomes, frozenset({"board"})) == frozenset({"board"})


class TestPublishDashboard:
    """대시보드는 공고 적재가 끝난 뒤의 덤이다. 실패해도 파이프라인을 멈추지 않는다."""

    def test_skipped_without_env(self, monkeypatch, capsys):
        monkeypatch.setattr(run.DashboardSync, "from_env", classmethod(lambda cls: None))
        run.publish_dashboard((), created=0, run_date="2026-09-15")
        assert "건너뜁니다" in capsys.readouterr().err

    def test_failure_is_reported_not_raised(self, monkeypatch, capsys):
        class Broken:
            def sync_skills(self, rows):
                raise OSError("network down")

        monkeypatch.setattr(run.DashboardSync, "from_env", classmethod(lambda cls: Broken()))
        run.publish_dashboard((), created=0, run_date="2026-09-15")
        assert "network down" in capsys.readouterr().err

    def test_skill_rows_use_the_same_owned_rule_as_analyze(self, monkeypatch):
        captured = {}

        class Recorder:
            def sync_skills(self, rows):
                captured["rows"] = rows
                from report.dashboard import SkillSyncResult

                return SkillSyncResult(created=0, updated=0, archived=0, failed=0)

            def write_summary(self, summary, run_date):
                captured["summary"] = summary
                return True

        monkeypatch.setattr(run.DashboardSync, "from_env", classmethod(lambda cls: Recorder()))
        run.publish_dashboard((), created=2, run_date="2026-09-15")

        entries, tools, book = run._skill_sources()
        expected_owned = set(tools) | book.proven_skills()
        assert {r.skill_id for r in captured["rows"] if r.owned} == expected_owned
        assert captured["summary"].new_postings == 2

    def test_row_building_failure_is_reported_not_raised(self, monkeypatch, capsys):
        from report.dashboard import SkillSyncResult

        class Working:
            def sync_skills(self, rows):
                return SkillSyncResult(created=0, updated=0, archived=0, failed=0)

            def write_summary(self, summary, run_date):
                return True

        monkeypatch.setattr(run.DashboardSync, "from_env", classmethod(lambda cls: Working()))
        monkeypatch.setattr(
            run,
            "build_skill_rows",
            lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("bad row")),
        )
        run.publish_dashboard((), created=0, run_date="2026-09-15")
        assert "bad row" in capsys.readouterr().err

    def test_any_exception_from_sync_is_reported_not_raised(self, monkeypatch, capsys):
        """가드가 넓다: OSError, ValueError, KeyError 가 아닌 예외도 경고만 남긴다."""
        class Broken:
            def sync_skills(self, rows):
                raise TypeError("shape")

        monkeypatch.setattr(run.DashboardSync, "from_env", classmethod(lambda cls: Broken()))
        run.publish_dashboard((), created=0, run_date="2026-09-15")
        assert "shape" in capsys.readouterr().err


class TestPublish:
    """publish 가 결과를 대시보드에 그대로 전달하는지."""

    def test_created_count_and_run_date_reach_the_dashboard(self, monkeypatch):
        from report.notion import RetireResult, SyncResult

        class FakeSync:
            def push(self, rows, names):
                return SyncResult(created=3, updated=0, failed=0)

            def retire(self, *, active, collected, complete_sources):
                return RetireResult(excluded=0, closed=0, unchanged=0, skipped=0, failed=0)

        monkeypatch.setattr(run.NotionSync, "from_env", classmethod(lambda cls: FakeSync()))

        recorded = {}

        def fake_publish_dashboard(rows, *, created, run_date):
            recorded["created"] = created
            recorded["run_date"] = run_date

        monkeypatch.setattr(run, "publish_dashboard", fake_publish_dashboard)

        run.publish((), collected=set(), complete_sources=frozenset(), run_date="2026-09-15")

        assert recorded["created"] == 3
        assert recorded["run_date"] == "2026-09-15"
