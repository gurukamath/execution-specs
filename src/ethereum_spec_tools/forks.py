"""
Ethereum Forks.

Detects Python packages that specify Ethereum hardforks.
"""

import importlib
import importlib.abc
import importlib.util
import pkgutil
import random
import sys
from contextlib import AbstractContextManager
from dataclasses import astuple, dataclass
from enum import Enum, auto
from importlib.machinery import ModuleSpec, PathFinder
from pathlib import Path
from pkgutil import ModuleInfo
from tempfile import TemporaryDirectory
from types import ModuleType
from typing import (
    TYPE_CHECKING,
    Any,
    Dict,
    Final,
    FrozenSet,
    Iterator,
    List,
    Mapping,
    Optional,
    Protocol,
    Type,
    TypeVar,
    cast,
)

from ethereum_types.numeric import U64, U256, Uint
from typing_extensions import override

from ethereum.fork_criteria import ByBlockNumber, ByTimestamp, Unscheduled

if TYPE_CHECKING:
    from ethereum.fork_criteria import ForkCriteria


class ConsensusType(Enum):
    """
    How a fork chooses its canonical chain.
    """

    PROOF_OF_WORK = auto()
    PROOF_OF_STAKE = auto()

    def is_pow(self) -> bool:
        """
        Returns True if self == PROOF_OF_WORK.
        """
        return self == ConsensusType.PROOF_OF_WORK

    def is_pos(self) -> bool:
        """
        Returns True if self == PROOF_OF_STAKE.
        """
        return self == ConsensusType.PROOF_OF_STAKE


H = TypeVar("H", bound="Hardfork")
ForkCriteriaArgument = ByBlockNumber | ByTimestamp | Unscheduled | None


@dataclass(frozen=True)
class ForkOverrides:
    """
    Temporary hardfork override values.
    """

    fork_criteria: ForkCriteriaArgument = None
    blob_target_gas_per_block: U64 | None = None
    gas_per_blob: U64 | None = None
    blob_min_gasprice: Uint | None = None
    blob_base_fee_update_fraction: Uint | None = None
    max_blob_gas_per_block: U64 | None = None
    blob_schedule_target: U64 | None = None
    blob_schedule_max: U64 | None = None
    enabled_eips: FrozenSet[int] | None = None
    """
    EIPs whose feature guards fold to their enabled branch; every other
    flag of the template folds to its disabled branch. `None` leaves the
    template's guards alone. A set always folds, even one that enables
    every flag of the template, so that fixtures filled against a set
    come from the same transformation as the generated forks.
    """

    def is_empty(self) -> bool:
        """
        Return true when all override values are unset.
        """
        return all(value is None for value in astuple(self))

    @staticmethod
    def _matches_field(override: object | None, on: object, name: str) -> bool:
        if override is None:
            return True

        try:
            default = getattr(on, name)
        except AttributeError:
            return False

        return override == default

    def matches_template(
        self,
        template: "Hardfork",
    ) -> bool:
        """
        Return true when the requested overrides match the template.
        """
        if self.is_empty():
            return True

        if (
            self.fork_criteria is not None
            and self.fork_criteria != template.criteria
        ):
            return False

        if self.enabled_eips is not None:
            return False

        fork_mod = template.module("fork")
        gas_costs = template.module("vm.gas").GasCosts

        checks = (
            (
                self.max_blob_gas_per_block,
                fork_mod,
                "MAX_BLOB_GAS_PER_BLOCK",
            ),
            (
                self.blob_target_gas_per_block,
                gas_costs,
                "BLOB_TARGET_GAS_PER_BLOCK",
            ),
            (self.gas_per_blob, gas_costs, "PER_BLOB"),
            (
                self.blob_min_gasprice,
                gas_costs,
                "BLOB_MIN_GASPRICE",
            ),
            (
                self.blob_base_fee_update_fraction,
                gas_costs,
                "BLOB_BASE_FEE_UPDATE_FRACTION",
            ),
            (
                self.blob_schedule_target,
                gas_costs,
                "BLOB_SCHEDULE_TARGET",
            ),
            (
                self.blob_schedule_max,
                gas_costs,
                "BLOB_SCHEDULE_MAX",
            ),
        )

        return all(self._matches_field(*x) for x in checks)


DEVELOP_FORK_NAME: Final[str] = "develop"
"""
Short name of the development source package under `ethereum.forks`.

It is a complete fork package with every EIP under consideration behind
a feature flag, from which the forks under development are generated.
It is not itself a fork: discovery leaves it out unless asked for it,
so that lints, documentation and fills only ever see generated forks.
"""


