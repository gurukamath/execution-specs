"""
Purpose-built EVM tracer for fill-time trace assertions.

This tracer is independent of the EIP-3155 tracer: that tracer exists to
emit wire-format-conformant output for client comparison, and its
representation (hex strings, precompile exclusion, deferred ``gasCost``)
is shaped by that goal. Trace assertions instead want a small, typed,
queryable record that we own and can keep stable.

It implements the ``EvmTracer`` protocol structurally (a ``__call__``
taking the EVM and a ``TraceEvent``), so it can be added to the t8n
``GroupTracer`` alongside any other tracer. It only depends on the spec's
``ethereum.trace`` events, never on spec-tools internals.

Each ``TraceStep`` separates a failed pre-charge check from an actual
deduction:

- ``gas_remaining``: gas available when the opcode started.
- ``gas_charged``: the opcode's own gas charge (the first
  ``GasAndRefund`` / ``charge_gas`` after its ``OpStart`` — an opcode
  charges before it recurses, so a later charge from a sub-frame
  precompile is ignored). ``None`` means it halted before charging
  (e.g. an out-of-gas at a ``check_gas`` gate), distinct from a charge
  of ``0``.
- ``gas_checks``: every ``GasCheck`` / ``check_gas`` the opcode performs,
  in order. A single opcode may check more than once (e.g. CALL checks
  static gas, then delegation gas), and a failed check is still
  recorded.
- ``gas_required`` (derived): what the opcode needed to proceed — its
  charge, or the check it failed on. Lets assertions compare
  availability against requirement without indexing ``gas_checks``.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ethereum.trace import (
    GasAndRefund,
    GasCheck,
    OpException,
    OpStart,
    TraceEvent,
    TransactionEnd,
)


@dataclass
class TraceStep:
    """A single executed opcode in the reference (EELS) trace."""

    pc: int
    op_name: str
    gas_remaining: int
    depth: int
    gas_charged: Optional[int] = None
    gas_checks: List[int] = field(default_factory=list)
    error: Optional[str] = None
    stack: List[int] = field(default_factory=list)

    @property
    def gas_required(self) -> Optional[int]:
        """
        Gas this opcode required to proceed.

        The amount it charged, or — if it never charged — the last
        sufficiency check it made (the one it failed on, for an
        out-of-gas). ``None`` if it neither charged nor checked.
        """
        if self.gas_charged is not None:
            return self.gas_charged
        if self.gas_checks:
            return self.gas_checks[-1]
        return None


class AssertionTracer:
    """
    Record a clean, typed, in-memory trace for fill-time assertions.

    One ``TraceStep`` is recorded per executed opcode; completed
    transactions are retained keyed by transaction hash, so a caller can
    map an authored transaction to its trace robustly (independent of
    ordering or rejected transactions). System transactions (no
    ``index_in_block`` / ``tx_hash``) are ignored, matching the EIP-3155
    tracer.
    """

    transactions: Dict[str, List[TraceStep]]

    def __init__(self) -> None:
        self._transaction_environment: Any = None
        self._active: List[TraceStep] = []
        self.transactions = {}

    def __call__(self, evm: Any, event: TraceEvent) -> None:
        """Record a single trace event."""
        transaction_environment = evm.message.tx_env
        if (
            transaction_environment.index_in_block is None
            or transaction_environment.tx_hash is None
        ):
            return  # system transaction

        if self._transaction_environment is not transaction_environment:
            self._active = []
            self._transaction_environment = transaction_environment

        if isinstance(event, OpStart):
            self._active.append(
                TraceStep(
                    pc=int(evm.pc),
                    op_name=str(event.op).split(".")[-1],
                    gas_remaining=int(evm.gas_left),
                    depth=int(evm.message.depth),
                    stack=[int(value) for value in evm.stack],
                )
            )
        elif isinstance(event, GasAndRefund):
            # The opcode's own charge: the first charge_gas after its
            # OpStart. An opcode charges before recursing, so a later
            # charge from a sub-frame precompile is ignored. ``None``
            # means it halted before charging (e.g. a check_gas gate).
            if self._active and self._active[-1].gas_charged is None:
                self._active[-1].gas_charged = int(event.gas_cost)
        elif isinstance(event, GasCheck):
            # Record every gas-sufficiency check (check_gas), separate
            # from actual charges. A check that fails (out of gas) is
            # still recorded, so an OOG at a pre-charge gate leaves the
            # checked amount here with charges still empty.
            if self._active:
                self._active[-1].gas_checks.append(int(event.gas_cost))
        elif isinstance(event, OpException):
            if self._active and self._active[-1].error is None:
                self._active[-1].error = type(event.error).__name__
        elif isinstance(event, TransactionEnd):
            tx_hash_key = bytes(transaction_environment.tx_hash).hex()
            self.transactions[tx_hash_key] = self._active
            self._active = []
            self._transaction_environment = None

    def transaction_traces(self) -> Dict[str, List[TraceStep]]:
        """Return completed per-transaction traces, keyed by tx hash."""
        return self.transactions
