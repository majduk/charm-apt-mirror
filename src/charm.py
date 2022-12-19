#!/usr/bin/env python3
# Copyright 2020 Ubuntu
# See LICENSE file for licensing details.

import json
import logging
import os
import re
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from jinja2 import Template
from ops.charm import CharmBase
from ops.framework import StoredState
from ops.main import main
from ops.model import ActiveStatus, BlockedStatus

from utils import convert_bytes, find_packages_by_indices, locate_package_indices

logger = logging.getLogger(__name__)


class AptMirrorCharm(CharmBase):
    _stored = StoredState()

    def __init__(self, *args):  # noqa: D107
        super().__init__(*args)
        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(self.on.install, self._on_install)
        self.framework.observe(self.on.update_status, self._on_update_status)
        self.framework.observe(self.on.synchronize_action, self._on_synchronize_action)
        self.framework.observe(
            self.on.create_snapshot_action, self._on_create_snapshot_action
        )
        self.framework.observe(
            self.on.publish_snapshot_action, self._on_publish_snapshot_action
        )
        self.framework.observe(
            self.on.list_snapshots_action, self._on_list_snapshots_action
        )
        self.framework.observe(
            self.on.delete_snapshot_action, self._on_delete_snapshot_action
        )
        self.framework.observe(
            self.on.check_packages_action, self._on_check_packages_action
        )
        self.framework.observe(
            self.on.clean_up_packages_action, self._on_clean_up_packages_action
        )
        self.framework.observe(
            self.on.publish_relation_joined, self._on_publish_relation_joined
        )

        self._stored.set_default(config={})

    def _on_publish_relation_joined(self, event):
        publish_path = "{}/publish".format(self._stored.config["base-path"])
        event.relation.data[self.model.unit].update({"path": publish_path})

    def _update_status(self):
        published_snapshot = self._get_published_snapshot()
        if published_snapshot:
            self.model.unit.status = ActiveStatus(
                "Publishes: {}".format(published_snapshot)
            )
        else:
            path = self._stored.config["base-path"] + "/mirror"
            if os.path.isdir(path):
                stat = os.stat(path)
                self.model.unit.status = BlockedStatus(
                    "Last sync: {} not published".format(time.ctime(stat.st_mtime))
                )
            else:
                self.model.unit.status = BlockedStatus("Packages not synchronized")

    def _on_update_status(self, _):
        self._update_status()

    def _on_install(self, _):
        subprocess.check_output(["apt", "install", "-y", "apt-mirror"])

    def _patch_config(self, current_config):
        config = {}
        proxy_settings = {
            "JUJU_CHARM_HTTP_PROXY": "http_proxy",
            "JUJU_CHARM_HTTPS_PROXY": "https_proxy",
        }
        if "use-proxy" in current_config and current_config["use-proxy"]:
            for env, proxy in proxy_settings.items():
                if env in os.environ:
                    config[proxy] = os.environ[env]
        config["use-proxy"] = set(proxy_settings.values()) & set(config)
        config["mirror-list"] = self._validate_mirror_list(
            current_config["mirror-list"]
        )
        return config

    def _on_config_changed(self, _):
        change_set = set()
        for key, value in self.model.config.items():
            if key not in self._stored.config or self._stored.config[key] != value:
                logger.info("Setting {} to: {}".format(key, value))
                self._stored.config[key] = value
                change_set.add(key)
        try:
            patched_config = self._patch_config(self._stored.config)
        except ValueError as err:
            self.model.unit.status = BlockedStatus(str(err))
        else:
            change_set.update(patched_config)
            self._stored.config.update(patched_config)

            # use change set to support single dispatch of a config change.
            template_change_set = {
                "base-path",
                "architecture",
                "threads",
                "http_proxy",
                "https_proxy",
                "mirror-list",
            }
            if len(change_set & template_change_set) > 0:
                self._render_config(self._stored.config)
            if "cron-schedule" in change_set:
                if self._stored.config["cron-schedule"] == "":
                    self._remove_cron_job()
                else:
                    self._setup_cron_job(self._stored.config)
            self._update_status()

    def _check_packages(self):
        # Find all packages that are defined in the "Packages" file of the
        # mirror_path and snapshot-*. These packages are still in referenced
        # and should not be removed.
        indexed_packages = set()
        snapshot_paths = self._list_snapshots()
        mirror_path = "{}/mirror".format(self._stored.config["base-path"])
        for path in snapshot_paths + [mirror_path]:
            for base, indices in locate_package_indices(path):
                indexed_packages |= set(find_packages_by_indices(indices, base=base))

        # Find all .deb packages that exist in the mirror_path. It might
        # contains some packages that are not longer being referenced. We can
        # clean up those packages using the clean-up-packages action.
        existing_packages = set(
            [p.absolute() for p in Path(mirror_path).rglob("**/*.deb")]
        )

        # Main calculation
        packages_to_be_removed = existing_packages - indexed_packages
        freed_up_space = 0
        for p in packages_to_be_removed:
            freed_up_space += p.stat().st_size
        return packages_to_be_removed, convert_bytes(freed_up_space)

    def _on_check_packages_action(self, event):
        packages_to_be_removed, freed_up_space = self._check_packages()
        event.set_results(
            {
                "message": (
                    "The following packages can be removed since they have no "
                    "reference in the current index and indices in all the snapshots."
                ),
                "count": len(packages_to_be_removed),
                "packages": json.dumps(
                    [str(p) for p in packages_to_be_removed], indent=4
                ),
                "total-size": freed_up_space,
            }
        )

    def _on_clean_up_packages_action(self, event):
        if not event.params["confirm"]:
            logger.info(
                "clean up action not performed because the user did not confirm "
                "the action."
            )
            event.set_results(
                {"message": "Aborted! Please confirm your action with 'confirm=true'."}
            )
            return

        start = time.time()
        logger.info("Cleaning up unreferenced packages")
        packages_to_be_removed, freed_up_space = self._check_packages()
        prefix_message = "Clean up completed without errors."
        for package in packages_to_be_removed:
            try:
                package.unlink()
                logger.info("Removed {}".format(package))
            except Exception as e:
                logger.error(e)
                prefix_message = (
                    "Clean up completed with errors, "
                    "please refer to juju's log for details."
                )
        elapsed = time.time() - start
        logger.info("Clean up complete, took {}s".format(elapsed))

        event.set_results(
            {
                "message": "{} Freed up {}".format(prefix_message, freed_up_space),
            }
        )

    def _on_synchronize_action(self, event):
        logger.info("Syncing packages")
        try:
            start = time.time()
            mirror_path = "{}/mirror".format(self._stored.config["base-path"])
            for dists in Path(mirror_path).rglob("**/dists"):
                shutil.rmtree(dists)
            subprocess.check_output(["apt-mirror"], stderr=subprocess.STDOUT)
            packages_to_be_removed, freed_up_space = self._check_packages()
            for package in packages_to_be_removed:
                package.unlink()
            elapsed = time.time() - start
            logger.info(
                "Sync complete, took {}s and freed up {}".format(
                    elapsed, freed_up_space
                )
            )
            event.set_results(
                {"time": elapsed, "message": "Freed up {}".format(freed_up_space)}
            )
        except subprocess.CalledProcessError as e:
            logger.info("Error {}".format(e.output))
            event.fail(e.output)
        self._update_status()

    def _on_create_snapshot_action(self, event):
        snapshot_name = self._get_snapshot_name()
        logger.info("Create snapshot {}".format(snapshot_name))
        snapshot_name_path = "{}/{}".format(
            self._stored.config["base-path"], snapshot_name
        )
        mirror_path = "{}/mirror".format(self._stored.config["base-path"])
        mirrors = self._mirror_names()
        if not os.path.exists(snapshot_name_path):
            os.makedirs(snapshot_name_path)
        for dirpath, dirs, files in os.walk(mirror_path):
            if "pool" in dirs:
                src_root = dirpath
                src_pool = "{}/pool".format(src_root)
                subtree = self._build_subtree(mirrors, src_root, mirror_path)
                dst_root = "{}/{}".format(snapshot_name_path, subtree)
                dst_pool = "{}/pool".format(dst_root)
                os.makedirs(dst_root, exist_ok=True)
                os.symlink(src_pool, dst_pool)
                logger.info("{} -> {}".format(src_pool, dst_pool))
            if "dists" in dirs:
                src_root = dirpath
                src_dists = "{}/dists".format(src_root)
                subtree = self._build_subtree(mirrors, src_root, mirror_path)
                dst_root = "{}/{}".format(snapshot_name_path, subtree)
                dst_dists = "{}/dists".format(dst_root)
                os.makedirs(dst_root, exist_ok=True)
                shutil.copytree(src_dists, dst_dists)
                logger.info("{} -> {}".format(src_dists, dst_dists))
        self._update_status()

    def _on_delete_snapshot_action(self, event):
        snapshot = event.params["name"]
        if not snapshot.startswith("snapshot-"):
            event.fail("Invalid snapshot name: {}".format(snapshot))
            return
        logger.info("Delete snapshot {}".format(snapshot))
        shutil.rmtree("{}/{}".format(self._stored.config["base-path"], snapshot))
        self._update_status()

    def _list_snapshots(self):
        return list(Path(self._stored.config["base-path"]).glob("snapshot-*"))

    def _on_list_snapshots_action(self, event):
        snapshots = [p.name for p in self._list_snapshots()]
        logger.info("List snapshots {}".format(snapshots))
        event.set_results({"snapshots": snapshots})

    def _on_publish_snapshot_action(self, event):
        name = event.params["name"]
        logger.info("Publish snapshot {}".format(name))
        snapshot_path = "{}/{}".format(self._stored.config["base-path"], name)
        publish_path = "{}/publish".format(self._stored.config["base-path"])
        if not os.path.isdir(snapshot_path):
            event.fail("Snapshot does not exist")
            return
        if os.path.islink(publish_path):
            os.unlink(publish_path)
        os.symlink(snapshot_path, publish_path)
        event.set_results({name: publish_path})
        self._update_status()

    def _render_config(self, config):
        with open("templates/mirror.list.j2") as f:
            t = Template(f.read())
        with open("/etc/apt/mirror.list", "wb") as f:
            b = t.render(opts=config).encode("UTF-8")
            f.write(b)

    def _setup_cron_job(self, config):
        with open("/etc/cron.d/{}".format(self.model.app.name), "w") as f:
            f.write("{} root apt-mirror\n".format(config["cron-schedule"]))

    def _remove_cron_job(self):
        cron_job = "/etc/cron.d/{}".format(self.model.app.name)
        if os.path.exists(cron_job):
            os.unlink(cron_job)

    def _get_snapshot_name(self):
        return "snapshot-{}".format(datetime.now().strftime("%Y%m%d%H%M%S"))

    def _validate_mirror_list(self, mirror_list):
        validated_mirror_list = []
        for mirror in mirror_list.splitlines():
            mirror_parts = mirror.split()
            if mirror == "":
                continue
            if len(mirror_parts) < 3:
                raise ValueError(
                    "An error has occurred when parsing 'mirror-list'. Please check "
                    "your 'mirror-list' option."
                )
            validated_mirror_list.append(mirror)
        return validated_mirror_list

    def _mirror_names(self):
        return [
            urlparse(mirror.split()[1]).hostname
            for mirror in self._stored.config["mirror-list"]
        ]

    def _get_published_snapshot(self):
        publish_path = "{}/publish".format(self._stored.config["base-path"])
        if os.path.islink(publish_path):
            return os.path.basename(os.readlink(publish_path))

    def _build_subtree(self, mirrors, root, path):
        # path relative to root directory
        subtree = os.path.relpath(root, path)
        # strip mirror name from the path
        if (
            "strip-mirror-name" in self._stored.config
            and self._stored.config["strip-mirror-name"]  # noqa: W503
        ):
            for m in mirrors:
                if re.findall(r"^{}".format(m), subtree):
                    subtree = os.path.relpath(subtree, m)
        # strip arbitrary component from the path
        if (
            "strip-mirror-path" in self._stored.config
            and self._stored.config["strip-mirror-path"]  # noqa: W503
        ):
            if self._stored.config["strip-mirror-path"] in subtree:
                subtree = subtree.replace(self._stored.config["strip-mirror-path"], "")
        return subtree


if __name__ == "__main__":
    main(AptMirrorCharm)
