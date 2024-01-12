# Copyright 2020 Ubuntu
# See LICENSE file for licensing details.

import datetime
import os
import random
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock, Mock, call, mock_open, patch
from urllib.parse import urlparse
from uuid import uuid4

import ops.testing
from ops.model import ActiveStatus, BlockedStatus
from ops.testing import Harness

from charm import AptMirrorCharm

ops.testing.SIMULATE_CAN_CONNECT = True


def get_default_charm_configs():
    return {
        "mirror-list": "deb http://{0}/a {0}\ndeb http://{0}/b {0}".format(uuid4()),
        "base-path": str(uuid4()),
        "architecture": str(uuid4()),
        "threads": random.randint(10, 20),
        "use-proxy": True,
        "strip-mirror-name": False,
        "strip-mirror-path": None,
        "cron-schedule": str(uuid4()),
    }


class BaseTest(unittest.TestCase):
    def setUp(self):
        self.harness = Harness(AptMirrorCharm)
        self.harness.begin()
        # we need to have this to sync up the charm state: i.e. the
        # _stored.config
        with patch("charm.open", new_callable=mock_open):
            self.harness.update_config(get_default_charm_configs())
            self.harness.charm._on_config_changed(Mock())

    def tearDown(self):
        self.harness.cleanup()


