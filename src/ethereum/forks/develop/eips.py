"""
Feature flags for the EIPs the development source tree carries.

This package is the source every fork under development is generated
from. It holds every EIP considered for inclusion, each behind a flag
defined here. A flag is read only as the whole test of an `if`
statement, so that a mechanical pass can fold the guards away: the
generated fork folders next to this package are such outputs, and so
are the temporary forks that devnet fills execute. The checked-in value
of every flag is `True`: this tree is the union of everything any
generated fork may ship.

[`REQUIRES`] records which EIPs an EIP builds on, [`OWNED_MODULES`]
records the modules that exist only for one EIP, and [`TARGETS`]
records the EIP sets anyone fills against: the forks generated next to
this package and the devnet pseudo-forks folded on demand. All three
are read by the tooling that validates an EIP selection and folds the
guards.

[`REQUIRES`]: ref:ethereum.forks.develop.eips.REQUIRES
[`OWNED_MODULES`]: ref:ethereum.forks.develop.eips.OWNED_MODULES
[`TARGETS`]: ref:ethereum.forks.develop.eips.TARGETS
"""

from dataclasses import dataclass
from typing import Final, FrozenSet, Mapping, Optional, Tuple

EIP_8141: Final[bool] = True
"""
[EIP-8141] frame transactions.

[EIP-8141]: https://eips.ethereum.org/EIPS/eip-8141
"""

EIP_8250: Final[bool] = True
"""
[EIP-8250] keyed nonces for frame transactions.

[EIP-8250]: https://eips.ethereum.org/EIPS/eip-8250
"""

EIP_8272: Final[bool] = True
"""
[EIP-8272] recent roots for frame transactions.

[EIP-8272]: https://eips.ethereum.org/EIPS/eip-8272
"""

EIP_7906: Final[bool] = True
"""
[EIP-7906] transaction assertions.

[EIP-7906]: https://eips.ethereum.org/EIPS/eip-7906
"""

REQUIRES: Final[Mapping[int, FrozenSet[int]]] = {
    8250: frozenset({8141}),
    8272: frozenset({8141}),
    7906: frozenset({8141}),
}
"""
EIPs that each EIP depends on, keyed by EIP number.

An EIP subset is valid only when it contains every requirement of every
EIP in it.
"""

OWNED_MODULES: Final[Mapping[int, Tuple[str, ...]]] = {
    8141: (
        "frame_processing",
        "transactions.frame_transaction",
        "vm.frame_interpreter",
        "vm.instructions.frame",
    ),
    8250: ("keyed_nonces",),
}
"""
Modules that exist only for one EIP, keyed by EIP number and named
relative to the fork package.

An owned module is imported from shared modules only under its EIP's
flag and is removed when the guards are folded with that EIP disabled.
"""


@dataclass(frozen=True)
class Target:
    """
    An EIP set that fixtures are filled against.

    The set is the target's own EIPs plus, transitively, those of its
    [`base`][b]: a devnet is its fork's accepted set plus candidates, so
    accepting a candidate moves one number between entries rather than
    leaving the devnet entry stale.

    [b]: ref:ethereum.forks.develop.eips.Target.base
    """

    eips: FrozenSet[int]
    """
    The EIPs this target enables beyond its base.
    """

    base: Optional[str] = None
    """
    The name of the target whose set this one extends, if any.

    Without one, the target extends the fork this source is derived
    from: the source with every flag off. Every chain of bases ends
    there, so no target names that fork.
    """

    generated: bool = True
    """
    Whether the fold tool writes this target as a fork package next to
    the source. A target that is not generated is a pseudo-fork: the
    test tooling folds it on demand and nothing is checked in for it.
    """


TARGETS: Final[Mapping[str, Target]] = {
    "bogota": Target(eips=frozenset({8141})),
}
"""
The EIP sets fixtures are filled against, keyed by short name.

A generated entry names a fork package next to this one; every flag
outside its resolved set folds to the disabled branch when the fork is
generated. While a fork is open this is its kitchen sink; the set
shrinks only when an EIP leaves the fork. A pseudo-fork entry names a
devnet, and the test framework derives the devnet's fork class from it.
"""
