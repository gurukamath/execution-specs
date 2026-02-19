"""
Tests for EIP-2780 Reduce Transaction Intrinsic Cost.

Tests that value-moving CALL opcodes charge gas correctly under
Amsterdam's split cold access and restructured value transfer costs.
"""

import enum
from typing import Callable, Optional

import pytest
from execution_testing import (
    AccessList,
    Account,
    Address,
    Alloc,
    Block,
    BlockchainTestFiller,
    Bytecode,
    Fork,
    Op,
    Transaction,
)
from execution_testing.forks.gas_costs import GasCosts

from .spec import ref_spec_2780

REFERENCE_SPEC_GIT_PATH = ref_spec_2780.git_path
REFERENCE_SPEC_VERSION = ref_spec_2780.version

pytestmark = pytest.mark.valid_from("Amsterdam")


class AccessScenario(enum.Enum):
    """Which access cost threshold is being tested."""

    WARM = "warm"
    COLD_NOCODE = "cold_nocode"
    COLD_CODE = "cold_code"


class AccessSuccess(enum.Enum):
    """Whether the gas check at the tested threshold passes."""

    OOG = "oog"
    SUCCESS = "success"


def compute_scenario_gas(
    access: AccessScenario,
    gsc: GasCosts,
) -> int:
    """Return the gas threshold for the given access scenario."""
    match access:
        case AccessScenario.WARM:
            return gsc.G_WARM_ACCOUNT_ACCESS
        case AccessScenario.COLD_NOCODE:
            return gsc.G_COLD_ACCOUNT_COST_NOCODE
        case AccessScenario.COLD_CODE:
            return gsc.G_COLD_ACCOUNT_COST_CODE


PostFn = Callable[[Address, Address, int, bool], dict[Address, Account]]


def _run_call_test(
    fork: Fork,
    pre: Alloc,
    blockchain_test: BlockchainTestFiller,
    access: AccessScenario,
    success: AccessSuccess,
    caller_code_fn: Callable[[Address, int], Bytecode],
    n_args: int,
    value: int,
    has_value_transfer: bool,
    account_new: bool,
    post_fn: PostFn,
) -> None:
    """
    Core logic shared by all CALL-family opcode tests.

    Deploys or allocates a target, builds a caller that invokes it,
    and asserts post-state based on the access/success combination.
    """
    gsc = fork.gas_costs()
    target_is_warm = access == AccessScenario.WARM
    target_has_code = not account_new

    if account_new:
        target = pre.empty_account()
    else:
        target = pre.deploy_contract(code=Op.STOP)

    code = caller_code_fn(target, value)
    caller = pre.deploy_contract(code=code, balance=value)
    alice = pre.fund_eoa()

    access_list: Optional[list[AccessList]] = (
        [AccessList(address=target, storage_keys=[])]
        if target_is_warm
        else None
    )

    intrinsic_cost = fork.transaction_intrinsic_cost_calculator()(
        access_list=access_list,
        recipient_is_contract=True,
        return_cost_deducted_prior_execution=True,
    )
    bytecode_cost = gsc.G_VERY_LOW * n_args

    # Value cost depends on whether the target is new or existing.
    value_cost = 0
    if has_value_transfer and value > 0:
        if account_new:
            value_cost = gsc.G_STATE_UPDATE + gsc.G_NEW_ACCOUNT
        else:
            value_cost = 2 * gsc.G_STATE_UPDATE

    # Gas for the tested threshold, minus 1 for OOG.
    scenario_gas = compute_scenario_gas(access, gsc)
    if success == AccessSuccess.OOG:
        scenario_gas -= 1

    gas_limit = intrinsic_cost + bytecode_cost + scenario_gas

    # Overall OOG: true unless we pass the highest applicable
    # threshold for this target.
    if success == AccessSuccess.OOG:
        is_oog = True
    elif access in (AccessScenario.WARM, AccessScenario.COLD_CODE):
        is_oog = False
    else:
        # COLD_NOCODE + SUCCESS: only overall success if target
        # has no code (no second check_gas to fail).
        is_oog = target_has_code

    if not is_oog:
        gas_limit += value_cost

    tx = Transaction(
        sender=alice,
        to=caller,
        gas_limit=gas_limit,
        access_list=access_list,
    )

    post = post_fn(caller, target, value, is_oog)

    blockchain_test(
        pre=pre,
        blocks=[Block(txs=[tx])],
        post=post,
    )


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(0, id="zero_value"),
        pytest.param(1, id="non-zero_value"),
    ],
)
@pytest.mark.parametrize(
    "account_new",
    [
        pytest.param(False, id="existing_target"),
        pytest.param(True, id="new_account"),
    ],
)
@pytest.mark.parametrize(
    "access",
    list(AccessScenario),
    ids=lambda a: a.value,
)
@pytest.mark.parametrize(
    "success",
    list(AccessSuccess),
    ids=lambda s: s.value,
)
def test_call(
    fork: Fork,
    pre: Alloc,
    blockchain_test: BlockchainTestFiller,
    value: int,
    account_new: bool,
    access: AccessScenario,
    success: AccessSuccess,
) -> None:
    """
    Test CALL opcode gas charging under EIP-2780.

    CALL transfers value from caller to target. With value > 0,
    the value cost is 2 * GAS_STATE_UPDATE for existing targets
    or GAS_STATE_UPDATE + GAS_NEW_ACCOUNT for new accounts.
    """
    if account_new and access == AccessScenario.COLD_CODE:
        pytest.skip("Empty target has no code")

    def caller_code_fn(target: Address, val: int) -> Bytecode:
        return Op.CALL(
            gas=0,
            address=target,
            value=val,
            args_offset=0,
            args_size=0,
            ret_offset=0,
            ret_size=0,
        )

    def post_fn(
        caller: Address,
        target: Address,
        value: int,
        is_oog: bool,
    ) -> dict[Address, Account]:
        if is_oog:
            if account_new:
                return {caller: Account(balance=value)}
            return {
                caller: Account(balance=value),
                target: Account(balance=0, code=Op.STOP),
            }
        if value > 0:
            target_account = (
                Account(balance=value)
                if account_new
                else Account(balance=value, code=Op.STOP)
            )
            return {
                caller: Account(balance=0),
                target: target_account,
            }
        if account_new:
            # No value sent: target stays non-existent
            return {caller: Account(balance=0)}
        return {
            caller: Account(balance=0),
            target: Account(balance=0, code=Op.STOP),
        }

    _run_call_test(
        fork=fork,
        pre=pre,
        blockchain_test=blockchain_test,
        access=access,
        success=success,
        caller_code_fn=caller_code_fn,
        n_args=7,
        value=value,
        has_value_transfer=True,
        account_new=account_new,
        post_fn=post_fn,
    )


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(0, id="zero_value"),
        pytest.param(1, id="non-zero_value"),
    ],
)
@pytest.mark.parametrize(
    "access",
    list(AccessScenario),
    ids=lambda a: a.value,
)
@pytest.mark.parametrize(
    "success",
    list(AccessSuccess),
    ids=lambda s: s.value,
)
def test_callcode(
    fork: Fork,
    pre: Alloc,
    blockchain_test: BlockchainTestFiller,
    value: int,
    access: AccessScenario,
    success: AccessSuccess,
) -> None:
    """
    Test CALLCODE opcode gas charging under EIP-2780.

    CALLCODE transfers value to self (caller), so there is no
    net balance change even on success with value > 0.
    """

    def caller_code_fn(target: Address, val: int) -> Bytecode:
        return Op.CALLCODE(
            gas=0,
            address=target,
            value=val,
            args_offset=0,
            args_size=0,
            ret_offset=0,
            ret_size=0,
        )

    def post_fn(
        caller: Address,
        target: Address,
        value: int,
        _is_oog: bool,
    ) -> dict[Address, Account]:
        # CALLCODE transfers value to self: no net change
        return {
            caller: Account(balance=value),
            target: Account(balance=0, code=Op.STOP),
        }

    _run_call_test(
        fork=fork,
        pre=pre,
        blockchain_test=blockchain_test,
        access=access,
        success=success,
        caller_code_fn=caller_code_fn,
        n_args=7,
        value=value,
        has_value_transfer=True,
        account_new=False,
        post_fn=post_fn,
    )


