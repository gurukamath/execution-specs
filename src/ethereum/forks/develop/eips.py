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
records which EIPs each generated fork enables. All three are read by
the tooling that validates an EIP selection and folds the guards.

[`REQUIRES`]: ref:ethereum.forks.develop.eips.REQUIRES
[`OWNED_MODULES`]: ref:ethereum.forks.develop.eips.OWNED_MODULES
[`TARGETS`]: ref:ethereum.forks.develop.eips.TARGETS
"""

from typing import Final, FrozenSet, Mapping, Tuple

EIP_8141: Final[bool] = True
"""
[EIP-8141] frame transactions.

[EIP-8141]: https://eips.ethereum.org/EIPS/eip-8141
"""

REQUIRES: Final[Mapping[int, FrozenSet[int]]] = {}
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
}
"""
Modules that exist only for one EIP, keyed by EIP number and named
relative to the fork package.

An owned module is imported from shared modules only under its EIP's
flag and is removed when the guards are folded with that EIP disabled.
"""

TARGETS: Final[Mapping[str, FrozenSet[int]]] = {
    "bogota": frozenset({8141}),
}
"""
The EIPs each generated fork enables, keyed by the fork's short name.

Every flag not listed for a fork folds to its disabled branch when that
fork is generated. While a fork is open this is its kitchen sink; the
set shrinks only when an EIP leaves the fork.
"""
