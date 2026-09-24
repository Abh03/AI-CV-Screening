import pytest


def pytest_addoption(parser):
    parser.addoption("--run-infrastructure", action="store_true", help="Run PostgreSQL/Redis checks")
    parser.addoption("--run-live-llm", action="store_true", help="Allow live provider calls")


def pytest_collection_modifyitems(config, items):
    for item in items:
        if item.get_closest_marker("infrastructure") and not config.getoption("--run-infrastructure"):
            item.add_marker(pytest.mark.skip(reason="Use --run-infrastructure with PostgreSQL/Redis running"))
        if item.get_closest_marker("integration") and not config.getoption("--run-live-llm"):
            item.add_marker(pytest.mark.skip(reason="Use --run-live-llm to allow paid provider calls"))


@pytest.fixture(autouse=True)
def offline_llm(request, monkeypatch):
    if not request.node.get_closest_marker("integration"):
        from app.stage3_evaluation.llm_client import llm_client
        monkeypatch.setattr(llm_client, "provider", "mock")
        monkeypatch.setattr(llm_client, "_initialized", False)