@pytest.mark.parametrize(
    "access",
    list(AccessScenario),
    ids=lambda a: a.value,
)
@pytest.mark.parametrize(
    "success",
    list(AccessSuccess),
    ids=lambda s: s.value,
)
def test_delegatecall(
    fork: Fork,
    pre: Alloc,
    blockchain_test: BlockchainTestFiller,
    access: AccessScenario,
    success: AccessSuccess,
) -> None:
    """
    Test DELEGATECALL opcode gas charging under EIP-2780.

    DELEGATECALL does not transfer value. Only access costs apply.
    """

    def caller_code_fn(target: Address, _val: int) -> Bytecode:
        return Op.DELEGATECALL(
            gas=0,
            address=target,
            args_offset=0,
            args_size=0,
            ret_offset=0,
            ret_size=0,
        )

    def post_fn(
        caller: Address,
        target: Address,
        _value: int,
        _is_oog: bool,
    ) -> dict[Address, Account]:
        return {
            caller: Account(balance=0),
            target: Account(balance=0, code=Op.STOP),
        }

    _run_call_test(
        fork=fork,
        pre=pre,
        blockchain_test=blockchain_test,
        access=access,
        success=success,
        caller_code_fn=caller_code_fn,
        n_args=6,
        value=0,
        has_value_transfer=False,
        account_new=False,
        post_fn=post_fn,
    )


@pytest.mark.parametrize(
    "access",
    list(AccessScenario),
    ids=lambda a: a.value,
)
@pytest.mark.parametrize(
    "success",
    list(AccessSuccess),
    ids=lambda s: s.value,
)
def test_staticcall(
    fork: Fork,
    pre: Alloc,
    blockchain_test: BlockchainTestFiller,
    access: AccessScenario,
    success: AccessSuccess,
) -> None:
    """
    Test STATICCALL opcode gas charging under EIP-2780.

    STATICCALL does not transfer value. Only access costs apply.
    """

    def caller_code_fn(target: Address, _val: int) -> Bytecode:
        return Op.STATICCALL(
            gas=0,
            address=target,
            args_offset=0,
            args_size=0,
            ret_offset=0,
            ret_size=0,
        )

    def post_fn(
        caller: Address,
        target: Address,
        _value: int,
        _is_oog: bool,
    ) -> dict[Address, Account]:
        return {
            caller: Account(balance=0),
            target: Account(balance=0, code=Op.STOP),
        }

    _run_call_test(
        fork=fork,
        pre=pre,
        blockchain_test=blockchain_test,
        access=access,
        success=success,
        caller_code_fn=caller_code_fn,
        n_args=6,
        value=0,
        has_value_transfer=False,
        account_new=False,
        post_fn=post_fn,
    )
