"""
Tests for the ethereum-spec-new-fork CLI tool.
"""

from pathlib import Path
from tempfile import TemporaryDirectory

import libcst as cst
import pytest
from libcst.codemod import CodemodContext

from ethereum_spec_tools.forks import Hardfork
from ethereum_spec_tools.new_fork.builder import validate_eip_selection
from ethereum_spec_tools.new_fork.cli import main as new_fork
from ethereum_spec_tools.new_fork.codemod.remove_docstring import (
    RemoveDocstringCommand,
)


@pytest.mark.parametrize(
    "template_fork",
    [
        Hardfork.discover()[-1].short_name,
        "osaka",
    ],
    ids=lambda tf: f"{tf}",
)
def test_end_to_end(template_fork: str) -> None:
    """
    Test that the ethereum-spec-new-fork CLI tool creates a fork from a
    template, correctly modifying names, blob parameters, and imports.
    """
    with TemporaryDirectory() as base_dir:
        output_dir = Path(base_dir) / "ethereum"
        fork_dir = output_dir / "e2e_fork"

        new_fork(
            [
                "--new-fork",
                "e2e_fork",
                "--template-fork",
                template_fork,
                "--target-blob-gas-per-block",
                "199",
                "--blob-base-fee-update-fraction",
                "750",
                "--min-blob-gasprice",
                "2",
                "--gas-per-blob",
                "1",
                "--at-timestamp",
                "7",
                "--max-blob-gas-per-block",
                "99",
                "--blob-schedule-target",
                "88",
                "--blob-schedule-max",
                "77",
                "--output",
                str(output_dir),
            ]
        )

        with (fork_dir / "__init__.py").open("r") as f:
            source = f.read()

            assert '"""' not in source[:20]
            assert "FORK_CRITERIA: ForkCriteria = ByTimestamp(7)" in source
            assert template_fork.capitalize() not in source

        with (fork_dir / "utils" / "hexadecimal.py").open("r") as f:
            source = f.read()
            assert "E2E Fork" in source

        with (fork_dir / "vm" / "gas.py").open("r") as f:
            source = f.read()

            expected = [
                "BLOB_TARGET_GAS_PER_BLOCK: Final[U64] = U64(199)",
                "PER_BLOB: Final[U64] = U64(1)",
                "BLOB_MIN_GASPRICE: Final[Uint] = Uint(2)",
                "BLOB_BASE_FEE_UPDATE_FRACTION: Final[Uint] = Uint(750)",
                "BLOB_SCHEDULE_TARGET: Final[U64] = U64(88)",
                "BLOB_SCHEDULE_MAX: Final[U64] = U64(77)",
            ]

            for needle in expected:
                assert needle in source

        # TODO: Assert on `vm/gas.py` alone once every fork usable as a
        # template defines the constant there (i.e. once the pre-Amsterdam
        # forks, which define it in `fork.py`, are gone).
        blob_gas_ceiling = "MAX_BLOB_GAS_PER_BLOCK: Final[U64] = U64(99)"
        assert any(
            blob_gas_ceiling in (fork_dir / relative_path).read_text()
            for relative_path in (Path("vm") / "gas.py", Path("fork.py"))
        )

        # TODO: Remove this condition once trie.py is removed from all
        # forks (i.e. fork-agnostic Trie is ported to pre-amsterdam forks).
        template_has_trie = (
            Path("src/ethereum/forks") / template_fork / "trie.py"
        ).exists()
        if template_has_trie:
            assert (fork_dir / "trie.py").exists()
        else:
            assert not (fork_dir / "trie.py").exists()


def has_module_docstring(file_path: Path) -> bool:
    """Return True if the file starts with a module-level doc-string."""
    tree = cst.parse_module(file_path.read_text())
    if not tree.body:
        return False
    first = tree.body[0]
    if not isinstance(first, cst.SimpleStatementLine):
        return False
    if len(first.body) != 1:
        return False
    expr = first.body[0]
    return isinstance(expr, cst.Expr) and isinstance(
        expr.value, cst.SimpleString
    )


