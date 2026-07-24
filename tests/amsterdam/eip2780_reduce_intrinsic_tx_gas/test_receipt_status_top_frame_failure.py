"""
Receipt status for transactions that fail in the top-frame layer.

A transaction that out-of-gases on a top-frame charge (EIP-2780 /
EIP-8037) never dispatches into the EVM, but it is still included in
the block and must produce a receipt with ``succeeded=False``. The
receipt status byte is committed to the header ``receiptsRoot``, so a
client that mis-reports it computes a different root and rejects the
(valid) block.

Every pre-existing top-frame OOG test places the failing transaction
alone in its block. An implementation that derives the receipt status
from shared per-block execution state can then report the *previous*
transaction's status for a top-frame failure and still pass those
tests, because the stale value in a fresh block happens to be
"failed". These tests pin the status by sandwiching the top-frame
failure between successful transactions in a single block, making the
status byte load-bearing in ``receiptsRoot``.

Regression: nimbus-eth1 ``1f8dd2122`` skipped its post-execution hook
(the only writer of the per-block status flag) for top-frame failures,
producing ``status=1`` receipts for such transactions on
glamsterdam-devnet-7 and rejecting finalized canonical blocks.
"""

from enum import Enum, auto

import pytest
from execution_testing import (
    Account,
    Address,
    Alloc,
    Block,
    BlockchainTestFiller,
    Fork,
    Op,
    RecipientType,
    Transaction,
    TransactionReceipt,
)

from ...prague.eip7702_set_code_tx.spec import Spec as Spec7702
from .spec import ref_spec_2780

REFERENCE_SPEC_GIT_PATH = ref_spec_2780.git_path
REFERENCE_SPEC_VERSION = ref_spec_2780.version

pytestmark = pytest.mark.valid_from("Amsterdam")


class TopFrameFailureMode(Enum):
    """The top-frame charge the failing transaction out-of-gases on."""

    CREATE_STATE_OOG = auto()
    NEW_ACCOUNT_STATE_OOG = auto()
    DELEGATED_REGULAR_OOG = auto()


