# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

"""A collection of utility functions."""

import logging
import shutil
from pathlib import Path
from typing import List, Set

logger = logging.getLogger(__name__)


def clean_dists(path: Path) -> None:
    """Clean dists for mirror path."""
    mirror_path = path / "mirror"
    for dists in mirror_path.rglob("**/dists"):
        shutil.rmtree(dists)
        logger.debug("Removed %s", dists)


def clean_packages(packages: Set[Path]) -> bool:
    """Clean up packages."""
    logger.info("Cleaning up unreferenced packages")
    result = True
    for package in packages:
        try:
            package.unlink()
            logger.debug("Removed %s", package)
        except FileNotFoundError as error:
            logger.error("package %s could not be removed", package)
            logger.exception(error)
            result = False

    return result


def find_packages_by_indices(indices, base=""):
    """Return the location of the packages.

    Find the locations of the packages given the path to "Packages*" files.

    Args:
        indices: A list of index files.
        base: The path prepend to the returned filename (optional, default="").

    Returns:
        The an unique set of path-to-packages found in the indices.
    """
    packages = set()
    package_indices = indices if isinstance(indices, list) else [indices]
    for package_index in package_indices:
        packages |= _locate_packages_from_index(package_index, archive_root=base)
    return packages


def _get_archive_root(pool: Path) -> Path:
    """Get archive path from pool path."""
    if pool.is_symlink():
        # Note(rgildein): using pool.resolve instead of pool.readlink, since readlink was
        # introduced in Python 3.9, we know that path exists, so it's safe
        return pool.resolve().parent.absolute()

    return pool.parent.absolute()


def _get_package_indices(dists: Path) -> List[Path]:
    """Get package indices."""
    indices, parents = [], []
    # filter files from same parent directory
    for path in sorted(dists.glob("**/Packages*")):
        if path.parent not in parents:
            indices.append(path)
            parents.append(path.parent)

    return indices


def locate_package_indices(path):
    """Return all locations of the index files.

    Find the locations of the index files within `path`.

    Args:
        path: The path from which we search for the index files.

    Returns:
        A zip of the path to archive root and the path to the package
        indices.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError("Invalid path: {}".format(path))

    archive_roots = []
    package_indices = []
    for dists in path.glob("**/dists"):
        pool = dists.parent / "pool"
        archive_roots.append(_get_archive_root(pool))
        package_indices.append(_get_package_indices(dists))

    return zip(archive_roots, package_indices)


def convert_bytes(num):
    """Convert `num` of bytes to an appropriate unit."""
    for unit in ["bytes", "KB", "MB", "GB", "TB"]:
        if num < 1024:
            return "{:.1f} {}".format(num, unit)
        num /= 1024


def _locate_packages_from_index(package_index, archive_root=""):
    """Return an unique set of path-to-packages found in the index file."""
    opener = _get_opener(package_index)

    # Note (rgildein): We need to use `rt` directly, because `r` for gzip is referring
    # to `rb` instead of `rt`.
    with opener(package_index, mode="rt") as f:
        packages = {
            Path(archive_root, line.strip().replace("Filename: ", ""))
            for line in f
            if line.startswith("Filename: ")
        }
    return packages


def _get_opener(path):
    """Return appropriate opener to open an index file."""
    extension = Path(path).suffix
    if extension == ".gz":
        import gzip

        return gzip.open
    elif extension == ".bz2":
        import bz2

        return bz2.open
    elif extension == ".lzma" or extension == ".xz":
        import lzma

        return lzma.open
    else:
        return open
