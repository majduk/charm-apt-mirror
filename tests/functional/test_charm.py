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


class TestCharmActions:
    @pytest.fixture
    def base_path(self, configs):
        return configs.get("base-path").get("value")

    async def test_publish_snapshot_action(
        self, ops_test, apt_mirror_unit, base_path, helper
    ):
        """Test publish_snapshot action."""
        name = "snapshot-publishme"
        create_cmd = "mkdir -p {}/{}".format(base_path, name)
        check_cmd = "readlink {}/publish".format(base_path)
        cleanup_cmd = "rm -rf {}/{} {}/publish".format(base_path, name, base_path)

        results = await helper.run_wait(apt_mirror_unit, create_cmd)
        assert results.get("return-code") == 0

        results = await helper.run_action_wait(
            apt_mirror_unit, "publish-snapshot", name=name
        )
        await ops_test.model.wait_for_idle(apps=["apt-mirror"], status="active")
        assert results.get("return-code") == 0

        results = await helper.run_wait(apt_mirror_unit, check_cmd)
        assert results.get("return-code") == 0
        assert results.get("stdout").strip() == "{}/{}".format(base_path, name)

        results = await helper.run_wait(apt_mirror_unit, cleanup_cmd)
        assert results.get("return-code") == 0

    async def test_create_snapshot_action(
        self, ops_test, apt_mirror_unit, base_path, helper
    ):
        """Test create_snapshot action."""
        count_cmd = "ls {} | grep ^snapshot | wc -l".format(base_path)
        cleanup_cmd = "rm -rf {}/snapshot*".format(base_path)

        results = await helper.run_wait(apt_mirror_unit, count_cmd)
        original_count = int(results.get("stdout").strip())

        results = await helper.run_action_wait(apt_mirror_unit, "create-snapshot")
        await ops_test.model.wait_for_idle(apps=["apt-mirror"], status="blocked")
        assert results.get("return-code") == 0

        results = await helper.run_wait(apt_mirror_unit, count_cmd)
        expected_count = int(results.get("stdout").strip())
        assert original_count + 1 == expected_count

        results = await helper.run_wait(apt_mirror_unit, cleanup_cmd)
        assert results.get("return-code") == 0

    async def test_delete_snapshot_action(self, apt_mirror_unit, base_path, helper):
        """Test delete_snapshot action."""
        name = "snapshot-deleteme"
        create_cmd = "mkdir {}/{}".format(base_path, name)
        check_cmd = "find {}/{}".format(base_path, name)

        results = await helper.run_wait(apt_mirror_unit, create_cmd)
        assert results.get("return-code") == 0

        results = await helper.run_action_wait(
            apt_mirror_unit, "delete-snapshot", name=name
        )
        assert results.get("return-code") == 0

        results = await helper.run_wait(apt_mirror_unit, check_cmd)
        assert results.get("return-code") != 0

    async def test_list_snapshots_action(self, apt_mirror_unit, helper):
        """Test list snapshots action."""
        results = await helper.run_action_wait(apt_mirror_unit, "list-snapshots")
        assert results.get("return-code") == 0

    async def test_synchronize_action(self, apt_mirror_unit, helper):
        """Test synchronize action."""
        results = await helper.run_action_wait(apt_mirror_unit, "synchronize")
        assert results.get("return-code") == 0


class TestCharm:
    async def test_setup_cron_schedule(
        self, ops_test, apt_mirror_app, apt_mirror_unit, helper
    ):
        """Test setup cron schedule config option."""
        cron_schedule = "0 5 * * 1"
        await apt_mirror_app.set_config({"cron-schedule": cron_schedule})
        await ops_test.model.wait_for_idle(apps=["apt-mirror"])

        results = await helper.run_wait(
            apt_mirror_unit, "cat /etc/cron.d/{}".format(apt_mirror_app.name)
        )
        assert results.get("stdout").strip() == "{} root apt-mirror".format(
            cron_schedule
        )

        # restore configs
        await apt_mirror_app.reset_config(["cron-schedule"])

    @pytest.mark.skip
    async def test_remove_cron_schedule(self):
        """Test remove cron schedule config option."""
        # currently the charm does not support remove cron schedule for
        # apt-mirror.
        pass

    async def test_client_access(
        self, ops_test, apt_mirror_app, apt_mirror_unit, helper
    ):
        """Test remote apt server can be accessed by client."""
        # Let's use bootstack public ppa for testing; it's very small compared
        # to ubuntu or other os's repos.
        url = "ppa.launchpadcontent.net/canonical-bootstack/public/ubuntu"
        mirror_list = """\
deb https://{0} focal main
deb https://{0} bionic main\
""".format(
            url
        )
        await apt_mirror_app.set_config({"mirror-list": mirror_list})
        await ops_test.model.wait_for_idle(apps=["apt-mirror"])

        await apt_mirror_unit.run_action("synchronize")
        await apt_mirror_unit.run_action("create-snapshot")
        results = await helper.run_action_wait(apt_mirror_unit, "list-snapshots")
        list_outputs = results.get("snapshots").strip()
        snapshot_name = re.findall(r"snapshot-\d+", list_outputs)[0]

        await apt_mirror_unit.run_action("publish-snapshot", name=snapshot_name)
        await ops_test.model.wait_for_idle(apps=["apt-mirror"], status="active")
        assert bool(re.match("^Publishes.*", apt_mirror_unit.workload_status_message))

        nginx_unit = ops_test.model.applications["nginx"].units[0]
        nginx_public_ip = await nginx_unit.get_public_address()

        client_unit = ops_test.model.applications["client"].units[0]
        # Note these can be changed if you changed the test url and mirror_list
        test_apts = """\
deb http://{0}/apt-mirror/{1} focal
deb http://{0}/apt-mirror/{1} bionic\
""".format(
            nginx_public_ip, url
        )
        await client_unit.run("echo '{}' > /etc/apt/source.list".format(test_apts))
        # Add public key; this is only required for this particular mirror-list
        # option. The magic number: "4b9a81747a207542" is coming from
        # https://launchpad.net/~canonical-bootstack/+archive/ubuntu/public
        await client_unit.run(
            "apt-key adv --keyserver keyserver.ubuntu.com --recv-keys 4b9a81747a207542"
        )
        results = await helper.run_wait(client_unit, "apt-get update")
        assert results.get("return-code") == 0
