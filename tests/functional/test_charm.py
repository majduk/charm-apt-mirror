import logging
import re
from pathlib import Path

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
    """Perform very basic checking of the actions."""

    @pytest.fixture
    def base_path(self, configs):
        return configs.get("base-path").get("value")

    async def test_publish_snapshot_action(self, ops_test, apt_mirror_unit, base_path, helper):
        """Test publish_snapshot action."""
        name = "snapshot-publishme"
        create_cmd = "mkdir -p {}/{}".format(base_path, name)
        check_cmd = "readlink {}/publish".format(base_path)
        cleanup_cmd = "rm -rf {}/{} {}/publish".format(base_path, name, base_path)

        results = await helper.run_wait(apt_mirror_unit, create_cmd)
        assert results.get("return-code") == 0

        results = await helper.run_action_wait(apt_mirror_unit, "publish-snapshot", name=name)
        await ops_test.model.wait_for_idle(apps=["apt-mirror"], status="active")
        assert results.get("return-code") == 0

        results = await helper.run_wait(apt_mirror_unit, check_cmd)
        assert results.get("return-code") == 0
        assert results.get("stdout").strip() == "{}/{}".format(base_path, name)

        results = await helper.run_wait(apt_mirror_unit, cleanup_cmd)
        assert results.get("return-code") == 0

    async def test_create_snapshot_action(self, ops_test, apt_mirror_unit, base_path, helper):
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

    @pytest.mark.parametrize(
        "name,expected_status",
        [("snapshot-deleteme", "completed"), ("random-name", "failed")],
    )
    async def test_delete_snapshot_action(
        self, name, expected_status, apt_mirror_unit, base_path, helper
    ):
        """Test delete_snapshot action."""
        create_cmd = "mkdir {}/{}".format(base_path, name)
        check_cmd = "find {}/{}".format(base_path, name)

        results = await helper.run_wait(apt_mirror_unit, create_cmd)
        assert results.get("return-code") == 0

        action = await apt_mirror_unit.run_action("delete-snapshot", name=name)
        await action.wait()
        assert action.status == expected_status

        if expected_status == "failed":
            results = await helper.run_wait(apt_mirror_unit, check_cmd)
            assert results.get("return-code") == 0
        else:
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

    async def test_check_packages_action(self, apt_mirror_unit, helper):
        """Test check packages action."""
        results = await helper.run_action_wait(apt_mirror_unit, "check-packages")
        assert results.get("return-code") == 0

    async def test_clean_up_packages_action(self, apt_mirror_unit, helper):
        """Test clean up packages action."""
        results = await helper.run_action_wait(apt_mirror_unit, "clean-up-packages", confirm=False)
        assert results.get("return-code") == 0
        assert "Aborted!" in results.get("message")

        results = await helper.run_action_wait(apt_mirror_unit, "clean-up-packages", confirm=True)
        assert results.get("return-code") == 0
        assert "Freed up" in results.get("message")


