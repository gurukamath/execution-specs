"""
Generate the forks under development from the development source package.

Each generated entry of the source package's `TARGETS` names a fork and
the EIPs it enables. The fork is produced by folding the source's feature
guards for that selection, formatting the result and writing it next to
the source. Pseudo-fork entries are validated but never written: the test
tooling folds them on demand. With `--check`, nothing is written: each
fork is generated into a temporary directory and compared with the
checked-in folder, so that continuous integration can require the two to
agree.
"""

import filecmp
import importlib
import sys
from argparse import ArgumentParser
from collections.abc import Mapping
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Sequence

from ..forks import DEVELOP_FORK_NAME, Hardfork, Target, resolve_target
from .builder import ForkBuilder, flags_of, validate_eip_selection


def _targets() -> Mapping[str, frozenset[int]]:
    """
    Resolve every entry of the source's `TARGETS` to its full EIP set,
    checking each against the source's flags and `REQUIRES`.

    Return the resolved sets of the generated entries only.
    """
    eips = importlib.import_module(f"ethereum.forks.{DEVELOP_FORK_NAME}.eips")
    targets: Mapping[str, Target] = eips.TARGETS
    flags = flags_of(eips)
    requires: Mapping[int, frozenset[int]] = getattr(eips, "REQUIRES", {})
    resolved = {name: resolve_target(targets, name) for name in targets}
    for name, enabled in resolved.items():
        try:
            validate_eip_selection(
                enabled, set(flags) - enabled, flags, requires
            )
        except ValueError as error:
            raise ValueError(f"target `{name}`: {error}") from error
    return {
        name: enabled
        for name, enabled in resolved.items()
        if targets[name].generated
    }


def _source_parent() -> Path:
    develop = next(
        fork
        for fork in Hardfork.discover(include_develop=True)
        if fork.short_name == DEVELOP_FORK_NAME
    )
    if develop.path is None:
        raise ValueError("development source package has no path")
    return Path(develop.path).parent


def generate(target: str, enabled: frozenset[int], output: Path) -> Path:
    """
    Fold the development source for `target` into `output / target`.
    """
    builder = ForkBuilder(DEVELOP_FORK_NAME, target)
    builder.output = output
    builder.force = True
    builder.format = True
    builder.modifiers = []
    builder.fold_feature_guards(enabled)
    builder.build()
    return builder.new_fork_path


def _differences(expected: Path, actual: Path) -> list[str]:
    """
    Paths that differ between two fork folders, ignoring bytecode caches.
    """
    found: list[str] = []

    def walk(comparison: filecmp.dircmp, prefix: Path) -> None:
        for name in comparison.left_only:
            found.append(str(prefix / name) + " (missing from generated)")
        for name in comparison.right_only:
            found.append(str(prefix / name) + " (not checked in)")
        for name in comparison.diff_files:
            found.append(str(prefix / name))
        for name, sub in comparison.subdirs.items():
            walk(sub, prefix / name)

    walk(
        filecmp.dircmp(expected, actual, ignore=["__pycache__"]),
        Path(),
    )
    return sorted(found)


def main(args: Sequence[str] | None = None) -> None:
    """
    Command-line entry point.
    """
    parser = ArgumentParser(
        description="Generate the forks under development from the"
        " development source package."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        default=False,
        help="generate into a temporary directory and fail if the result"
        " differs from the checked-in fork",
    )
    parser.add_argument(
        "targets",
        nargs="*",
        metavar="FORK",
        help="forks to generate (default: every generated entry of TARGETS)",
    )
    options = parser.parse_args(args)

    targets = _targets()
    selected = options.targets or sorted(targets)
    unknown = [name for name in selected if name not in targets]
    if unknown:
        parser.error(f"not a generated entry of TARGETS: {', '.join(unknown)}")

    parent = _source_parent()
    failed = False
    for name in selected:
        if options.check:
            with TemporaryDirectory() as scratch:
                generated = generate(name, targets[name], Path(scratch))
                differences = _differences(parent / name, generated)
            if differences:
                failed = True
                print(f"`{name}` is out of date; regenerate it:")
                for path in differences:
                    print(f"  {path}")
            else:
                print(f"`{name}` is up to date")
        else:
            generated = generate(name, targets[name], parent)
            print(f"generated `{generated}`")

    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
