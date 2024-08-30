import time
from functools import partial
from typing import Dict, Generator, Tuple

import pytest

from tests.helpers import TEST_FIXTURES
from tests.helpers.load_evm_tools_tests import (
    fetch_evm_tools_tests,
    idfn,
    load_evm_tools_test,
)

ETHEREUM_TESTS_PATH = TEST_FIXTURES["ethereum_tests"]["fixture_path"]
TEST_DIR = f"{ETHEREUM_TESTS_PATH}/GeneralStateTests/"
FORK_NAME = "Prague"

run_evm_tools_test = partial(
    load_evm_tools_test,
    fork_name=FORK_NAME,
)

SLOW_TESTS = (
    "CALLBlake2f_MaxRounds",
    "CALLCODEBlake2f",
    "CALLBlake2f",
    "loopExp",
    "loopMul",
)

test_dirs = (
    "tests/fixtures/latest_fork_tests/state_tests/prague/eip2537_bls_12_381_precompiles",
)


def fetch_temporary_tests(test_dirs: Tuple[str, ...]) -> Generator:
    for test_dir in test_dirs:
        yield from fetch_evm_tools_tests(
            test_dir,
            FORK_NAME,
            SLOW_TESTS,
        )


@pytest.mark.evm_tools
@pytest.mark.parametrize(
    "test_case",
    fetch_temporary_tests(test_dirs),
    ids=idfn,
)
def test_evm_tools(test_case: Dict) -> None:
    test_file = test_case["test_file"]
    test_key = test_case["test_key"]
    print(f"\nTest file: {test_file} -- Test key: {test_key}")
    t0 = time.time()
    run_evm_tools_test(test_case)
    t1 = time.time()
    total = t1 - t0
    print(f"Total time: {total}")
