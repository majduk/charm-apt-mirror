# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

"""A collection of utility functions."""

from bz2 import open as bopen
from gzip import open as gopen
from lzma import open as lopen
from os import readlink
from pathlib import Path


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

    dists = list(path.glob("**/dists"))
    pool = [d.parent / "pool" for d in dists]

    archive_roots = []
    package_indices = []
    for p, d in zip(pool, dists):
        archive_roots.append(
            Path(readlink(p)).parent.absolute()
            if p.is_symlink()
            else p.parent.absolute()
        )
        package_indices.append(
            [sorted(distro.glob("**/Packages*"))[0] for distro in d.glob("*")]
        )
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
    with opener(package_index, mode="r") as f:
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
        return gopen
    elif extension == ".bz2":
        return bopen
    elif extension == ".lzma" or extension == ".xz":
        return lopen
    else:
        return open
