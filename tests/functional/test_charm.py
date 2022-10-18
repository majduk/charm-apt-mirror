import logging
import re

import pytest

log = logging.getLogger(__name__)


@pytest.mark.abort_on_fail
@pytest.mark.skip_if_deployed
async def test_build_and_deploy(ops_test, series):
    """Test building apt-mirror charm and deploying it with a bundle file."""
    charm = await ops_test.build_charm(".")
    assert charm, "Charm was not built successfully."

    await ops_test.model.deploy(
        ops_test.render_bundle(
            "tests/functional/bundle.yaml.j2",
            charm=charm,
            series=series,
        )
    )
    await ops_test.model.wait_for_idle(apps=["apt-mirror"], status="blocked")
    await ops_test.model.wait_for_idle(apps=["nginx"], status="active")

    app = ops_test.model.applications["apt-mirror"]
    status_msg = app.units[0].workload_status_message
    assert bool(
        re.search("^Last sync: .* not published$", status_msg)
    ), "apt-mirror did not show correct block message."
