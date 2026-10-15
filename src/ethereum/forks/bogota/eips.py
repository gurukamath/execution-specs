"""
Feature flags for the EIPs this fork carries while it is under development.

A fork under development holds every EIP considered for inclusion, each
behind a flag defined here. A flag is read only as the whole test of an
`if` statement, so that a mechanical pass can fold the guards away once
the fork's EIP list is final. The checked-in value of every flag is
`True`: this tree is the union of everything the fork may ship. Devnet
and test fills override individual flags to execute a chosen subset.

[`REQUIRES`] records which EIPs an EIP builds on, and [`OWNED_MODULES`]
records the modules that exist only for one EIP. Both are read by the
tooling that validates an EIP subset and folds the guards.

[`REQUIRES`]: ref:ethereum.forks.bogota.eips.REQUIRES
[`OWNED_MODULES`]: ref:ethereum.forks.bogota.eips.OWNED_MODULES
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
