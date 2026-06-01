"""
Fail-closed helper for asserting on a transaction's EELS execution trace.

A trace expectation is a callable attached to a :class:`Transaction`
(``trace_expectations``) that receives a :class:`TransactionTraceView`
over that transaction's reference trace and raises ``AssertionError``
when the intended execution path was not taken. Expectations run during
filling and are never serialized into the fixture.

The accessor is deliberately fail-closed: asking for a step that is
absent raises rather than returning ``None`` or an empty result. A
premise that silently stops being exercised — for example a transaction
that runs out of gas before ever reaching the opcode under test —
therefore fails the expectation instead of passing vacuously. (The
transaction-level fail-closed case — a transaction that declares
expectations but produced no trace at all — is handled where
expectations are dispatched, in ``BaseTest.verify_trace_assertions``.)
"""

from dataclasses import dataclass
from typing import Callable, List, Optional

from execution_testing.client_clis.assertion_tracer import TraceStep


class TransactionTraceView:
    """Fail-closed accessor over a single transaction's trace steps."""

    def __init__(self, steps: List[TraceStep]) -> None:
        self._steps = steps

    def steps_for(self, op_name: str) -> List[TraceStep]:
        """Return all steps whose opcode mnemonic is ``op_name``."""
        return [step for step in self._steps if step.op_name == op_name]

    def require_step(self, op_name: str, occurrence: int = 0) -> TraceStep:
        """
        Return the ``occurrence``-th step for ``op_name``.

        Raise ``AssertionError`` if there is no such step. This is the
        fail-closed anchor: an expectation targeting an opcode the
        transaction never reached fails loudly, instead of passing
        because a "for each matching step" loop body simply never ran.
        """
        matches = self.steps_for(op_name)
        if occurrence >= len(matches):
            seen = ", ".join(sorted({step.op_name for step in self._steps}))
            raise AssertionError(
                f"trace has no {op_name!r} step at occurrence {occurrence} "
                f"(found {len(matches)}); opcodes reached: {seen or '<none>'}"
            )
        return matches[occurrence]


TraceAssertion = Callable[[TransactionTraceView], None]
"""A fill-time check over one tx's :class:`TransactionTraceView`."""


@dataclass(frozen=True)
class TraceExpectation:
    """
    Declarative, fail-closed expectation over a single trace step.

    Attach to a transaction via ``Transaction(trace_expectations=[...])``.
    Instances are callable, so they satisfy :data:`TraceAssertion` and
    can be mixed with plain callables in the same list.

    Only the provided fields are checked, and the step is located with
    ``require_step`` — so a missing opcode raises (the intended path was
    not reached) rather than passing vacuously.

    Fields:
        op_name: Opcode mnemonic to match, e.g. ``"CALL"``.
        occurrence: Which matching step (0-based); the second ``CALL`` is
            ``occurrence=1``.
        error: Exact exception class name the step must carry, e.g.
            ``"OutOfGasError"``.
        charged: ``False`` requires the step recorded no charge
            (``gas_charged is None`` — it halted before charging);
            ``True`` requires a charge.
        gas_short_by: Require the step to have been exactly this many gas
            short of its requirement: ``gas_remaining + gas_short_by ==
            gas_required``. ``1`` is the out-of-gas-by-one boundary; ``0``
            means it had exactly the required amount.
        check: Predicate over the matched ``TraceStep`` for relational or
            computed premises beyond the above, e.g.
            ``check=lambda step: step.gas_remaining == step.gas_required``.
            The expectation asserts it returns ``True``. For logic
            spanning multiple steps, attach a plain callable instead.
    """

    op_name: str
    occurrence: int = 0
    error: Optional[str] = None
    charged: Optional[bool] = None
    gas_short_by: Optional[int] = None
    check: Optional[Callable[[TraceStep], bool]] = None

    def __call__(self, tx_trace: TransactionTraceView) -> None:
        """Verify this expectation against one transaction's trace."""
        step = tx_trace.require_step(self.op_name, self.occurrence)
        label = f"{self.op_name}[{self.occurrence}]"
        if self.error is not None:
            assert step.error == self.error, (
                f"{label}: expected error {self.error!r}, got {step.error!r}"
            )
        if self.charged is not None:
            wanted = "a charge" if self.charged else "no charge"
            assert (step.gas_charged is not None) == self.charged, (
                f"{label}: expected {wanted}, "
                f"got gas_charged={step.gas_charged}"
            )
        if self.gas_short_by is not None:
            required = step.gas_required
            assert required is not None, (
                f"{label}: gas_short_by set but the step recorded no gas "
                "requirement (no charge or check)"
            )
            assert step.gas_remaining + self.gas_short_by == required, (
                f"{label}: expected gas_remaining + {self.gas_short_by} "
                f"== gas_required, got gas_remaining={step.gas_remaining}, "
                f"gas_required={required}"
            )
        if self.check is not None:
            assert self.check(step), (
                f"{label}: custom check failed "
                f"(gas_remaining={step.gas_remaining}, "
                f"gas_required={step.gas_required}, error={step.error!r})"
            )
