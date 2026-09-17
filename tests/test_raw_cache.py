"""공고 원본 캐시의 뒷정리.

`store/raw/{소스}-{날짜}.json` 은 같은 날 다시 돌릴 때만 읽는다(collect 의
fetch_payload 가 오늘 날짜 파일만 본다). 지난 날 파일은 영영 안 읽히지만,
수집 필터가 걸러낸 공고까지 들어 있어 나중에 직무 범위를 넓히면 그때 과거를
다시 뽑을 수 있는 유일한 자료다. 그래서 지우지 않고 압축해 둔다.
"""
import gzip
import json

from collect.raw_cache import compress_old


def _write(folder, name, payload):
    path = folder / name
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


class TestCompressOld:
    def test_yesterdays_file_becomes_a_gzip_and_the_original_goes(self, tmp_path):
        _write(tmp_path, "catch-2026-09-16.json", {"jobs": [1, 2, 3]})
        assert compress_old(tmp_path, "2026-09-17") == 1

        gz = tmp_path / "catch-2026-09-16.json.gz"
        assert gz.exists()
        assert not (tmp_path / "catch-2026-09-16.json").exists()
        with gzip.open(gz, "rt", encoding="utf-8") as handle:
            assert json.load(handle) == {"jobs": [1, 2, 3]}

    def test_todays_file_is_left_alone(self, tmp_path):
        """아직 오늘 재실행이 읽는다."""
        _write(tmp_path, "wanted-2026-09-17.json", {})
        assert compress_old(tmp_path, "2026-09-17") == 0
        assert (tmp_path / "wanted-2026-09-17.json").exists()

    def test_future_dates_are_left_alone(self, tmp_path):
        """시계가 어긋난 날 쓴 파일을 건드리면 그날 받은 것을 또 받는다."""
        _write(tmp_path, "wanted-2026-09-20.json", {})
        assert compress_old(tmp_path, "2026-09-17") == 0
        assert (tmp_path / "wanted-2026-09-20.json").exists()

    def test_files_without_a_date_are_left_alone(self, tmp_path):
        _write(tmp_path, "notes.json", {})
        (tmp_path / "readme.txt").write_text("x", encoding="utf-8")
        assert compress_old(tmp_path, "2026-09-17") == 0
        assert len(list(tmp_path.iterdir())) == 2

    def test_subdirectories_are_not_touched(self, tmp_path):
        """work24 캐시가 이 밑에 있다. 그쪽은 prune_cache 가 따로 본다."""
        nested = tmp_path / "work24"
        nested.mkdir()
        _write(nested, "Power_BI__90d_p3_2026-09-16.json", {})
        assert compress_old(tmp_path, "2026-09-17") == 0
        assert (nested / "Power_BI__90d_p3_2026-09-16.json").exists()

    def test_already_compressed_files_are_not_compressed_again(self, tmp_path):
        _write(tmp_path, "catch-2026-09-16.json", {"a": 1})
        assert compress_old(tmp_path, "2026-09-17") == 1
        assert compress_old(tmp_path, "2026-09-17") == 0

    def test_a_rerun_overwrites_the_gzip_of_the_same_day(self, tmp_path):
        """같은 날짜의 .json 이 다시 생겼다면 그게 최신이다."""
        _write(tmp_path, "catch-2026-09-16.json", {"old": True})
        compress_old(tmp_path, "2026-09-17")
        _write(tmp_path, "catch-2026-09-16.json", {"new": True})
        assert compress_old(tmp_path, "2026-09-17") == 1
        with gzip.open(tmp_path / "catch-2026-09-16.json.gz", "rt", encoding="utf-8") as handle:
            assert json.load(handle) == {"new": True}

    def test_no_half_written_gzip_is_left_when_it_fails(self, tmp_path, monkeypatch):
        """중간에 죽으면 원본도 잃고 압축본도 못 읽는 일이 생기면 안 된다."""
        _write(tmp_path, "catch-2026-09-16.json", {"a": 1})

        def boom(*args, **kwargs):
            raise OSError("디스크가 꽉 찼습니다")

        monkeypatch.setattr("collect.raw_cache.gzip.open", boom)
        try:
            compress_old(tmp_path, "2026-09-17")
        except OSError:
            pass
        assert (tmp_path / "catch-2026-09-16.json").exists(), "원본은 남아야 한다"
        assert not list(tmp_path.glob("*.gz"))
        assert not list(tmp_path.glob("*.tmp"))

    def test_a_missing_directory_is_not_an_error(self, tmp_path):
        assert compress_old(tmp_path / "없음", "2026-09-17") == 0
