"""Derive the core hash lock from the verified full lock and installed dependency metadata.

This preserves all existing distribution hashes. Run in the fully locked development
environment; optional dependencies and operating-system markers are evaluated separately.
"""

import importlib.metadata as metadata
import re
import tomllib
from pathlib import Path

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


def main():
    root = Path(__file__).resolve().parents[1]
    config = tomllib.loads((root / "pyproject.toml").read_text())
    pending = [Requirement(value).name for value in config["project"]["dependencies"]]
    seen = set()
    environments = []
    for python in ("3.12", "3.13", "3.14"):
        for system, platform, os_name in (("darwin", "Darwin", "posix"),
                                          ("linux", "Linux", "posix"), ("win32", "Windows", "nt")):
            environments.append(dict(default_environment(), python_version=python,
                                     python_full_version=python + ".0", sys_platform=system,
                                     platform_system=platform, os_name=os_name, extra=""))
    while pending:
        name = canonicalize_name(pending.pop())
        if name in seen:
            continue
        seen.add(name)
        try:
            requirements = metadata.requires(name) or []
        except metadata.PackageNotFoundError:
            # Colorama has no dependencies and may not be installed on a POSIX host.
            if name != "colorama":
                raise
            requirements = []
        for value in requirements:
            requirement = Requirement(value)
            if requirement.marker is None or any(requirement.marker.evaluate(env) for env in environments):
                pending.append(requirement.name)
    raw = (root / "requirements.lock").read_text()
    blocks = re.split(r"(?m)^(?=[a-zA-Z0-9][a-zA-Z0-9._-]*==)", raw)
    selected = {}
    for block in blocks:
        match = re.match(r"([a-zA-Z0-9._-]+)==", block)
        if match and canonicalize_name(match[1]) in seen:
            # The full lock's dependency comments mention optional packages; drop them.
            lines = [line for line in block.splitlines() if not line.lstrip().startswith("#")]
            selected[canonicalize_name(match[1])] = "\n".join(lines).rstrip() + "\n"
    missing = seen - selected.keys()
    if missing:
        raise RuntimeError("Full lock lacks required core packages: " + ", ".join(sorted(missing)))
    header = ("# Core-only hash lock, derived from requirements.lock by scripts/lock_core.py.\n"
              "# Python 3.12-3.14, macOS/Linux/Windows. No hosted or model-provider SDKs.\n")
    (root / "requirements-core.lock").write_text(header + "".join(selected[n] for n in sorted(selected)))
    print(f"Locked {len(selected)} core packages")


if __name__ == "__main__":
    main()
