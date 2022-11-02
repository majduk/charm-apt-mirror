import asyncio
import logging

import pytest

log = logging.getLogger(__name__)


class Helper:
    """Helper class for async functions."""

    @staticmethod
    async def run_action_wait(unit, action_name, **kwargs):
        action = await unit.run_action(action_name, **kwargs)
        await action.wait()
        return action.results

    @staticmethod
    async def run_wait(unit, command):
        action = await unit.run(command)
        await action.wait()
        return action.results


def pytest_addoption(parser):
    parser.addoption(
        "--series",
        type=str,
        default="jammy",
        help="Set the series for the machine units.",
    )


@pytest.fixture
def series(request):
    return request.config.getoption("--series")


@pytest.fixture(scope="class")
def apt_mirror_app(ops_test):
    return ops_test.model.applications["apt-mirror"]


@pytest.fixture(scope="class")
def apt_mirror_unit(apt_mirror_app):
    return apt_mirror_app.units[0]


@pytest.fixture(scope="class")
def configs(apt_mirror_app):
    async def get_config_synced():
        return await apt_mirror_app.get_config()

    loop = asyncio.get_event_loop()
    coroutine = get_config_synced()
    return loop.run_until_complete(coroutine)


@pytest.fixture(scope="class")
def helper():
    return Helper