class TestCharm:
    """Perform various functional testing of the charm and its actions."""

    @pytest.fixture
    def base_path(self, configs):
        return configs.get("base-path").get("value")

    async def test_setup_cron_schedule(self, ops_test, apt_mirror_app, apt_mirror_unit, helper):
        """Test setup cron schedule config option.

        Test cron job for automatic synchronization is added.
        """
        cron_schedule = "0 5 * * 1"
        await apt_mirror_app.set_config({"cron-schedule": cron_schedule})
        await ops_test.model.wait_for_idle(apps=["apt-mirror"])

        results = await helper.run_wait(
            apt_mirror_unit, "cat /etc/cron.d/{}".format(apt_mirror_app.name)
        )
        assert results.get("stdout").strip() == "{} root apt-mirror".format(cron_schedule)

        # restore configs
        await apt_mirror_app.reset_config(["cron-schedule"])

    async def test_remove_cron_schedule(self, ops_test, apt_mirror_app, apt_mirror_unit, helper):
        """Test remove cron schedule config option.

        Test cron job for automatic synchronization is removed.
        """
        cron_schedule = ""
        await apt_mirror_app.set_config({"cron-schedule": cron_schedule})
        await ops_test.model.wait_for_idle(apps=["apt-mirror"])

        results = await helper.run_wait(
            apt_mirror_unit, "ls /etc/cron.d/{}".format(apt_mirror_app.name)
        )
        assert results.get("return-code") != 0

        # restore configs
        await apt_mirror_app.reset_config(["cron-schedule"])

    async def test_bad_mirror_list_options(
        self, ops_test, apt_mirror_app, apt_mirror_unit, base_path, helper
    ):
        """Test bad mirror-list config option.

        Test if the input mirror-list is not a valid string containing: <deb
        uri distribution [component1] [component2] [...]>. Note that it does
        not verify each part, such as <uri>, is valid or not.
        """
        # Clean up
        await apt_mirror_unit.run("rm -rf {}/mirror/*".format(base_path))
        await apt_mirror_unit.run("rm -rf {}/publish".format(base_path))
        await apt_mirror_unit.run("rm -rf {}/snapshot-*".format(base_path))

        # Create bad mirror-list option - 1
        mirror_list = "deb"
        await apt_mirror_app.set_config({"mirror-list": mirror_list})
        await ops_test.model.wait_for_idle(apps=["apt-mirror"], status="blocked")
        app = ops_test.model.applications["apt-mirror"]
        status_msg = app.units[0].workload_status_message
        assert bool(
            re.search("^An error .* option.$", status_msg)
        ), "apt-mirror did not show correct block message."

        # Create bad mirror-list option - 2
        mirror_list = "deb fake-uri"
        await apt_mirror_app.set_config({"mirror-list": mirror_list})
        await ops_test.model.wait_for_idle(apps=["apt-mirror"], status="blocked")
        app = ops_test.model.applications["apt-mirror"]
        status_msg = app.units[0].workload_status_message
        assert bool(
            re.search("^An error .* option.$", status_msg)
        ), "apt-mirror did not show correct block message."

        # Fix the mirror-list option
        url = "ppa.launchpadcontent.net/canonical-bootstack/public/ubuntu"
        mirror_list = """\
deb https://{0} focal main
deb https://{0} bionic main\
""".format(
            url
        )
        await apt_mirror_app.set_config({"mirror-list": mirror_list})
        await ops_test.model.wait_for_idle(apps=["apt-mirror"], status="blocked")
        app = ops_test.model.applications["apt-mirror"]
        status_msg = app.units[0].workload_status_message
        assert bool(
            re.search("^Last sync: .* not published$", status_msg)
        ), "apt-mirror did not show correct block message."

    async def test_client_access(
        self, ops_test, apt_mirror_app, apt_mirror_unit, base_path, helper
    ):
        """Test client access.

        Test remote apt server can be accessed by client.
        """
        # Clean up
        await apt_mirror_unit.run("rm -rf {}/mirror/*".format(base_path))
        await apt_mirror_unit.run("rm -rf {}/publish".format(base_path))
        await apt_mirror_unit.run("rm -rf {}/snapshot-*".format(base_path))

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
deb http://{0}/apt-mirror/{1} focal main
deb http://{0}/apt-mirror/{1} bionic main\
""".format(
            nginx_public_ip, url
        )
        await client_unit.run("echo '{}' > /etc/apt/sources.list".format(test_apts))
        # Add public key; this is only required for this particular mirror-list
        # option. The magic number: "4b9a81747a207542" is coming from
        # https://launchpad.net/~canonical-bootstack/+archive/ubuntu/public
        await client_unit.run(
            "apt-key adv --keyserver keyserver.ubuntu.com --recv-keys 4b9a81747a207542"
        )
        results = await helper.run_wait(client_unit, "apt-get update")
        assert results.get("return-code") == 0

    async def test_unreferenced_packages_config_changed(
        self, ops_test, apt_mirror_app, apt_mirror_unit, base_path, helper
    ):
        """Test removing unreferenced packages.

        Test unreferenced packages can be removed when mirror-list is
        changed and there's no snapshot requiring them.
        """
        # Clean up
        await apt_mirror_unit.run("rm -rf {}/mirror/*".format(base_path))
        await apt_mirror_unit.run("rm -rf {}/publish".format(base_path))
        await apt_mirror_unit.run("rm -rf {}/snapshot-*".format(base_path))

        # Start with 2 mirror lists.
        mirror_list = """\