@pytest.mark.parametrize(
    "failure_mode",
    [
        pytest.param(
            TopFrameFailureMode.CREATE_STATE_OOG,
            id="create_state_oog",
        ),
        pytest.param(
            TopFrameFailureMode.NEW_ACCOUNT_STATE_OOG,
            id="new_account_state_oog",
        ),
        pytest.param(
            TopFrameFailureMode.DELEGATED_REGULAR_OOG,
            id="delegated_regular_oog",
        ),
    ],
)
def test_receipt_status_top_frame_oog_between_successful_txs(
    fork: Fork,
    pre: Alloc,
    blockchain_test: BlockchainTestFiller,
    failure_mode: TopFrameFailureMode,
) -> None:
    """
    Pin the failed receipt status of a top-frame OOG transaction that
    sits between two successful transactions in one block.

    The middle transaction passes the intrinsic check but out-of-gases
    on a top-frame charge before any EVM bytecode runs:

    - ``create_state_oog``: contract creation; the created account's
      ``NEW_ACCOUNT`` state charge fires at the top frame and the gas
      limit is one short of covering it.
    - ``new_account_state_oog``: value transfer to an empty recipient;
      the ``NEW_ACCOUNT`` state charge fires and the gas limit is one
      short.
    - ``delegated_regular_oog``: recipient holds an EIP-7702
      delegation; the ``COLD_ACCOUNT_ACCESS`` regular charge fires and
      the gas limit is one short.

    The failing transaction burns its full gas limit, bumps the sender
    nonce, and must produce a ``succeeded=False`` receipt between two
    ``succeeded=True`` receipts. A client that carries execution status
    across transactions in a block reports ``status=1`` for the middle
    receipt and diverges on ``receiptsRoot``.
    """
    gas_price = 1_000_000_000
    value = 1

    sender_initial_balance = 10**18
    ok_sender_1 = pre.fund_eoa(sender_initial_balance)
    ok_sender_2 = pre.fund_eoa(sender_initial_balance)
    fail_sender = pre.fund_eoa(sender_initial_balance)
    # Alive via balance, so the successful transfers to it incur no
    # top-frame charge and consume exactly their intrinsic gas.
    ok_recipient = pre.fund_eoa(amount=1)

    intrinsic_cost = fork.transaction_intrinsic_cost_calculator()

    fail_target: Address | None = None
    fail_target_post: Account | None = None
    if failure_mode is TopFrameFailureMode.CREATE_STATE_OOG:
        intrinsic_gas = intrinsic_cost(
            contract_creation=True,
            return_cost_deducted_prior_execution=True,
        )
        top_frame_state_gas = fork.transaction_top_frame_state_gas(
            contract_creation=True,
        )
        assert top_frame_state_gas > 0, (
            "contract creation must charge NEW_ACCOUNT at the top frame"
        )
        fail_gas_limit = intrinsic_gas + top_frame_state_gas - 1
        fail_to: Address | None = None
    elif failure_mode is TopFrameFailureMode.NEW_ACCOUNT_STATE_OOG:
        intrinsic_gas = intrinsic_cost(
            sends_value=True,
            recipient_type=RecipientType.EMPTY_ACCOUNT,
            return_cost_deducted_prior_execution=True,
        )
        top_frame_state_gas = fork.transaction_top_frame_state_gas(
            sends_value=True,
            recipient_type=RecipientType.EMPTY_ACCOUNT,
        )
        assert top_frame_state_gas > 0, (
            "value transfer to an empty recipient must charge "
            "NEW_ACCOUNT at the top frame"
        )
        fail_gas_limit = intrinsic_gas + top_frame_state_gas - 1
        fail_to = pre.fund_eoa(amount=0)
        fail_target = fail_to
        # The rolled-back transfer must not bring the recipient into
        # existence.
        fail_target_post = None
    elif failure_mode is TopFrameFailureMode.DELEGATED_REGULAR_OOG:
        delegated_to = pre.deploy_contract(code=Op.STOP)
        target_code = Spec7702.delegation_designation(delegated_to)
        fail_to = pre.deploy_contract(code=target_code)
        intrinsic_gas = intrinsic_cost(
            recipient_type=RecipientType.DELEGATION_7702,
            return_cost_deducted_prior_execution=True,
        )
        top_frame_gas = fork.transaction_top_frame_gas_calculator()(
            recipient_type=RecipientType.DELEGATION_7702,
        )
        assert top_frame_gas > 0, (
            "a delegated recipient must charge COLD_ACCOUNT_ACCESS "
            "at the top frame"
        )
        fail_gas_limit = intrinsic_gas + top_frame_gas - 1
        fail_target = fail_to
        fail_target_post = Account(balance=0, code=target_code)
    else:
        raise ValueError(f"unhandled failure mode: {failure_mode}")

    # The successful transfers go to an alive EOA: no top-frame charge,
    # no EVM execution, so each consumes exactly its intrinsic gas.
    ok_intrinsic_gas = intrinsic_cost(
        sends_value=True,
        recipient_type=RecipientType.EOA,
        return_cost_deducted_prior_execution=True,
    )
    assert (
        fork.transaction_top_frame_state_gas(
            sends_value=True,
            recipient_type=RecipientType.EOA,
        )
        == 0
    ), "an alive recipient must not incur a top-frame state charge"

    ok_tx_1 = Transaction(
        sender=ok_sender_1,
        to=ok_recipient,
        value=value,
        gas_limit=ok_intrinsic_gas,
        gas_price=gas_price,
        expected_receipt=TransactionReceipt(
            status=1,
            cumulative_gas_used=ok_intrinsic_gas,
        ),
    )
    fail_tx = Transaction(
        sender=fail_sender,
        to=fail_to,
        value=(
            value
            if failure_mode is TopFrameFailureMode.NEW_ACCOUNT_STATE_OOG
            else 0
        ),
        gas_limit=fail_gas_limit,
        gas_price=gas_price,
        expected_receipt=TransactionReceipt(
            status=0,
            gas_used=fail_gas_limit,
            cumulative_gas_used=ok_intrinsic_gas + fail_gas_limit,
        ),
    )
    ok_tx_2 = Transaction(
        sender=ok_sender_2,
        to=ok_recipient,
        value=value,
        gas_limit=ok_intrinsic_gas,
        gas_price=gas_price,
        expected_receipt=TransactionReceipt(
            status=1,
            cumulative_gas_used=2 * ok_intrinsic_gas + fail_gas_limit,
        ),
    )

    ok_sender_final_balance = (
        sender_initial_balance - value - ok_intrinsic_gas * gas_price
    )
    post: dict[Address, Account | None] = {
        ok_sender_1: Account(nonce=1, balance=ok_sender_final_balance),
        ok_sender_2: Account(nonce=1, balance=ok_sender_final_balance),
        ok_recipient: Account(balance=1 + 2 * value),
        # The failing transaction is included: the nonce bumps and the
        # full gas limit is paid, but nothing else happens.
        fail_sender: Account(
            nonce=1,
            balance=sender_initial_balance - fail_gas_limit * gas_price,
        ),
    }
    if failure_mode is TopFrameFailureMode.CREATE_STATE_OOG:
        post[fail_tx.created_contract] = None
    else:
        assert fail_target is not None
        post[fail_target] = fail_target_post

    blockchain_test(
        pre=pre,
        blocks=[Block(txs=[ok_tx_1, fail_tx, ok_tx_2])],
        post=post,
    )