class Target(Protocol):
    """
    An entry of a development source's `TARGETS` table: an EIP set that
    fixtures are filled against. Mirrors the record the source defines.
    """

    @property
    def eips(self) -> FrozenSet[int]:
        """The EIPs the target enables beyond its base."""
        ...

    @property
    def base(self) -> Optional[str]:
        """The target this one extends, if any."""
        ...

    @property
    def generated(self) -> bool:
        """Whether the fold tool writes the target as a fork package."""
        ...


def resolve_target(targets: Mapping[str, Target], name: str) -> FrozenSet[int]:
    """
    Return the EIP set of `targets[name]`, including those of its bases.
    """
    resolved: set[int] = set()
    chain: list[str] = []
    current: Optional[str] = name
    while current is not None:
        if current in chain:
            cycle = " -> ".join([*chain, current])
            raise ValueError(f"target `{name}` has a base cycle: {cycle}")
        if current not in targets:
            raise ValueError(
                f"target `{current}`, reached from `{name}`, is not in"
                " `TARGETS`"
            )
        chain.append(current)
        resolved |= set(targets[current].eips)
        current = targets[current].base
    return frozenset(resolved)


def pseudo_fork_targets() -> Mapping[str, FrozenSet[int]]:
    """
    Return the resolved EIP set of every `TARGETS` entry the development
    source does not generate, keyed by target name.

    These are the pseudo-forks: devnet EIP sets that the test tooling
    folds from the source on demand instead of reading a fork package.
    """
    eips = importlib.import_module(f"ethereum.forks.{DEVELOP_FORK_NAME}.eips")
    targets: Mapping[str, Target] = eips.TARGETS
    return {
        name: resolve_target(targets, name)
        for name, target in targets.items()
        if not target.generated
    }


