"""모든 테스트는 더미 설정(config/*.example.yaml)으로 돈다.

개인 설정(profile·experience·companies)은 저장소에 없다. 테스트 결과가 그
파일이 있느냐, 누구의 이력이 들어 있느냐에 따라 달라지면 저장소를 받은
사람의 컴퓨터에서 테스트가 깨진다.
"""
import pytest

from run import EXAMPLE_ENV


@pytest.fixture(autouse=True)
def example_configs(monkeypatch):
    monkeypatch.setenv(EXAMPLE_ENV, "1")
