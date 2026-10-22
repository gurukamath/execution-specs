"""
EIP mixins of the Bogota targets, derived from the specification.

The specification's development source names every EIP set that
fixtures are filled against in its `TARGETS` table: the generated
Bogota fork and the devnet pseudo-forks that extend it. A fork class
here lists `TargetEIPs("<target>")` among its bases, and that expands
to the `EIP<n>` mixins of the EIPs the target adds beyond its base
target, so the EIP set is written once, in the specification.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

from ethereum.forks.develop.eips import TARGETS
from ethereum_spec_tools.forks import resolve_target

__all__ = ["TargetEIPs"]

if TYPE_CHECKING:

    def TargetEIPs(target: str) -> type[BaseFork]:  # noqa: N802
        """Typing-only stand-in for a target's EIP mixins."""
        ...

    from execution_testing.forks.base_fork import BaseFork

else:
    from execution_testing.forks.base_fork import TargetMarker

    def _fork_class_name(target: str) -> str:
        """Return the fork class name a target name maps to."""
        return "".join(part.capitalize() for part in target.split("_"))

    def _mixin(eip: int) -> type:
        """
        Return the `EIP<n>` class of this package's `eip_<n>` module.
        """
        module_name = f"{__name__}.eip_{eip}"
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError as error:
            raise ImportError(
                f"the specification's TARGETS name EIP-{eip}, but"
                f" `{module_name}` does not exist"
            ) from error
        return getattr(module, f"EIP{eip}")

    class TargetEIPs:
        """
        Expand to the EIP mixins a target adds beyond its base target.

        The fork class listing this among its bases must also list the
        base target's fork class, so that the inherited mixins supply
        the base's EIPs.
        """

        def __init__(self, target: str) -> None:
            if target not in TARGETS:
                raise ValueError(f"`{target}` is not in TARGETS")
            self._target = target

        def __mro_entries__(
            self,
            bases: tuple[type, ...],
        ) -> tuple[type, ...]:
            """
            Return the mixins of the EIPs the target adds, followed by
            a marker that ties the fork class to the target's name.
            """
            entry = TARGETS[self._target]
            base_eips: frozenset[int] = frozenset()
            if entry.base is not None:
                base_eips = resolve_target(TARGETS, entry.base)
                base_class = _fork_class_name(entry.base)
                # `bases` still holds this sentinel, which has no name.
                if not any(
                    getattr(base, "__name__", None) == base_class
                    for base in bases
                ):
                    raise TypeError(
                        f"target `{self._target}` extends `{entry.base}`,"
                        f" so its fork class must subclass `{base_class}`"
                    )
            own = resolve_target(TARGETS, self._target) - base_eips
            marker = type(
                f"_{self._target}_target",
                (TargetMarker,),
                {
                    "__doc__": f"Mark a class as target `{self._target}`.",
                    "fork_class_name": _fork_class_name(self._target),
                },
            )
            return (*(_mixin(eip) for eip in sorted(own)), marker)