deb https://ppa.launchpadcontent.net/canonical-bootstack/public/ubuntu focal main
deb http://ppa.launchpad.net/landscape/19.10/ubuntu bionic main\
"""
        await apt_mirror_app.set_config({"mirror-list": mirror_list})
        await ops_test.model.wait_for_idle(apps=["apt-mirror"])
        await apt_mirror_unit.run_action("synchronize")

        # End up with 1 mirror lists.
        mirror_list = """\
deb https://ppa.launchpadcontent.net/canonical-bootstack/public/ubuntu focal main
"""
        await apt_mirror_app.set_config({"mirror-list": mirror_list})
        await ops_test.model.wait_for_idle(apps=["apt-mirror"])
        await apt_mirror_unit.run_action("synchronize")

        # Since landscape mirror is removed, we expect there are no extra
        # unreferenced packages because they will be removed during
        # synchronization.
        results = await helper.run_action_wait(apt_mirror_unit, "check-packages")
        count = int(results.get("count"))
        assert count == 0

    async def test_unreferenced_packages_config_changed_snapshoted(
        self, ops_test, apt_mirror_app, apt_mirror_unit, base_path, helper
    ):
        """Test not removing snapshoted packages.

        Test packages will not be removed when mirror-list is changed but a
        snapshot is still requiring them.
        """
        # Clean up
        await apt_mirror_unit.run("rm -rf {}/mirror/*".format(base_path))
        await apt_mirror_unit.run("rm -rf {}/publish".format(base_path))
        await apt_mirror_unit.run("rm -rf {}/snapshot-*".format(base_path))

        # Start with 2 mirror lists.
        mirror_list = """\
deb https://ppa.launchpadcontent.net/canonical-bootstack/public/ubuntu focal main
deb http://ppa.launchpad.net/landscape/19.10/ubuntu bionic main\
"""
        await apt_mirror_app.set_config({"mirror-list": mirror_list})
        await ops_test.model.wait_for_idle(apps=["apt-mirror"])
        await apt_mirror_unit.run_action("synchronize")

        # Create a snapshot at this point.
        await apt_mirror_unit.run_action("create-snapshot")

        # End up with 1 mirror lists.
        mirror_list = """\
deb https://ppa.launchpadcontent.net/canonical-bootstack/public/ubuntu focal main
"""
        await apt_mirror_app.set_config({"mirror-list": mirror_list})
        await ops_test.model.wait_for_idle(apps=["apt-mirror"])
        await apt_mirror_unit.run_action("synchronize")

        # Even though we end up with only 1 mirror list, but since we created
        # a snapshot before changing mirror list, we should still have
        # references to the packages in the snapshot, thus there should be no
        # packages to be removed.
        results = await helper.run_action_wait(apt_mirror_unit, "check-packages")
        count = int(results.get("count"))
        assert count == 0

        # Let's try to delete the snapshot and check if there are
        # still some unreferenced packages remain.
        await apt_mirror_unit.run("rm -rf {}/snapshot-*".format(base_path))
        results = await helper.run_action_wait(apt_mirror_unit, "check-packages")
        count = int(results.get("count"))
        assert count > 0

    async def test_outdated_packages_version_changed(
        self, ops_test, apt_mirror_app, apt_mirror_unit, base_path, helper
    ):
        """Test removing outdated packages.

        Test outdated packages can be removed when current index is not
        requiring them and is pointing to newer versions.
        """
        # Clean up
        await apt_mirror_unit.run("rm -rf {}/mirror/*".format(base_path))
        await apt_mirror_unit.run("rm -rf {}/publish".format(base_path))
        await apt_mirror_unit.run("rm -rf {}/snapshot-*".format(base_path))

        # Start with a test mirror
        mirror_list = """\
