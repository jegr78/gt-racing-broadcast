"""Single source of truth for the running build's version string.

The VERSION file is stamped into the source-tree root by tools/build-binary.py
(`--add-data <workdir>/VERSION:src`, so it lands at `<_MEIPASS>/src/VERSION` in a
frozen binary); a repo checkout has none -> 'dev'. Both racecast.py (CLI) and the
relay resolve their version through this helper so the two never drift.
"""
import os


def read_version(src_base):
    """Return the trimmed VERSION file under `src_base`, or 'dev' when absent/empty.

    `src_base` is the source-tree root: the dir holding racecast.py in a repo run,
    and `<_MEIPASS>/src` in a frozen binary.
    """
    try:
        with open(os.path.join(src_base, "VERSION"), encoding="utf-8") as fh:
            return fh.read().strip() or "dev"
    except OSError:
        return "dev"


def label(version):
    """Display form of `version`: 'dev', or the version with exactly one leading 'v'.

    Release builds stamp the git tag (`v1.13.0`) while a manual build may stamp a bare
    `1.13.0`; both show as `v1.13.0`.
    """
    if version == "dev":
        return "dev"
    return "v" + version.removeprefix("v")