def test_remove_docstring_command() -> None:
    """Test that RemoveDocstringCommand removes module docstrings."""
    source = '"""Module docstring."""\n\nsome_var = 123\n'
    module = cst.parse_module(source)
    context = CodemodContext()
    command = RemoveDocstringCommand(context)

    new_module = command.transform_module(module)
    result = new_module.code

    assert '"""Module docstring."""' not in result
    assert "some_var = 123" in result


def test_remove_docstring_preserves_other_docstrings() -> None:
    """Test that function/class docstrings are preserved."""
    source = '''"""Module docstring."""

def foo():
    """Function docstring."""
    pass
'''
    module = cst.parse_module(source)
    context = CodemodContext()
    command = RemoveDocstringCommand(context)

    new_module = command.transform_module(module)
    result = new_module.code

    assert not result.startswith('"""Module docstring."""')
    assert '"""Function docstring."""' in result


def test_remove_docstring_handles_files_without_docstrings() -> None:
    """Test that files without docstrings remain unchanged."""
    source_without_docstring = "some_var = 123\n\ndef foo():\n    pass\n"
    module = cst.parse_module(source_without_docstring)
    context = CodemodContext()
    command = RemoveDocstringCommand(context)

    new_module = command.transform_module(module)

    assert new_module.code == source_without_docstring


def test_remove_docstring_handles_empty_files() -> None:
    """Test that empty files remain empty."""
    source_empty = ""
    module = cst.parse_module(source_empty)
    context = CodemodContext()
    command = RemoveDocstringCommand(context)

    new_module = command.transform_module(module)

    assert new_module.code == source_empty


def test_validate_eip_selection() -> None:
    """
    An EIP selection must name known flags, not contradict itself, and
    enable every requirement of an enabled EIP.
    """
    flags = {8141, 8250}
    requires = {8250: {8141}}

    validate_eip_selection({8141, 8250}, set(), flags, requires)
    validate_eip_selection(set(), {8141, 8250}, flags, requires)
    validate_eip_selection({8141}, {8250}, flags, requires)

    with pytest.raises(ValueError, match="both enabled and disabled"):
        validate_eip_selection({8141}, {8141}, flags, requires)
    with pytest.raises(ValueError, match="no feature flag"):
        validate_eip_selection({9999}, set(), flags, requires)
    with pytest.raises(ValueError, match="requires"):
        validate_eip_selection({8250}, {8141}, flags, requires)
    with pytest.raises(ValueError, match="requires"):
        validate_eip_selection({8250}, set(), flags, requires)


@pytest.mark.parametrize("enabled", [True, False], ids=["on", "off"])
def test_fold_feature_guards_end_to_end(enabled: bool) -> None:
    """
    Folding the flagged fork removes its flags module and every guard,
    and keeps or deletes the EIP's own modules with the EIP.
    """
    template = "bogota"
    owned = [
        Path("frame_processing.py"),
        Path("transactions") / "frame_transaction.py",
    ]
    with TemporaryDirectory() as base_dir:
        output_dir = Path(base_dir) / "ethereum"
        fork_dir = output_dir / template
        new_fork(
            [
                "--template-fork",
                template,
                "--new-fork",
                template,
                "--output",
                str(output_dir),
                "--fold-only",
                "--enable-eip" if enabled else "--disable-eip",
                "8141",
            ]
        )

        assert not (fork_dir / "eips.py").exists()
        for relative in owned:
            assert (fork_dir / relative).exists() == enabled
        for path in fork_dir.rglob("*.py"):
            assert "EIP_8141" not in path.read_text(), path
        gas = (fork_dir / "vm" / "gas.py").read_text()
        assert ("def charge_frame_state_gas(" in gas) == enabled
        init = (fork_dir / "__init__.py").read_text()
        assert "FORK_CRITERIA" in init and has_module_docstring(
            fork_dir / "__init__.py"
        )


def test_fold_rejects_unknown_eip() -> None:
    """A selection naming an EIP without a flag is refused."""
    with TemporaryDirectory() as base_dir:
        with pytest.raises(ValueError, match="no feature flag"):
            new_fork(
                [
                    "--template-fork",
                    "bogota",
                    "--new-fork",
                    "bogota",
                    "--output",
                    str(Path(base_dir) / "ethereum"),
                    "--fold-only",
                    "--disable-eip",
                    "9999",
                ]
            )