deb https://ppa.launchpadcontent.net/canonical-bootstack/public/ubuntu focal main
"""
        await apt_mirror_app.set_config({"mirror-list": mirror_list})
        await ops_test.model.wait_for_idle(apps=["apt-mirror"])
        await apt_mirror_unit.run_action("synchronize")

        # Simulate there are outdated packages in the pool; for example adding
        # copies of current packages to the pool, but appending _outdated to
        # the filename.
        cmd_1 = r"for f in $(find {} -name *.deb)".format(Path(base_path, "mirror"))
        cmd_2 = r"; do cp $f $(echo $f | sed -e s/\.deb/_outdated\.deb/); done;"
        cmd = cmd_1 + cmd_2
        await apt_mirror_unit.run(cmd)

        # Make sure we find outdated packages.
        results = await helper.run_action_wait(apt_mirror_unit, "check-packages")
        count = int(results.get("count"))
        assert count > 0

        # Let's try to delete the outdated packages and check if there are
        # still some outdated packages remain.
        await helper.run_action_wait(apt_mirror_unit, "clean-up-packages", confirm=True)
        results = await helper.run_action_wait(apt_mirror_unit, "check-packages")
        count = int(results.get("count"))
        assert count == 0

    async def test_outdated_packages_distro_changed(
        self, ops_test, apt_mirror_app, apt_mirror_unit, base_path, helper
    ):
        """Test removing outdated packages.

        Test outdated packages are removed when a distro is upgraded, when no
        indices are not requiring them. Also, test the outdated packages are
        not removed when the index of the old distro is kept in the snapshot.
        """
        # Clean up
        await apt_mirror_unit.run("rm -rf {}/mirror/*".format(base_path))
        await apt_mirror_unit.run("rm -rf {}/publish".format(base_path))
        await apt_mirror_unit.run("rm -rf {}/snapshot-*".format(base_path))

        # Start with a test mirror
        mirror_list = """\
deb https://ppa.launchpadcontent.net/canonical-bootstack/public/ubuntu bionic main
"""
        await apt_mirror_app.set_config({"mirror-list": mirror_list})
        await ops_test.model.wait_for_idle(apps=["apt-mirror"])
        await apt_mirror_unit.run_action("synchronize")

        # Upgrade the distro to focal
        mirror_list = """\
deb https://ppa.launchpadcontent.net/canonical-bootstack/public/ubuntu focal main
"""
        await apt_mirror_app.set_config({"mirror-list": mirror_list})
        await ops_test.model.wait_for_idle(apps=["apt-mirror"])
        await apt_mirror_unit.run_action("synchronize")

        # Make sure we don't find outdated packages because they should be
        # removed during synchronization.
        results = await helper.run_action_wait(apt_mirror_unit, "check-packages")
        count = int(results.get("count"))
        assert count == 0

        # Let's switch back to bionic and create a snapshot before switching to
        # focal.
        mirror_list = """\
deb https://ppa.launchpadcontent.net/canonical-bootstack/public/ubuntu bionic main
"""
        await apt_mirror_app.set_config({"mirror-list": mirror_list})
        await ops_test.model.wait_for_idle(apps=["apt-mirror"])
        await apt_mirror_unit.run_action("synchronize")
        await helper.run_action_wait(apt_mirror_unit, "create-snapshot")
        mirror_list = """\
deb https://ppa.launchpadcontent.net/canonical-bootstack/public/ubuntu focal main
"""
        await apt_mirror_app.set_config({"mirror-list": mirror_list})
        await ops_test.model.wait_for_idle(apps=["apt-mirror"])
        await apt_mirror_unit.run_action("synchronize")

        # This time we should not find any "outdated" packages because they are
        # still required in the snapshot.
        results = await helper.run_action_wait(apt_mirror_unit, "check-packages")
        count = int(results.get("count"))
        assert count == 0

        # Remove the snapshot, and we should find the "outdated" packages
        await apt_mirror_unit.run("rm -rf {}/snapshot-*".format(base_path))
        results = await helper.run_action_wait(apt_mirror_unit, "check-packages")
        count = int(results.get("count"))
        assert count > 0