class TestCharm(BaseTest):
    def mock_repo_directory_tree(self, path, host, h_path, repo, c):
        return (["{}/{}/{}/{}".format(path, host, h_path, repo), ["{}".format(n)], n] for n in c)

    @patch("charm.open", new_callable=mock_open)
    def test_bad_mirror_list(self, mock_open_call):
        bad_case_1 = """\
deb
"""
        bad_case_2 = """\
deb fake-uri
"""
        for test_case in [bad_case_1, bad_case_2]:
            with self.assertRaisesRegex(ValueError, "^An error .* option.$"):
                self.harness.charm._validate_mirror_list(test_case)

    @patch("charm.open", new_callable=mock_open)
    def test_good_mirror_list(self, mock_open_call):
        good_mirror_list = """\
deb fake-uri fake-distro fake-comp1

deb fake-uri fake-distro fake-comp1 fake-comp2
deb fake-uri fake-distro\
"""
        expected = [
            "deb fake-uri fake-distro fake-comp1",
            "deb fake-uri fake-distro fake-comp1 fake-comp2",
            "deb fake-uri fake-distro",
        ]
        returned = self.harness.charm._validate_mirror_list(good_mirror_list)
        self.assertEqual(sorted(returned), sorted(expected))

    @patch("os.path.islink")
    def test_update_status_not_synced(self, os_path_islink):
        os_path_islink.return_value = False
        self.harness.charm._on_update_status(Mock())
        self.assertEqual(
            self.harness.model.unit.status, BlockedStatus("Packages not synchronized")
        )

    @patch("os.path.islink")
    @patch("os.path.isdir")
    @patch("os.stat")
    def test_update_status_not_published(self, os_stat, os_path_isdir, os_path_islink):
        os_path_islink.return_value = False
        os_path_isdir.return_value = True

        class MockStat:
            st_mtime = 1

        os_stat.return_value = MockStat()
        self.harness.charm._on_update_status(Mock())
        self.assertEqual(
            self.harness.model.unit.status,
            BlockedStatus("Last sync: {} not published".format(time.ctime(os_stat.st_mtime))),
        )

    @patch("os.path.islink")
    @patch("os.readlink")
    def test_update_status_published(self, os_readlink, os_path_islink):
        snapshot_name = str(uuid4())
        os_path_islink.return_value = True
        os_readlink.return_value = "/tmp/{}".format(snapshot_name)
        self.harness.charm._on_update_status(Mock())
        self.assertEqual(
            self.harness.model.unit.status,
            ActiveStatus("Publishes: {}".format(snapshot_name)),
        )

    @patch("charm.open", new_callable=mock_open)
    def test_publish_relation_joined(self, mock_open_call):
        relation_id = self.harness.add_relation("publish", "webserver")
        self.harness.add_relation_unit(relation_id, "webserver/0")
        self.assertEqual(
            self.harness.get_relation_data(relation_id, self.harness._unit_name),
            {"path": "{}/publish".format(self.harness.charm._stored.config["base-path"])},
        )

    @patch("subprocess.check_output")
    def test_install(self, mock_subprocess_check_output):
        self.harness.charm._on_install(Mock())
        mock_subprocess_check_output.assert_called_with(["apt", "install", "-y", "apt-mirror"])

    @patch("charm.open", new_callable=mock_open)
    def test_cron_schedule_set(self, mock_open_call):
        schedule = str(uuid4())
        self.harness.update_config({"cron-schedule": schedule})
        mock_open_call.assert_called_with(
            "/etc/cron.d/{}".format(self.harness.charm.model.app.name), "w"
        )
        mock_open_call.return_value.write.assert_called_with(
            "{} root apt-mirror\n".format(schedule)
        )

    @patch("os.unlink")
    @patch("os.path.exists")
    @patch("charm.open", new_callable=mock_open)
    def test_cron_schedule_remove(self, mock_open_call, os_path_exists, os_unlink):
        schedule = ""
        self.harness.update_config({"cron-schedule": schedule})
        os_path_exists.return_value = True
        os_unlink.assert_called_with("/etc/cron.d/{}".format(self.harness.charm.model.app.name))

    @patch("charm.open", new_callable=mock_open)
    def test_apt_mirror_list(self, mocked_open):
        url = "http://archive.ubuntu.com/ubuntu"
        opts = "jammy main restricted universe multiverse"
        self.harness.update_config({"mirror-list": "deb {} {}".format(url, opts)})
        mocked_open.assert_called_with(Path("/etc/apt/mirror.list"), "wb")
        mocked_open().write.assert_called_once_with(
            "set base_path         {base-path}\n"
            "set mirror_path       $base_path/mirror\n"
            "set skel_path         $base_path/skel\n"
            "set var_path          $base_path/var\n"
            "set postmirror_script $var_path/postmirror.sh\n"
            "set defaultarch       {architecture}\n"
            "set run_postmirror    0\n"
            "set nthreads          {threads}\n"
            "set limit_rate        100m\n"
            "set _tilde            0\n"
            "{mirror-list}\n".format(**self.harness.model.config).encode()
        )

    @patch.dict(
        os.environ,
        {"JUJU_CHARM_HTTP_PROXY": "httpproxy", "JUJU_CHARM_HTTPS_PROXY": "httpsproxy"},
        clear=True,
    )
    def test_juju_proxy(self):
        with open("templates/mirror.list.j2") as f:
            t = f.read()
        mock_open_call = mock_open(read_data=t)
        with patch("charm.open", mock_open_call):
            self.harness.update_config({"use-proxy": True})
            default_config = self.harness.model.config
        mock_open_call.assert_called_with(Path("/etc/apt/mirror.list"), "wb")
        mock_open_call.return_value.write.assert_called_once_with(
            "set base_path         {base-path}\n"
            "set mirror_path       $base_path/mirror\n"
            "set skel_path         $base_path/skel\n"
            "set var_path          $base_path/var\n"
            "set postmirror_script $var_path/postmirror.sh\n"
            "set defaultarch       {architecture}\n"
            "set run_postmirror    0\n"
            "set nthreads          {threads}\n"
            "set limit_rate        100m\n"
            "set _tilde            0\n"
            "set use_proxy         on\n"
            "set http_proxy        httpproxy\n"
            "set https_proxy       httpsproxy\n"
            "{mirror-list}\n".format(**default_config).encode()
        )

    @patch.dict(
        os.environ,
        {"JUJU_CHARM_HTTP_PROXY": "httpproxy", "JUJU_CHARM_HTTPS_PROXY": "httpsproxy"},
        clear=True,
    )
    def test_juju_proxy_override(self):
        with open("templates/mirror.list.j2") as f:
            t = f.read()
        mock_open_call = mock_open(read_data=t)
        with patch("charm.open", mock_open_call):
            self.harness.update_config({"use-proxy": False})
            default_config = self.harness.model.config
        mock_open_call.assert_called_with(Path("/etc/apt/mirror.list"), "wb")
        mock_open_call.return_value.write.assert_called_once_with(
            "set base_path         {base-path}\n"
            "set mirror_path       $base_path/mirror\n"
            "set skel_path         $base_path/skel\n"
            "set var_path          $base_path/var\n"
            "set postmirror_script $var_path/postmirror.sh\n"
            "set defaultarch       {architecture}\n"
            "set run_postmirror    0\n"
            "set nthreads          {threads}\n"
            "set limit_rate        100m\n"
            "set _tilde            0\n"
            "{mirror-list}\n".format(**default_config).encode()
        )

    def test_get_mirrors_without_filter(self):
        """Test git_mirrors without any filter applied."""
        archive = "http://archive.ubuntu.com/ubuntu"
        mirrors = [
            f"deb {archive} jammy-security main restricted universe multiverse",
            f"deb {archive} jammy-updates main restricted universe multiverse",
            f"deb {archive} jammy-proposed main restricted universe multiverse",
            f"deb {archive} jammy-backports main restricted universe multiverse",
        ]
        mirror_regex = ""
        exp_mirrors = mirrors
        self.harness.charm._stored.config["mirror-list"] = mirrors
        filtered_mirrors = self.harness.charm._get_mirrors(mirror_regex)
        self.assertListEqual(filtered_mirrors, exp_mirrors)

    def test_get_mirrors_with_filter(self):
        """Test git_mirrors without any filter applied."""
        archive = "http://archive.ubuntu.com/ubuntu"
        mirrors = [
            f"deb {archive} jammy-security main restricted universe multiverse",
            f"deb {archive} jammy-updates main restricted universe multiverse",
            f"deb {archive} jammy-proposed main restricted universe multiverse",
            f"deb {archive} jammy-backports main restricted universe multiverse",
            f"deb {archive} focal-updates main restricted universe multiverse",
        ]
        self.harness.charm._stored.config["mirror-list"] = mirrors

        # based regex contains part of distribution
        mirror_regex = "updates"
        exp_mirrors = [
            f"deb {archive} jammy-updates main restricted universe multiverse",
            f"deb {archive} focal-updates main restricted universe multiverse",
        ]
        filtered_mirrors = self.harness.charm._get_mirrors(mirror_regex)
        self.assertListEqual(filtered_mirrors, exp_mirrors)

        # based regex contains full source
        mirror_regex = f"deb {archive} jammy-updates main restricted universe multiverse"
        exp_mirrors = [f"deb {archive} jammy-updates main restricted universe multiverse"]
        filtered_mirrors = self.harness.charm._get_mirrors(mirror_regex)
        self.assertListEqual(filtered_mirrors, exp_mirrors)

    def test_create_tmp_apt_mirror_config(self):
        """Test helper function to create tmp config for apt-mirror."""
        exp_path = "/tmp/test"
        exp_mirrors = [1, 2, 3]
        with mock.patch("charm.NamedTemporaryFile") as mock_tmp:
            mock_tmp.return_value.__enter__.return_value = mock_file = MagicMock()
            mock_file.name = exp_path
            self.harness.charm._render_config = mock_render = MagicMock()
            path = self.harness.charm._create_tmp_apt_mirror_config(*exp_mirrors)

            mock_tmp.assert_called_once_with(delete=False)
            mock_render.assert_called_once()
            args, _ = mock_render.call_args
            config, _ = args
            assert config.get("mirror-list") == tuple(exp_mirrors)
            assert path == Path(exp_path)

    @patch("charm.clean_dists")
    @patch("charm.clean_packages")
    @patch("subprocess.check_output")
    def test_synchronize_action_without_source(
        self, mock_subprocess_check_output, mock_clean_packages, mock_clean_dists
    ):
        event = MagicMock()
        event.params.get.return_value = exp_source = None
        exp_mirrors = ["deb test1", "deb test2"]
        exp_config = "/tmp/test"
        exp_packages_to_clean = ["test"]
        self.harness.charm._get_mirrors = mock_get_mirror = MagicMock(return_value=exp_mirrors)
        self.harness.charm._create_tmp_apt_mirror_config = (
            mock_create_tmp_apt_mirror_config
        ) = MagicMock(return_value=Path(exp_config))
        self.harness.charm._check_packages = Mock()
        self.harness.charm._check_packages.return_value = (
            exp_packages_to_clean,
            "0.0 bytes",
        )

        self.harness.charm._on_synchronize_action(event)
        mock_get_mirror.assert_called_once_with(exp_source)
        mock_clean_dists.assert_called_once_with(
            Path(self.harness.charm._stored.config["base-path"])
        )
        mock_create_tmp_apt_mirror_config.assert_called_once_with(*exp_mirrors)
        mock_subprocess_check_output.assert_called_once_with(
            ["apt-mirror", exp_config], stderr=subprocess.STDOUT
        )
        mock_clean_packages.assert_called_once_with(exp_packages_to_clean)

    @patch("charm.clean_dists")
    @patch("charm.clean_packages")
    @patch("subprocess.check_output")
    def test_synchronize_action_with_source(
        self, mock_subprocess_check_output, mock_clean_packages, mock_clean_dists
    ):
        event = MagicMock()
        event.params.get.return_value = exp_source = "deb test"
        exp_mirrors = ["deb test1", "deb test2"]
        exp_config = "/tmp/test"
        exp_packages_to_clean = ["test"]
        self.harness.charm._get_mirrors = mock_get_mirror = MagicMock(return_value=exp_mirrors)
        self.harness.charm._create_tmp_apt_mirror_config = (
            mock_create_tmp_apt_mirror_config
        ) = MagicMock(return_value=Path(exp_config))
        self.harness.charm._check_packages = Mock()
        self.harness.charm._check_packages.return_value = (
            exp_packages_to_clean,
            "0.0 bytes",
        )

        self.harness.charm._on_synchronize_action(event)
        mock_get_mirror.assert_called_once_with(exp_source)
        mock_clean_dists.assert_not_called()
        mock_create_tmp_apt_mirror_config.assert_called_once_with(*exp_mirrors)
        mock_subprocess_check_output.assert_called_once_with(
            ["apt-mirror", exp_config], stderr=subprocess.STDOUT
        )
        mock_clean_packages.assert_called_once_with(exp_packages_to_clean)

    @patch("charm.open", new_callable=mock_open)
    @patch("os.walk")
    @patch("shutil.copytree")
    @patch("os.path.exists")
    @patch("os.symlink")
    @patch("os.makedirs")
    def test_create_snapshot_action(
        self, os_makedirs, os_symlink, os_path_exists, shutil_copytree, os_walk, _
    ):
        self.harness.update_config(
            {
                "strip-mirror-name": False,
                "mirror-list": "deb http://{0}/a {0}".format(uuid4()),
            }
        )
        default_config = self.harness.model.config
        rand_subdir = str(random.randint(10, 100))
        upstream_path = "{}".format(uuid4())
        mirror_url = default_config["mirror-list"].split()[1]
        mirror_host = urlparse(mirror_url).hostname
        mirror_path = "{}/mirror".format(default_config["base-path"])
        os_walk.side_effect = iter(
            [
                self.mock_repo_directory_tree(
                    mirror_path,
                    mirror_host,
                    upstream_path,
                    rand_subdir,
                    ["pool", "dists"],
                )
            ]
        )
        os_path_exists.return_value = False

        snapshot_name = str(uuid4())
        self.harness.charm._get_snapshot_name = Mock()
        self.harness.charm._get_snapshot_name.return_value = snapshot_name
        self.harness.charm._on_create_snapshot_action(Mock())
        exp_src_root = (
            self.harness.charm.base_path / "mirror" / mirror_host / upstream_path / rand_subdir
        )
        exp_dst_root = (
            self.harness.charm.base_path
            / snapshot_name
            / mirror_host
            / upstream_path
            / rand_subdir
        )
        os_makedirs.assert_has_calls(
            [
                call(self.harness.charm.base_path / snapshot_name),
                call(exp_dst_root, exist_ok=True),
                call(exp_dst_root, exist_ok=True),
            ]
        )
        os_symlink.assert_called_once_with(exp_src_root / "pool", exp_dst_root / "pool")
        shutil_copytree.assert_called_once_with(exp_src_root / "dists", exp_dst_root / "dists")

    @patch("charm.open", new_callable=mock_open)
    @patch("os.walk")
    @patch("shutil.copytree")
    @patch("os.path.exists")
    @patch("os.symlink")
    @patch("os.makedirs")
    def test_create_snapshot_action_strip_mirrors(
        self, os_makedirs, os_symlink, os_path_exists, shutil_copytree, os_walk, _
    ):
        self.harness.update_config(
            {
                "strip-mirror-name": True,
                "mirror-list": "deb http://{0}/a {0}".format(uuid4()),
            }
        )
        default_config = self.harness.model.config

        rand_subdir = str(random.randint(10, 100))
        upstream_path = "{}".format(uuid4())
        mirror_url = default_config["mirror-list"].split()[1]
        mirror_host = urlparse(mirror_url).hostname
        mirror_path = "{}/mirror".format(default_config["base-path"])
        os_walk.side_effect = iter(
            [
                self.mock_repo_directory_tree(
                    mirror_path,
                    mirror_host,
                    upstream_path,
                    rand_subdir,
                    ["pool", "dists"],
                )
            ]
        )
        os_path_exists.return_value = False

        snapshot_name = str(uuid4())
        self.harness.charm._get_snapshot_name = Mock()
        self.harness.charm._get_snapshot_name.return_value = snapshot_name
        self.harness.charm._on_create_snapshot_action(Mock())
        exp_src_root = (
            self.harness.charm.base_path / "mirror" / mirror_host / upstream_path / rand_subdir
        )
        exp_dst_root = self.harness.charm.base_path / snapshot_name / upstream_path / rand_subdir
        os_makedirs.assert_has_calls(
            [
                call(self.harness.charm.base_path / snapshot_name),
                call(exp_dst_root, exist_ok=True),
                call(exp_dst_root, exist_ok=True),
            ]
        )
        os_symlink.assert_called_once_with(exp_src_root / "pool", exp_dst_root / "pool")
        shutil_copytree.assert_called_once_with(exp_src_root / "dists", exp_dst_root / "dists")

    @patch("charm.open", new_callable=mock_open)
    @patch("os.walk")
    @patch("shutil.copytree")
    @patch("os.path.exists")
    @patch("os.symlink")
    @patch("os.makedirs")
    def test_create_snapshot_action_strip_path(
        self, os_makedirs, os_symlink, os_path_exists, shutil_copytree, os_walk, _
    ):
        upstream_path = "{}".format(uuid4())
        self.harness.update_config(
            {
                "strip-mirror-name": False,
                "mirror-list": "deb http://{0}/a {0}".format(uuid4()),
                "strip-mirror-path": "/{}".format(upstream_path),
            }
        )
        default_config = self.harness.model.config

        rand_subdir = str(random.randint(10, 100))
        mirror_url = default_config["mirror-list"].split()[1]
        mirror_host = urlparse(mirror_url).hostname
        mirror_path = "{}/mirror".format(default_config["base-path"])
        os_walk.side_effect = iter(
            [
                self.mock_repo_directory_tree(
                    mirror_path,
                    mirror_host,
                    upstream_path,
                    rand_subdir,
                    ["pool", "dists"],
                )
            ]
        )
        os_path_exists.return_value = False

        snapshot_name = str(uuid4())
        self.harness.charm._get_snapshot_name = Mock()
        self.harness.charm._get_snapshot_name.return_value = snapshot_name
        self.harness.charm._on_create_snapshot_action(Mock())
        exp_src_root = (
            self.harness.charm.base_path / "mirror" / mirror_host / upstream_path / rand_subdir
        )
        exp_dst_root = self.harness.charm.base_path / snapshot_name / mirror_host / rand_subdir
        os_makedirs.assert_has_calls(
            [
                call(self.harness.charm.base_path / snapshot_name),
                call(exp_dst_root, exist_ok=True),
                call(exp_dst_root, exist_ok=True),
            ]
        )
        os_symlink.assert_called_once_with(exp_src_root / "pool", exp_dst_root / "pool")
        shutil_copytree.assert_called_once_with(exp_src_root / "dists", exp_dst_root / "dists")

    def test_list_snapshots_action(self):
        snapshot_name = "snapshot-{}".format(datetime.datetime.now().strftime("%Y%m%d%H%M%S"))
        snapshot = Mock()
        snapshot.name = snapshot_name
        for test_input, expected in [([snapshot], [snapshot_name]), ([], [])]:
            with self.subTest():
                action_event = Mock()
                self.harness.charm._get_snapshot_name = Mock()
                self.harness.charm._list_snapshots = Mock()
                self.harness.charm._list_snapshots.return_value = test_input
                self.harness.charm._on_list_snapshots_action(action_event)
                action_event.set_results.assert_called_once_with({"snapshots": expected})

    @patch("shutil.rmtree")
    def test_delete_snapshot_action_success(self, shutil_rmtree):
        snapshot_name = "snapshot-19700101"
        self.harness.charm._get_snapshot_name = Mock()
        self.harness.charm._on_delete_snapshot_action(Mock(params={"name": snapshot_name}))
        shutil_rmtree.assert_called_once_with(self.harness.charm.base_path / snapshot_name)

    @patch("shutil.rmtree")
    def test_delete_snapshot_action_failure(self, shutil_rmtree):
        for snapshot_name in ["", str(uuid4())]:
            with self.subTest(name=snapshot_name):
                self.harness.charm._get_snapshot_name = Mock()
                self.harness.charm._on_delete_snapshot_action(Mock(params={"name": snapshot_name}))
                shutil_rmtree.assert_not_called()

    @patch("os.path.isdir")
    @patch("os.path.islink")
    @patch("os.path.basename")
    @patch("os.symlink")
    @patch("os.readlink")
    @patch("os.unlink")
    def test_publish_snapshot_action_success(
        self,
        os_unlink,
        os_readlink,
        os_symlink,
        os_path_basename,
        os_path_islink,
        os_path_isdir,
    ):
        snapshot_name = str(uuid4())
        os_path_islink.return_value = True
        self.harness.charm._get_snapshot_name = Mock()
        self.harness.charm._on_publish_snapshot_action(Mock(params={"name": snapshot_name}))
        os_symlink.assert_called_once_with(
            self.harness.charm.base_path / snapshot_name,
            self.harness.charm.base_path / "publish",
        )

    @patch("os.path.isdir")
    @patch("os.path.islink")
    @patch("os.symlink")
    @patch("os.unlink")
    def test_publish_snapshot_action_fail(
        self, os_unlink, os_symlink, os_path_islink, os_path_isdir
    ):
        snapshot_name = str(uuid4())
        os_path_isdir.return_value = False
        action_event = Mock(params={"name": snapshot_name})
        self.harness.charm._get_snapshot_name = Mock()
        self.harness.charm._on_publish_snapshot_action(action_event)
        self.assertEqual(action_event.fail.call_args, call("Snapshot does not exist"))

    def test_list_snapshots_not_empty(self):
        with tempfile.TemporaryDirectory() as tmpdirname:
            base_path = Path(tmpdirname)
            with patch("charm.open", new_callable=mock_open):
                self.harness.update_config(
                    {
                        "base-path": str(base_path),
                    }
                )
            expected_snapshots = [base_path / "snapshot-1970010{}".format(i) for i in range(3)]
            for snapshot in expected_snapshots:
                snapshot.mkdir(parents=True)
            returned_snapshots = self.harness.charm._list_snapshots()
            self.assertEqual(sorted(expected_snapshots), sorted(returned_snapshots))

    def test_list_snapshots_empty(self):
        with tempfile.TemporaryDirectory() as tmpdirname:
            base_path = Path(tmpdirname)
            with patch("charm.open", new_callable=mock_open):
                self.harness.update_config(
                    {
                        "base-path": str(base_path),
                    }
                )
            expected_snapshots = []
            returned_snapshots = self.harness.charm._list_snapshots()
            self.assertEqual(expected_snapshots, sorted(returned_snapshots))

    def test_get_snapshot_name(self):
        snapshot_name = self.harness.charm._get_snapshot_name()
        part_0 = snapshot_name.split("-")[0]
        part_1 = snapshot_name.split("-")[1]
        self.assertEqual(part_0, "snapshot")
        self.assertTrue(datetime.datetime.strptime(part_1, "%Y%m%d%H%M%S"))

    def test_check_packages_action(self):
        packages_to_be_removed = [Mock() for i in range(3)]
        self.harness.charm._check_packages = Mock()
        self.harness.charm._check_packages.return_value = [
            packages_to_be_removed,
            "0.0 bytes",
        ]
        action_event = Mock()
        self.harness.charm._on_check_packages_action(action_event)
        action_event.set_results.assert_called_once()

    def test_clean_up_packages_action_false(self):
        action_event = Mock(params={"confirm": False})
        self.harness.charm._on_clean_up_packages_action(action_event)
        action_event.set_results.assert_called_once()

    def test_clean_up_packages_action_true(self):
        packages_to_be_removed = [Mock() for i in range(3)]
        self.harness.charm._check_packages = Mock()
        self.harness.charm._check_packages.return_value = [
            packages_to_be_removed,
            "0.0 bytes",
        ]
        action_event = Mock(params={"confirm": True})
        self.harness.charm._on_clean_up_packages_action(action_event)
        for package in packages_to_be_removed:
            package.unlink.assert_called_once()
