"""
EVM trace implementation that counts how many times each opcode is executed.
"""

from collections import defaultdict
from typing import Any

from ethereum.trace import EvmTracer, OpStart, TraceEvent

from .protocols import Evm


class CountTracer(EvmTracer):
    """
    EVM trace implementation that counts how many times each opcode is
    executed.
    """

    transaction_environment: object | None
    active_traces: defaultdict[str, int]

    def __init__(self) -> None:
        self.transaction_environment = None
        self.active_traces = defaultdict(lambda: 0)

    def __call__(self, evm: object, event: TraceEvent) -> None:
        """
        Create a trace of the event.
        """
        if not isinstance(event, OpStart):
            return

        assert isinstance(evm, Evm)

        # TODO: Rethink the tracer interface so it does not probe
        # fork-specific frame layouts. Recent forks merge the message
        # fields into the frame itself; older forks keep them on
        # `evm.message`.
        message: Any = getattr(evm, "message", evm)

        if self.transaction_environment is not message.tx_env:
            self.active_traces = defaultdict(lambda: 0)
            self.transaction_environment = message.tx_env

        self.active_traces[event.op.name] += 1

    def results(self) -> dict[str, int]:
        """
        Return and clear the current opcode counts.
        """
        results = self.active_traces
        self.active_traces = defaultdict(lambda: 0)
        self.transaction_environment = None
        return results