class Hardfork:
    """
    Metadata associated with an Ethereum hardfork.
    """

    mod: ModuleType

    @classmethod
    def discover(
        cls: Type[H],
        submodule_search_locations: None | list[str] = None,
        include_develop: bool = False,
    ) -> List[H]:
        """
        Find packages which contain Ethereum hardfork specifications.

        The development source package is included only when
        `include_develop` is set.
        """
        if submodule_search_locations is None:
            ethereum_forks = importlib.import_module("ethereum.forks")
        else:
            spec = ModuleSpec("ethereum.forks", loader=None, is_package=True)
            spec.submodule_search_locations = submodule_search_locations

            ethereum_forks = importlib.util.module_from_spec(spec)
            if spec.loader and hasattr(spec.loader, "exec_module"):
                spec.loader.exec_module(ethereum_forks)

        path = getattr(ethereum_forks, "__path__", None)
        if path is None:
            raise ValueError("module `ethereum` has no path information")

        modules = pkgutil.iter_modules(path, ethereum_forks.__name__ + ".")
        modules = (module for module in modules if module.ispkg)
        forks: List[H] = []

        for pkg in modules:
            if (
                not include_develop
                and pkg.name.rsplit(".", 1)[-1] == DEVELOP_FORK_NAME
            ):
                continue
            try:
                mod = sys.modules[pkg.name]
                if hasattr(mod, "FORK_CRITERIA"):
                    forks.append(cls(mod))
                continue
            except KeyError:
                pass

            # Use find_spec() to find the module specification.
            if isinstance(pkg.module_finder, importlib.abc.MetaPathFinder):
                found = pkg.module_finder.find_spec(pkg.name, None)
            elif isinstance(pkg.module_finder, importlib.abc.PathEntryFinder):
                found = pkg.module_finder.find_spec(pkg.name)
            else:
                raise Exception(
                    "unsupported module_finder "
                    f"`{type(pkg.module_finder).__name__}` while finding spec "
                    f"for `{pkg.name}`"
                )

            if not found:
                raise Exception(f"unable to find module spec for {pkg.name}")

            # Load the module from the spec.
            mod = importlib.util.module_from_spec(found)

            sys.modules[pkg.name] = mod

            # Execute the module in its namespace.
            if found.loader:
                found.loader.exec_module(mod)
            else:
                raise Exception(f"No loader found for module {pkg.name}")

            if hasattr(mod, "FORK_CRITERIA"):
                forks.append(cls(mod))

        # Timestamps are bigger than block numbers, so this always works.
        forks.sort(key=lambda fork: fork.criteria)

        return forks

    @classmethod
    def by_short_name(cls: Type[H], name: str) -> H:
        """
        Return the hardfork matching the given short name.
        """
        for fork in cls.discover():
            if fork.short_name == name:
                return fork

        raise ValueError(f"unknown hardfork `{name}`")

    @classmethod
    def load(cls: Type[H], config_dict: Dict["ForkCriteria", str]) -> List[H]:
        """
        Load the forks from a config dict specifying fork blocks and
        timestamps.
        """
        config = sorted(config_dict.items(), key=lambda x: x[0])

        forks = []

        for criteria, name in config:
            mod = importlib.import_module("ethereum." + name)
            mod.FORK_CRITERIA = criteria  # type: ignore
            forks.append(cls(mod))

        return forks

    @classmethod
    def load_from_json(cls: Type[H], json: Any) -> List[H]:
        """
        Load fork config from the json format used by Geth.

        Does not support some forks that only exist on Mainnet. Use
        `discover()` for Mainnet.
        """
        from ethereum.fork_criteria import ByBlockNumber, ByTimestamp

        c = json["config"]
        config = {
            ByBlockNumber(0): "frontier",
            ByBlockNumber(c["homesteadBlock"]): "homestead",
            ByBlockNumber(c["eip150Block"]): "tangerine_whistle",
            ByBlockNumber(c["eip155Block"]): "spurious_dragon",
            ByBlockNumber(c["byzantiumBlock"]): "byzantium",
            ByBlockNumber(c["constantinopleBlock"]): "constantinople",
            ByBlockNumber(c["istanbulBlock"]): "istanbul",
            ByBlockNumber(c["berlinBlock"]): "berlin",
            ByBlockNumber(c["londonBlock"]): "london",
            ByBlockNumber(c["mergeForkBlock"]): "paris",
            ByTimestamp(c["shanghaiTime"]): "shanghai",
        }

        if "daoForkBlock" in c:
            raise Exception(
                "Hardfork.load_from_json() does not support Mainnet"
            )

        return cls.load(config)

    @staticmethod
    def clone(
        template: H | str,
        overrides: ForkOverrides | None = None,
    ) -> "TemporaryHardfork":
        """
        Create a temporary clone of an existing fork, optionally tweaking its
        parameters.
        """
        from .new_fork.builder import ForkBuilder

        if overrides is None:
            overrides = ForkOverrides()

        maybe_directory: TemporaryDirectory | None = TemporaryDirectory()

        try:
            assert maybe_directory is not None
            directory: TemporaryDirectory = maybe_directory

            if isinstance(template, str):
                template_name = template
            else:
                template_name = template.short_name

            clone_name = (
                f"{template_name}_clone{random.randrange(1_000_000_000)}"
            )

            builder = ForkBuilder(template_name, clone_name)

            builder.output = Path(directory.name)

            if overrides.fork_criteria is not None:
                builder.fork_criteria = overrides.fork_criteria

            if overrides.blob_target_gas_per_block is not None:
                builder.modify_target_blob_gas_per_block(
                    overrides.blob_target_gas_per_block
                )

            if overrides.gas_per_blob is not None:
                builder.modify_gas_per_blob(overrides.gas_per_blob)

            if overrides.blob_min_gasprice is not None:
                builder.modify_min_blob_gasprice(overrides.blob_min_gasprice)

            if overrides.blob_base_fee_update_fraction is not None:
                builder.modify_blob_base_fee_update_fraction(
                    overrides.blob_base_fee_update_fraction
                )

            if overrides.max_blob_gas_per_block is not None:
                builder.modify_max_blob_gas_per_block(
                    overrides.max_blob_gas_per_block
                )

            if overrides.blob_schedule_target is not None:
                builder.modify_blob_schedule_target(
                    overrides.blob_schedule_target
                )

            if overrides.blob_schedule_max is not None:
                builder.modify_blob_schedule_max(overrides.blob_schedule_max)

            if overrides.enabled_eips is not None:
                builder.fold_feature_guards(overrides.enabled_eips)

            builder.build()

            clone_forks = Hardfork.discover([directory.name])
            if len(clone_forks) != 1:
                raise Exception("len(clone_forks) != 1")
            if clone_forks[0].short_name != clone_name:
                raise Exception("found incorrect fork")

            value = TemporaryHardfork(clone_forks[0].mod, directory)
            maybe_directory = None
            return value
        finally:
            if maybe_directory is not None:
                maybe_directory.cleanup()

    def __init__(self, mod: ModuleType) -> None:
        self.mod = mod

    @property
    def consensus(self) -> ConsensusType:
        """
        How this fork chooses its canonical chain.
        """
        if hasattr(self.module("fork"), "validate_proof_of_work"):
            return ConsensusType.PROOF_OF_WORK
        else:
            return ConsensusType.PROOF_OF_STAKE

    @property
    def criteria(self) -> "ForkCriteria":
        """
        Criteria to trigger this hardfork.
        """
        from ethereum.fork_criteria import ForkCriteria

        criteria = self.mod.FORK_CRITERIA
        assert isinstance(criteria, ForkCriteria)
        return criteria

    @property
    def block(self) -> Uint:
        """
        Block number of the first block in this hard fork.
        """
        from ethereum.fork_criteria import ByBlockNumber

        if isinstance(self.criteria, ByBlockNumber):
            return self.criteria.block_number
        else:
            raise AttributeError

    @property
    def timestamp(self) -> U256:
        """
        Timestamp of the first block in this hard fork.
        """
        from ethereum.fork_criteria import ByTimestamp

        if isinstance(self.criteria, ByTimestamp):
            return self.criteria.timestamp
        else:
            raise AttributeError

    def has_activated(self, block_number: Uint, timestamp: U256) -> bool:
        """
        Check whether this fork has activated.
        """
        return self.criteria.check(block_number, timestamp)

    @property
    def path(self) -> Optional[str]:
        """
        Path to the module containing this hard fork.
        """
        got = getattr(self.mod, "__path__", None)
        if got is None or isinstance(got, str):
            return got

        try:
            assert isinstance(got[0], str)
            return got[0]
        except IndexError:
            return None

    @property
    def short_name(self) -> str:
        """
        Short name (without the `ethereum.` prefix) of the hard fork.
        """
        return self.mod.__name__.split(".")[-1]

    @property
    def name(self) -> str:
        """
        Name of the hard fork.
        """
        return self.mod.__name__

    @property
    def title_case_name(self) -> str:
        """
        Name of the hard fork.
        """
        if self.short_name.startswith("bpo"):
            return "BPO" + self.short_name[3:].replace("_", " ")

        return self.short_name.replace("_", " ").title()

    def __repr__(self) -> str:
        """
        Return repr(self).
        """
        return (
            self.__class__.__name__
            + "("
            + f"name={self.name!r}, "
            + f"criteria={self.criteria}, "
            + "..."
            + ")"
        )

    def import_module(self) -> ModuleType:
        """
        Return the module containing this specification.
        """
        return self.mod

    def module(self, name: str) -> Any:
        """
        Import if necessary, and return the given module belonging to this hard
        fork.
        """
        # Handle the "already imported" case early.
        full_name = self.mod.__name__ + "." + name
        try:
            return sys.modules[full_name]
        except KeyError:
            pass

        # Import each package (including parents), returning the last one.
        fragments = name.split(".")
        mod = self.mod

        for fragment in fragments:
            name = mod.__name__ + "." + fragment
            try:
                mod = sys.modules[name]
                continue
            except KeyError:
                pass

            if mod.__spec__ is None:
                raise ImportError(f"{mod.__name__} is not a package")
            if mod.__spec__.submodule_search_locations is None:
                raise ImportError(f"{mod.__name__} is not a package")

            spec = PathFinder.find_spec(
                name,
                path=mod.__spec__.submodule_search_locations,
                target=mod,
            )
            if spec is None or spec.loader is None:
                raise ModuleNotFoundError(name)

            mod = importlib.util.module_from_spec(spec)
            sys.modules[name] = mod
            if spec.loader and hasattr(spec.loader, "exec_module"):
                spec.loader.exec_module(mod)

        assert mod.__name__ == full_name
        return mod

    def iter_modules(self) -> Iterator[ModuleInfo]:
        """
        Iterate through the (sub-)modules describing this hardfork.
        """
        if self.mod.__path__ is None:
            raise ValueError(f"cannot walk {self.name}, path is None")

        return pkgutil.iter_modules(self.mod.__path__, self.name + ".")

    def walk_packages(self) -> Iterator[ModuleInfo]:
        """
        Iterate recursively through the (sub-)modules describing this hardfork.
        """
        if self.mod.__path__ is None:
            raise ValueError(f"cannot walk {self.name}, path is None")

        return pkgutil.walk_packages(self.mod.__path__, self.name + ".")


class TemporaryHardfork(Hardfork, AbstractContextManager):
    """
    Short-lived `Hardfork` located in a temporary directory.
    """

    directory: TemporaryDirectory | None

    def __init__(self, mod: ModuleType, directory: TemporaryDirectory) -> None:
        super().__init__(mod)
        self.directory = directory

    @override
    def __exit__(self, *args: object, **kwargs: object) -> None:
        del args
        del kwargs

        assert self.directory is not None
        self.directory.cleanup()
        self.directory = None

        # Intentionally break ourselves. Once the directory is gone, imports
        # won't work.
        self.mod = cast(ModuleType, None)
