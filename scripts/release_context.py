"""Materialize the Python image's reviewed inputs, never the whole checkout."""

from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def materialize(source: Path, destination: Path) -> None:
    """Copy only application Python and SQL; reject symlinks before reading."""
    if destination.resolve().is_relative_to(source.resolve()):
        raise ValueError("Release context must be outside the source checkout")
    destination.mkdir(parents=True, exist_ok=False)
    files = [
        Path("pyproject.toml"),
        Path("uv.lock"),
        Path("db/schema_baseline.sql"),
        Path("csa/evals/data/decision_cases.json"),
        # Read by the owner's smoke run inside the gateway image (scripts/assistant_smoke.py).
        Path("csa/workspace/smoke_cases.json"),
    ]
    for directory, suffix in (
        ("apps", ".py"),
        ("csa", ".py"),
        ("scripts", ".py"),
        ("db/migrations", ".sql"),
        ("db/sql", ".sql"),
    ):
        for parent in (Path(directory), *Path(directory).parents):
            if (source / parent).is_symlink():
                raise ValueError(f"Symlink is not a release input: {parent}")
        for entry in (source / directory).rglob("*"):
            relative = entry.relative_to(source)
            if entry.is_symlink():
                raise ValueError(f"Symlink is not a release input: {relative}")
            if any(part.startswith(".") or part == "__pycache__" for part in relative.parts):
                continue
            if (
                entry.is_file()
                and entry.suffix == suffix
                and re.fullmatch(r"[a-zA-Z0-9_]+" + re.escape(suffix), entry.name)
            ):
                files.append(relative)
    for relative in files:
        origin = source / relative
        if any((source / parent).is_symlink() for parent in relative.parents):
            raise ValueError(f"Symlink is not a release input: {relative}")
        if origin.is_symlink() or not origin.is_file():
            raise ValueError(f"Invalid release input: {relative}")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(origin, target)
    dockerfile = source / "deploy" / "Dockerfile"
    if dockerfile.parent.is_symlink() or dockerfile.is_symlink():
        raise ValueError("Dockerfile cannot be a symlink")
    shutil.copyfile(dockerfile, destination / "Dockerfile")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    materialize(ROOT, args.destination)


if __name__ == "__main__":
    main()
