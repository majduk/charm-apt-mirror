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
from tempfile import NamedTemporaryFile
from typing import List, Optional
from urllib.parse import urlparse

from jinja2 import Environment, FileSystemLoader
from ops.charm import CharmBase
from ops.framework import StoredState
from ops.main import main
from ops.model import ActiveStatus, BlockedStatus

from utils import (
    clean_dists,
    clean_packages,
    convert_bytes,
    find_packages_by_indices,
    locate_package_indices,
)

logger = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).parent.resolve() / "../templates"
MIRROR_LIST_TEMPLATE = "mirror.list.j2"
APT_MIRROR_CONFIG = Path("/etc/apt/mirror.list")


class AptMirrorCharm(CharmBase):
    _stored = StoredState()

    def __init__(self, *args):  # noqa: D107
        super().__init__(*args)
        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(self.on.install, self._on_install)
        self.framework.observe(self.on.update_status, self._on_update_status)
        self.framework.observe(self.on.synchronize_action, self._on_synchronize_action)
        self.framework.observe(self.on.create_snapshot_action, self._on_create_snapshot_action)
        self.framework.observe(self.on.publish_snapshot_action, self._on_publish_snapshot_action)
        self.framework.observe(self.on.list_snapshots_action, self._on_list_snapshots_action)
        self.framework.observe(self.on.delete_snapshot_action, self._on_delete_snapshot_action)
        self.framework.observe(self.on.check_packages_action, self._on_check_packages_action)
        self.framework.observe(self.on.clean_up_packages_action, self._on_clean_up_packages_action)
        self.framework.observe(self.on.publish_relation_joined, self._on_publish_relation_joined)

        self._stored.set_default(config={})

    def _on_publish_relation_joined(self, event):
        publish_path = "{}/publish".format(self._stored.config["base-path"])
        event.relation.data[self.model.unit].update({"path": publish_path})

    def _update_status(self):
        published_snapshot = self._get_published_snapshot()
        if published_snapshot:
            self.model.unit.status = ActiveStatus("Publishes: {}".format(published_snapshot))
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
        """Patch configuration options.

        Some config options need to be obtained from the input config options.
        Therefore, this internal function processes the input configuration
        option, and adds additional options for the charm; the added options
        should be connected with "_", for example, http_proxy and https_proxy.

        Args:
            current_config: Current config options.

        Returns:
            config: Additional configuration options

        Raises:
            ValueError: If `_validate_mirror_list` failed.
        """
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
        config["mirror-list"] = self._validate_mirror_list(current_config["mirror-list"])
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
            # if _validate_mirror_list failed, set the unit to blocked state.
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
                self._render_config(self._stored.config, APT_MIRROR_CONFIG)
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
        existing_packages = set([p.absolute() for p in Path(mirror_path).rglob("**/*.deb")])

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
                "packages": json.dumps([str(p) for p in packages_to_be_removed], indent=4),
                "total-size": freed_up_space,
            }
        )

    def _on_clean_up_packages_action(self, event):
        if not event.params["confirm"]:
            logger.info(
                "clean up action not performed because the user did not confirm the action."
            )
            event.set_results(
                {"message": "Aborted! Please confirm your action with 'confirm=true'."}
            )
            return

        start = time.time()
        logger.info("Cleaning up unreferenced packages")
        packages_to_be_removed, freed_up_space = self._check_packages()
        cleanup_result = clean_packages(packages_to_be_removed)
        elapsed = time.time() - start

        if cleanup_result is True:
            message = "Clean up completed without errors."
        else:
            message = "Clean up completed with errors, please refer to juju's log for details."

        logger.info("Clean up complete, took %ds", elapsed)
        event.set_results(
            {
                "time": elapsed,
                "message": "{} Freed up {} by cleaning {} packages".format(
                    message, freed_up_space, len(packages_to_be_removed)
                ),
            }
        )

    def _get_mirrors(self, source: Optional[str] = None) -> List[str]:
        """Get filtered mirrors."""
        mirrors = list(self._stored.config.get("mirror-list", []))
        logger.debug("found %d mirrors in charm configuration", len(mirrors))
        if source:
            reg_filter = re.compile(source)
            mirrors = list(filter(reg_filter.search, mirrors))

        return mirrors

    def _create_tmp_apt_mirror_config(self, *mirrors) -> Path:
        """Create tmp apt_mirror config."""
        with NamedTemporaryFile(delete=False) as tmp_config:
            tmp_path = Path(tmp_config.name)

        config = dict(self._stored.config).copy()
        config["mirror-list"] = mirrors
        self._render_config(config, tmp_path)
        return tmp_path

    def _on_synchronize_action(self, event):
        """Perform synchronize action."""
        logger.info("Syncing packages")
        start = time.time()
        mirror_filter = event.params.get("source")
        mirrors = self._get_mirrors(mirror_filter)

        if not mirrors:
            event.fail(
                f"No mirror matches the filter `{mirror_filter}` or mirror-list config "
                "options is empty. Please check mirror list with "
                "`juju config apt-mirror mirror-list`"
            )
            return

        try:
            if mirror_filter is None:
                # clean dists only if no filter was applied
                clean_dists(Path(self._stored.config["base-path"]))

            logger.info("running apt-mirror for:%s", os.linesep + os.linesep.join(mirrors))
            tmp_path = self._create_tmp_apt_mirror_config(*mirrors)
            subprocess.check_output(["apt-mirror", str(tmp_path)], stderr=subprocess.STDOUT)
        except subprocess.CalledProcessError as error:
            logger.exception(error)
            event.fail(error.output)
        except Exception as error:
            logger.exception(error)
            event.fail(f"action failed due invalid configuration: {error}")
        else:
            packages_to_be_removed, freed_up_space = self._check_packages()
            clean_packages(packages_to_be_removed)
            elapsed = time.time() - start
            logger.info("Sync complete, took %ss and freed up %s", elapsed, freed_up_space)
            event.set_results(
                {
                    "time": elapsed,
                    "message": "Freed up {} by cleaning {} packages".format(
                        freed_up_space, len(packages_to_be_removed)
                    ),
                }
            )
            self._update_status()

    def _on_create_snapshot_action(self, event):
        snapshot_name = self._get_snapshot_name()
        logger.info("Create snapshot {}".format(snapshot_name))
        snapshot_name_path = "{}/{}".format(self._stored.config["base-path"], snapshot_name)
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

    def _render_config(self, config, apt_mirror_config: Path) -> None:
        """Render apt_mirror config."""
        env = Environment(loader=FileSystemLoader(TEMPLATE_DIR))
        template = env.get_template(MIRROR_LIST_TEMPLATE)
        rendered_config = template.render(opts=config).encode("UTF-8")

        with open(apt_mirror_config, "wb") as file:
            file.write(rendered_config)

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
            urlparse(mirror.split()[1]).hostname for mirror in self._stored.config["mirror-list"]
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
