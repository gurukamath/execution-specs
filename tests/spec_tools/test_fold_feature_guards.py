"""
Unit tests for the feature-guard folding codemod.
"""

from typing import Any

import pytest
from libcst.codemod import CodemodContext, CodemodTest

from ethereum_spec_tools.new_fork.codemod.feature_guards import (
    FoldFeatureGuardsCommand,
)


class TestFoldFeatureGuardsCommand(CodemodTest):
    """Tests for `FoldFeatureGuardsCommand`."""

    TRANSFORM = FoldFeatureGuardsCommand

    def fold(self, before: str, after: str, **kwargs: Any) -> None:
        """Run the codemod as an ordinary module."""
        self.assertCodemod(before, after, **kwargs)

    def test_enabled_guard_in_function_body(self) -> None:
        """An enabled guard is replaced by its `if` branch, dedented."""
        before = """
            from ..eips import EIP_8141

            def f(x):
                y = 1
                if EIP_8141:
                    # frame case
                    y = 2
                else:
                    y = 3
                return y
        """
        after = """
            def f(x):
                y = 1
                # frame case
                y = 2
                return y
        """
        self.fold(before, after, enable=["EIP_8141"])

    def test_disabled_guard_in_function_body(self) -> None:
        """A disabled guard is replaced by its `else` branch."""
        before = """
            from ..eips import EIP_8141

            def f(x):
                if EIP_8141:
                    y = 2
                else:
                    y = 3
                return y
        """
        after = """
            def f(x):
                y = 3
                return y
        """
        self.fold(before, after, disable=["EIP_8141"])

    def test_disabled_guard_without_else_vanishes(self) -> None:
        """A disabled guard with no `else` disappears."""
        before = """
            from ..eips import EIP_8141

            def f(x):
                if EIP_8141:
                    assert x
                return x
        """
        after = """
            def f(x):
                return x
        """
        self.fold(before, after, disable=["EIP_8141"])

    def test_undecided_guard_is_left_alone(self) -> None:
        """A flag that is neither enabled nor disabled keeps its guard."""
        before = """
            from ..eips import EIP_8141, EIP_8250

            if EIP_8141:
                a = 1
            if EIP_8250:
                b = 2
        """
        after = """
            from ..eips import EIP_8250

            a = 1
            if EIP_8250:
                b = 2
        """
        self.fold(before, after, enable=["EIP_8141"])

    def test_negated_guard(self) -> None:
        """`if not FLAG:` selects the opposite branch."""
        before = """
            from ..eips import EIP_8141

            class Env:
                origin: int
                if not EIP_8141:
                    recipient: int
                gas: int
        """
        after = """
            class Env:
                origin: int
                gas: int
        """
        self.fold(before, after, enable=["EIP_8141"])

    def test_conjunction_folds_when_all_decided(self) -> None:
        """A conjunction folds to `else` on any disabled member."""
        before = """
            from ..eips import EIP_8141, EIP_8250

            if EIP_8141 and EIP_8250:
                a = 1
            else:
                a = 2
        """
        after = """
            a = 2
        """
        self.fold(before, after, enable=["EIP_8141"], disable=["EIP_8250"])

    def test_conjunction_simplifies_when_partly_decided(self) -> None:
        """Enabled members drop out of a conjunction with undecided ones."""
        before = """
            from ..eips import EIP_8141, EIP_8250

            if EIP_8141 and EIP_8250:
                a = 1
        """
        after = """
            from ..eips import EIP_8250

            if EIP_8250:
                a = 1
        """
        self.fold(before, after, enable=["EIP_8141"])

    def test_nested_guards_fold_inner_first(self) -> None:
        """A guard inside a guard folds without confusing the two `else`s."""
        before = """
            from ..eips import EIP_8141

            def f(r):
                if EIP_8141:
                    if r.is_frame:
                        logs = r.frame_logs
                    else:
                        logs = r.logs
                else:
                    logs = r.logs
                return logs
        """
        after_on = """
            def f(r):
                if r.is_frame:
                    logs = r.frame_logs
                else:
                    logs = r.logs
                return logs
        """
        after_off = """
            def f(r):
                logs = r.logs
                return logs
        """
        self.fold(before, after_on, enable=["EIP_8141"])
        self.fold(before, after_off, disable=["EIP_8141"])

    def test_enum_body_guard(self) -> None:
        """Members under a guard in an enum body fold into the body."""
        before = """
            import enum

            from ..eips import EIP_8141

            class Ops(enum.Enum):
                STOP = 0x00
                if EIP_8141:
                    # Frame Transaction Operations
                    APPROVE = 0xAA
                RETURN = 0xF3
        """
        after = """
            import enum

            class Ops(enum.Enum):
                STOP = 0x00
                # Frame Transaction Operations
                APPROVE = 0xAA
                RETURN = 0xF3
        """
        self.fold(before, after, enable=["EIP_8141"])

    def test_empty_class_body_gets_pass(self) -> None:
        """A body left empty by the fold gets a `pass`."""
        before = """
            from ..eips import EIP_8141

            class Only:
                if EIP_8141:
                    x: int
        """
        after = """
            class Only:
                pass
        """
        self.fold(before, after, disable=["EIP_8141"])

    def test_module_level_guard_with_comment_on_if(self) -> None:
        """Comments above the `if` stay above the surviving statements."""
        before = """
            from ..eips import EIP_8141

            # Entry point of frames.
            if EIP_8141:
                FRAME_ENTRY_POINT = 0xAA
        """
        after = """
            # Entry point of frames.
            FRAME_ENTRY_POINT = 0xAA
        """
        self.fold(before, after, enable=["EIP_8141"])

    def test_table_update_merges_into_literal(self) -> None:
        """A guarded `update` folds back into the table it extends."""
        before = """
            from ..eips import EIP_8141

            table = {
                Ops.STOP: stop,
                Ops.ADD: add,
            }

            if EIP_8141:
                table.update(
                    {
                        Ops.APPROVE: approve,
                    }
                )
        """
        after = """
            table = {
                Ops.STOP: stop,
                Ops.ADD: add,
                Ops.APPROVE: approve,
            }
        """
        self.fold(before, after, enable=["EIP_8141"])

    def test_union_pruning_in_annotation_and_alias(self) -> None:
        """Names only a disabled branch defined leave unions."""
        before = """
            from typing import Union

            from ..eips import EIP_8141

            if EIP_8141:
                class FrameReceipt:
                    pass

            Receipts = (
                Receipt
                | FrameReceipt
            )

            def encode(r: Receipt | FrameReceipt) -> Union[int, FrameReceipt]:
                return b""
        """
        after = """
            Receipts = (
                Receipt
            )

            def encode(r: Receipt) -> int:
                return b""
        """
        self.fold(before, after, disable=["EIP_8141"])

    def test_union_kept_when_enabled(self) -> None:
        """With the flag on, the union is untouched."""
        before = """
            from ..eips import EIP_8141

            if EIP_8141:
                class FrameReceipt:
                    pass

            def encode(r: Receipt | FrameReceipt) -> bytes:
                return b""
        """
        after = """
            class FrameReceipt:
                pass

            def encode(r: Receipt | FrameReceipt) -> bytes:
                return b""
        """
        self.fold(before, after, enable=["EIP_8141"])

    def test_unused_import_removed_after_fold(self) -> None:
        """An import only the folded-away branch used is removed."""
        before = """
            from dataclasses import dataclass, replace

            from ..eips import EIP_8141

            @dataclass
            class A:
                x: int

            if EIP_8141:
                def copy(a: A) -> A:
                    return replace(a)
        """
        after = """
            from dataclasses import dataclass

            @dataclass
            class A:
                x: int
        """
        self.fold(before, after, disable=["EIP_8141"])

    def test_flags_module_strips_decided_flags(self) -> None:
        """Decided flags, their docstrings and table entries are removed."""
        before = '''
            from typing import Final, FrozenSet, Mapping, Tuple

            EIP_8141: Final[bool] = True
            """
            Frames.
            """

            EIP_8250: Final[bool] = True
            """
            Keyed nonces.
            """

            REQUIRES: Final[Mapping[int, FrozenSet[int]]] = {
                8250: frozenset({8141}),
            }

            OWNED_MODULES: Final[Mapping[int, Tuple[str, ...]]] = {
                8141: ("frame_processing",),
                8250: ("keyed_nonces",),
            }
        '''
        after = '''
            from typing import Final, FrozenSet, Mapping, Tuple

            EIP_8250: Final[bool] = True
            """
            Keyed nonces.
            """

            REQUIRES: Final[Mapping[int, FrozenSet[int]]] = {}

            OWNED_MODULES: Final[Mapping[int, Tuple[str, ...]]] = {
                8250: ("keyed_nonces",),
            }
        '''
        self.assertCodemod(
            before,
            after,
            enable=["EIP_8141"],
            context_override=CodemodContext(filename="pkg/eips.py"),
        )

    def test_surfaced_imports_join_the_import_block(self) -> None:
        """Imports a module-level fold surfaces move up with the others."""
        before = """
            from .a import A
            from .eips import EIP_8141

            X = 1

            if EIP_8141:
                from .b import B


            def f():
                return A, B, X
        """
        after = """
            from .a import A
            from .b import B

            X = 1


            def f():
                return A, B, X
        """
        self.fold(before, after, enable=["EIP_8141"])

    def test_re_export_is_never_removed(self) -> None:
        """`from m import x as x` is a re-export and survives the fold."""
        before = """
            from .gas import LIMIT as LIMIT

            from ..eips import EIP_8141

            if EIP_8141:
                y = 2
        """
        after = """
            from .gas import LIMIT as LIMIT

            y = 2
        """
        self.fold(before, after, enable=["EIP_8141"])

    def test_docstrings_in_folded_class_are_dedented(self) -> None:
        """Attribute docstrings lose the indentation of the removed guard."""
        before = '''
            from ..eips import EIP_8141

            if EIP_8141:

                class FrameReceipt:
                    """
                    Result of a frame.
                    """

                    status: int
                    """
                    Outcome of the frame's execution.
                    """
        '''
        after = '''
            class FrameReceipt:
                """
                Result of a frame.
                """

                status: int
                """
                Outcome of the frame's execution.
                """
        '''
        self.fold(before, after, enable=["EIP_8141"])

    def test_surviving_flag_name_is_an_error(self) -> None:
        """A decided flag used outside an `if` test fails the fold."""
        before = """
            from ..eips import EIP_8141

            x = 1 if EIP_8141 else 2
        """
        with pytest.raises(Exception, match="survived the fold"):
            self.assertCodemod(before, before, enable=["EIP_8141"])

    def test_non_flag_name_is_rejected(self) -> None:
        """Only `EIP_<n>` names are accepted as flags."""
        with pytest.raises(ValueError, match="not an EIP flag"):
            self.assertCodemod("x = 1\n", "x = 1\n", enable=["DEBUG"])
