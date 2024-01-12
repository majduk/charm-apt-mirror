# Copyright 2020 Ubuntu
# See LICENSE file for licensing details.

import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch
from uuid import uuid4

import pytest

import utils

TEST_MIRROR_PATH = Path("./tests/unit/test_resources/apt-mirror/mirror").absolute()
TEST_ARCHIVE_ROOT = (
    TEST_MIRROR_PATH / "ppa.launchpadcontent.net/canonical-bootstack/public/ubuntu/"
)
TEST_INDEX = TEST_ARCHIVE_ROOT / "dists/focal/main/binary-amd64/Packages"
TEST_POOL = TEST_ARCHIVE_ROOT / "pool"


@patch("utils.shutil")
def test_clean_dists(mock_shutil):
    """Test for helper function to clean all dists for mirror."""
    tmp_path = MagicMock()
    tmp_path.__truediv__.return_value = mirror_path = MagicMock()
    dists = MagicMock()
    mirror_path.rglob.return_value = [dists, dists]

    utils.clean_dists(tmp_path)
    tmp_path.__truediv__.assert_called_once_with("mirror")
    mirror_path.rglob.assert_called_once_with("**/dists")
    mock_shutil.rmtree.assert_has_calls([call(dists), call(dists)])


@pytest.mark.parametrize(
    "packages, exp_result",
    [
        ([MagicMock(), MagicMock(), MagicMock()], True),
        (
            [MagicMock(), MagicMock(**{"unlink.side_effect": FileNotFoundError()})],
            False,
        ),
    ],
)
def test_clean_packages(packages, exp_result):
    """Test helper function to clean up packages."""
    assert utils.clean_packages(packages) == exp_result
    for package in packages:
        package.unlink.assert_called_once()


@pytest.mark.parametrize(
    "paths, exp_indices",
    [
        (
            [
                "bionic-backports/universe/binary-amd64/Packages.xz",
                "bionic-backports/universe/binary-amd64/Packages.gz",
                "bionic-backports/universe/binary-amd64/Packages",
                "bionic-backports/main/binary-amd64/Packages.xz",
                "bionic-backports/main/binary-amd64/Packages.gz",
                "bionic-backports/main/binary-amd64/Packages",
                "bionic-backports/multiverse/binary-amd64/Packages.xz",
                "bionic-backports/multiverse/binary-amd64/Packages.gz",
                "bionic-backports/multiverse/binary-amd64/Packages",
                "bionic-backports/restricted/binary-amd64/Packages.xz",
                "bionic-backports/restricted/binary-amd64/Packages.gz",
                "bionic-backports/restricted/binary-amd64/Packages",
                "focal-backports/universe/binary-amd64/Packages.xz",
                "focal-backports/universe/binary-amd64/Packages.gz",
                "focal-backports/universe/binary-amd64/Packages",
                "focal-backports/main/binary-amd64/Packages.xz",
                "focal-backports/main/binary-amd64/Packages.gz",
                "focal-backports/main/binary-amd64/Packages",
                "focal-backports/multiverse/binary-amd64/Packages.xz",
                "focal-backports/multiverse/binary-amd64/Packages.gz",
                "focal-backports/multiverse/binary-amd64/Packages",
                "focal-backports/restricted/binary-amd64/Packages.xz",
                "focal-backports/restricted/binary-amd64/Packages.gz",
                "focal-backports/restricted/binary-amd64/Packages",
            ],
            [
                "bionic-backports/main/binary-amd64/Packages",
                "bionic-backports/multiverse/binary-amd64/Packages",
                "bionic-backports/restricted/binary-amd64/Packages",
                "bionic-backports/universe/binary-amd64/Packages",
                "focal-backports/main/binary-amd64/Packages",
                "focal-backports/multiverse/binary-amd64/Packages",
                "focal-backports/restricted/binary-amd64/Packages",
                "focal-backports/universe/binary-amd64/Packages",
            ],
        )
    ],
)
def test_get_package_indices(tmp_path, paths, exp_indices):
    """Test helper function to obtain package indices from dists directory."""
    dists = tmp_path / "dists"
    for path in paths:
        _path = dists / path
        _path.mkdir(parents=True, exist_ok=True)
        _path.touch()

    assert utils._get_package_indices(dists) == [dists / index for index in exp_indices]


def test_get_archive_root_symlink():
    """Test helper function to get root for pool."""
    mock_path = MagicMock(spec_set=Path)
    pool = mock_path("/a/b/c")
    pool.is_symlink.return_value = True

    archive_root = utils._get_archive_root(pool)
    assert archive_root == pool.resolve.return_value.parent.absolute.return_value


def test_get_archive_root():
    """Test helper function to get root for pool."""
    mock_path = MagicMock(spec_set=Path)
    pool = mock_path("/a/b/c")
    pool.is_symlink.return_value = False

    archive_root = utils._get_archive_root(pool)
    assert archive_root == pool.parent.absolute.return_value


class TestUtils(unittest.TestCase):
    def test_find_packages_by_indices(self):
        """Test find_packages_by_indices on a sample mirror path."""
        expected_outputs = {p.absolute() for p in TEST_MIRROR_PATH.glob("**/*.deb")}
        returned_outputs = set()

        for archive_root, indices in utils.locate_package_indices(TEST_MIRROR_PATH):
            returned_outputs |= utils.find_packages_by_indices(indices, base=archive_root)

        self.assertEqual(sorted(expected_outputs), sorted(returned_outputs))

    def test_locate_package_indices(self):
        """Test locate_package_indices on a sample mirror path."""
        expected_num_of_indices = 2
        returned_output = list(utils.locate_package_indices(TEST_MIRROR_PATH))
        self.assertEqual(expected_num_of_indices, len(returned_output))

        non_existing_path = Path(str(uuid4()))
        self.assertRaises(FileNotFoundError, utils.locate_package_indices, non_existing_path)

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
            for n in utils._locate_packages_from_index(TEST_INDEX, archive_root=TEST_ARCHIVE_ROOT)
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
