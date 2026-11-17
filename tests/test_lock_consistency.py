from __future__ import annotations

import re
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 remains supported by the CI matrix.
    import tomli as tomllib

from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "requirements-dev.lock"
PROJECT = ROOT / "pyproject.toml"


def _canonicalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def test_windows_lock_contains_all_active_project_runtime_dependencies() -> None:
    """Catch missing runtime distributions in the Windows --no-deps CI install."""
    project = tomllib.loads(PROJECT.read_text(encoding="utf-8"))
    locked: dict[str, str] = {}
    for line in LOCK.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        requirement = Requirement(line)
        assert requirement.url is None, f"Direct URL is not allowed in lock: {line}"
        assert len(requirement.specifier) > 0, f"Lock entry must be pinned: {line}"
        locked[_canonicalize(requirement.name)] = str(requirement.specifier)

    missing = []
    for text in project["project"]["dependencies"]:
        requirement = Requirement(text)
        if requirement.marker is not None and not requirement.marker.evaluate(
            {"sys_platform": sys.platform, "python_version": f"{sys.version_info.major}.{sys.version_info.minor}"}
        ):
            continue
        if _canonicalize(requirement.name) not in locked:
            missing.append(requirement.name)
    assert not missing, f"Runtime distributions missing from Windows lock: {missing}"
