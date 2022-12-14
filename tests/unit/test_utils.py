# Copyright 2020 Ubuntu
# See LICENSE file for licensing details.

import unittest
from pathlib import Path
from uuid import uuid4

import utils

TEST_MIRROR_PATH = Path("./tests/unit/test_resources/apt-mirror/mirror").absolute()
TEST_ARCHIVE_ROOT = (
    TEST_MIRROR_PATH / "ppa.launchpadcontent.net/canonical-bootstack/public/ubuntu/"
)
TEST_INDEX = TEST_ARCHIVE_ROOT / "dists/focal/main/binary-amd64/Packages"
TEST_POOL = TEST_ARCHIVE_ROOT / "pool"


class TestUtils(unittest.TestCase):
    def test_find_packages_by_indices(self):
        """Test find_packages_by_indices on a sample mirror path."""
        expected_outputs = {p.absolute() for p in TEST_MIRROR_PATH.glob("**/*.deb")}
        returned_outputs = set()

        for archive_root, indices in utils.locate_package_indices(TEST_MIRROR_PATH):
            returned_outputs |= utils.find_packages_by_indices(
                indices, base=archive_root
            )

        self.assertEqual(sorted(expected_outputs), sorted(returned_outputs))

    def test_locate_package_indices(self):
        """Test locate_package_indices on a sample mirror path."""
        expected_num_of_indices = 2
        returned_output = list(utils.locate_package_indices(TEST_MIRROR_PATH))
        self.assertEqual(expected_num_of_indices, len(returned_output))

        non_existing_path = Path(str(uuid4()))
        self.assertRaises(
            FileNotFoundError, utils.locate_package_indices, non_existing_path
        )

    def test_convert_bytes(self):
        """Test convert_bytes on some sample bytes."""
        units = ["bytes", "KB", "MB", "GB", "TB"]
        test_params = [
            (1000 ** (i + 1), "{:.1f} {}".format(1000 ** (i + 1) / 1024 ** (i), unit))
            for i, unit in zip(range(5), units)
        ]
        for num, expected in test_params:
            with self.subTest():
                self.assertEqual(utils.convert_bytes(num), expected)

    def test__locate_packages_from_index(self):
        """Test _locate_packages_from_index on a sample index and pool."""
        expected_packages = {str(p.absolute()) for p in TEST_POOL.glob("**/*.deb")}
        returned_packages = [
            str(n)
            for n in utils._locate_packages_from_index(
                TEST_INDEX, archive_root=TEST_ARCHIVE_ROOT
            )
        ]
        self.assertEqual(sorted(expected_packages), sorted(returned_packages))

    def test__get_opener(self):
        from bz2 import open as bopen
        from gzip import open as gopen
        from lzma import open as lopen

        test_params = [
            ("{}.gz".format(uuid4), gopen),
            ("{}.bz2".format(uuid4), bopen),
            ("{}.lzma".format(uuid4), lopen),
            ("{}.xz".format(uuid4), lopen),
            ("{}".format(uuid4), open),
        ]
        for file, expected in test_params:
            with self.subTest():
                self.assertEqual(utils._get_opener(file), expected)
