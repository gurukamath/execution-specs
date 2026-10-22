"""
libcst codemod that folds EIP feature guards for a decided set of flags.

A fork under development keeps every EIP it carries behind a flag, read
only as the whole test of an `if` statement. Given which flags are
enabled and which are disabled, this codemod replaces each decided guard
by the branch it selects, prunes names that only a disabled branch
defined from unions in annotations and aliases, merges a guarded
`update` of a dict table back into the table's literal, removes the
imports the fold leaves unused, and finally refuses any module where a
decided flag name survives. Flags that are neither enabled nor disabled
are left in place, so a fold can decide one fork's EIPs and leave
another's untouched.

Docstrings are prose and are not folded, but their `ref:` links are
kept resolvable: a link prefix can be rewritten from the source
package's name to the output's, and links to names the fold removed
are dropped, their inline references unwrapped to plain text.
"""

import argparse
import re
from typing import ClassVar, Collection, Sequence

import libcst as cst
from libcst.codemod import CodemodContext, VisitorBasedCodemodCommand
from libcst.codemod.visitors import RemoveImportsVisitor
from libcst.metadata.base_provider import ProviderT
from typing_extensions import override

FLAG_PATTERN = re.compile(r"^EIP_(\d+)$")


def _flag_number(name: str) -> int:
    match = FLAG_PATTERN.match(name)
    if match is None:
        raise ValueError(f"`{name}` is not an EIP flag name")
    return int(match.group(1))


def _conjuncts(expression: cst.BaseExpression) -> list[cst.BaseExpression]:
    """
    Flatten a chain of `and` into its members.
    """
    if isinstance(expression, cst.BooleanOperation) and isinstance(
        expression.operator, cst.And
    ):
        return _conjuncts(expression.left) + _conjuncts(expression.right)
    return [expression]


def _suite_statements(
    suite: cst.BaseSuite,
) -> list[cst.BaseStatement]:
    """
    Return the statements of a block, turning a one-line suite into a line.
    """
    if isinstance(suite, cst.IndentedBlock):
        return list(suite.body)
    assert isinstance(suite, cst.SimpleStatementSuite)
    return [cst.SimpleStatementLine(body=list(suite.body))]


def _is_re_export(alias: cst.ImportAlias) -> bool:
    """
    Whether an import alias is the `x as x` form that marks a re-export.
    """
    asname = alias.asname
    return (
        asname is not None
        and isinstance(asname.name, cst.Name)
        and isinstance(alias.name, cst.Name)
        and asname.name.value == alias.name.value
    )


def _block_indent(suite: cst.BaseSuite) -> str:
    """
    The indentation a block adds to its statements.
    """
    if isinstance(suite, cst.IndentedBlock) and suite.indent is not None:
        return suite.indent
    return " " * 4


class _DedentDocstrings(cst.CSTTransformer):
    """
    Strip one level of indentation from the inner lines of multi-line
    strings in docstring position.

    A folded block's statements are re-indented by libcst, but the text
    inside a string literal is token content and keeps the indentation
    it was written with. Docstrings are the only strings the
    specification writes across lines, so those are brought back in
    line with their new block.
    """

    def __init__(self, indent: str) -> None:
        self._indent = indent
        self._in_docstring_line = False

    @override
    def visit_SimpleStatementLine(  # noqa: D102
        self, node: cst.SimpleStatementLine
    ) -> bool:
        self._in_docstring_line = _is_docstring_line(node)
        return True

    @override
    def leave_SimpleStatementLine(  # noqa: D102
        self,
        original_node: cst.SimpleStatementLine,
        updated_node: cst.SimpleStatementLine,
    ) -> cst.SimpleStatementLine:
        self._in_docstring_line = False
        return updated_node

    @override
    def leave_SimpleString(  # noqa: D102
        self, original_node: cst.SimpleString, updated_node: cst.SimpleString
    ) -> cst.SimpleString:
        if not self._in_docstring_line or "\n" not in updated_node.value:
            return updated_node
        first, *rest = updated_node.value.split("\n")
        rest = [
            line[len(self._indent) :]
            if line.startswith(self._indent)
            else line
            for line in rest
        ]
        return updated_node.with_changes(value="\n".join([first, *rest]))


_LINK_DEFINITION = re.compile(
    r"^[ \t]*\[(?P<label>[^\]]+)\]:[ \t]*ref:(?P<target>\S+)[ \t]*\n?",
    re.MULTILINE,
)


def rewrite_docstring_links(
    text: str,
    prefix: tuple[str, str] | None,
    removed: Collection[str],
) -> str:
    """
    Rewrite the `ref:` links of a docstring.

    With `prefix`, links under the first qualified name are moved under
    the second. A link definition whose target is one of `removed`, or
    lies under one of them, is deleted, and every inline reference to
    its label becomes the reference's own text. Blank lines the
    deletions leave before the closing quotes are removed.
    """
    if prefix is not None:
        old, new = prefix
        text = re.sub(
            r"ref:" + re.escape(old) + r"(?=[.\s]|$)", f"ref:{new}", text
        )

    dropped: list[str] = []

    def is_removed(target: str) -> bool:
        return any(
            target == name or target.startswith(name + ".") for name in removed
        )

    def definition(match: re.Match[str]) -> str:
        if is_removed(match.group("target")):
            dropped.append(match.group("label"))
            return ""
        return match.group(0)

    if removed:
        text = _LINK_DEFINITION.sub(definition, text)
    for label in dropped:
        text = re.sub(
            r"\[([^\[\]]+)\]\[" + re.escape(label) + r"\]", r"\1", text
        )
        text = re.sub(r"\[" + re.escape(label) + r"\](?![\[(:])", label, text)
    if dropped:
        text = re.sub(r"\n[ \t]*\n([ \t]*)(\"\"\"|''')$", r"\n\1\2", text)
        text = re.sub(r"\n[ \t]*\n[ \t]*\n", "\n\n", text)
    return text


class _RewriteDocstringLinks(cst.CSTTransformer):
    """
    Apply `rewrite_docstring_links` to every string in docstring position.
    """

    def __init__(
        self, prefix: tuple[str, str] | None, removed: Collection[str]
    ) -> None:
        self._prefix = prefix
        self._removed = removed
        self._in_docstring_line = False

    @override
    def visit_SimpleStatementLine(  # noqa: D102
        self, node: cst.SimpleStatementLine
    ) -> bool:
        self._in_docstring_line = _is_docstring_line(node)
        return True

    @override
    def leave_SimpleStatementLine(  # noqa: D102
        self,
        original_node: cst.SimpleStatementLine,
        updated_node: cst.SimpleStatementLine,
    ) -> cst.SimpleStatementLine:
        self._in_docstring_line = False
        return updated_node

    @override
    def leave_SimpleString(  # noqa: D102
        self, original_node: cst.SimpleString, updated_node: cst.SimpleString
    ) -> cst.SimpleString:
        if not self._in_docstring_line or "ref:" not in updated_node.value:
            return updated_node
        return updated_node.with_changes(
            value=rewrite_docstring_links(
                updated_node.value, self._prefix, self._removed
            )
        )


def _is_import_line(statement: cst.BaseStatement) -> bool:
    return isinstance(statement, cst.SimpleStatementLine) and all(
        isinstance(small, (cst.Import, cst.ImportFrom))
        for small in statement.body
    )


def _hoist_surfaced_imports(
    body: list[cst.BaseStatement],
) -> list[cst.BaseStatement]:
    """
    Move module-level imports that a fold surfaced below other code up
    to the end of the module's leading import block, so that the
    import sorter sees one block.
    """
    start = 1 if body and _is_docstring_line(body[0]) else 0
    end = start
    while end < len(body) and _is_import_line(body[end]):
        end += 1
    leading, rest = body[:end], body[end:]
    surfaced = [s for s in rest if _is_import_line(s)]
    if not surfaced:
        return body
    remaining = [s for s in rest if not _is_import_line(s)]
    surfaced = [s.with_changes(leading_lines=[]) for s in surfaced]
    if remaining:
        first = remaining[0]
        assert isinstance(
            first, (cst.SimpleStatementLine, cst.BaseCompoundStatement)
        )
        if not first.leading_lines:
            remaining[0] = first.with_changes(
                leading_lines=[cst.EmptyLine(), cst.EmptyLine()]
            )
    return leading + surfaced + remaining


def _is_docstring_line(statement: cst.BaseStatement) -> bool:
    return (
        isinstance(statement, cst.SimpleStatementLine)
        and len(statement.body) == 1
        and isinstance(statement.body[0], cst.Expr)
        and isinstance(
            statement.body[0].value, (cst.SimpleString, cst.ConcatenatedString)
        )
    )


def _bound_names(statements: Sequence[cst.BaseStatement]) -> set[str]:
    """
    Names a block binds at its own level: assignments, definitions and
    imports. Bindings nested inside those definitions are not collected.
    """
    names: set[str] = set()
    for statement in statements:
        if isinstance(statement, (cst.ClassDef, cst.FunctionDef)):
            names.add(statement.name.value)
        elif isinstance(statement, cst.SimpleStatementLine):
            for small in statement.body:
                if isinstance(small, cst.Assign):
                    for target in small.targets:
                        if isinstance(target.target, cst.Name):
                            names.add(target.target.value)
                elif isinstance(small, cst.AnnAssign):
                    if isinstance(small.target, cst.Name):
                        names.add(small.target.value)
                elif isinstance(small, cst.ImportFrom):
                    if isinstance(small.names, cst.ImportStar):
                        continue
                    for alias in small.names:
                        bound = (
                            alias.asname.name if alias.asname else alias.name
                        )
                        if isinstance(bound, cst.Name):
                            names.add(bound.value)
    return names


class _ConditionalNameCollector(cst.CSTVisitor):
    """
    Collect the names bound only inside branches that the fold discards.
    """

    def __init__(self, evaluate) -> None:  # type: ignore[no-untyped-def]
        self._evaluate = evaluate
        self.names: set[str] = set()

    @override
    def visit_If(self, node: cst.If) -> bool:  # noqa: D102
        value, _ = self._evaluate(node.test)
        if value is True:
            if isinstance(node.orelse, cst.Else):
                self.names |= _bound_names(_suite_statements(node.orelse.body))
        elif value is False:
            self.names |= _bound_names(_suite_statements(node.body))
        return True


class _RemovedQualifiedNames(cst.CSTVisitor):
    """
    Collect, qualified by module and class, the names bound only inside
    branches that the fold discards. Function bodies are not entered:
    nothing links to a local.
    """

    def __init__(self, qualname: str, evaluate) -> None:  # type: ignore[no-untyped-def]
        self._scope = [qualname]
        self._evaluate = evaluate
        self.names: set[str] = set()

    @override
    def visit_ClassDef(self, node: cst.ClassDef) -> bool:  # noqa: D102
        self._scope.append(node.name.value)
        return True

    @override
    def leave_ClassDef(self, original_node: cst.ClassDef) -> None:  # noqa: D102
        self._scope.pop()

    @override
    def visit_FunctionDef(self, node: cst.FunctionDef) -> bool:  # noqa: D102
        return False

    @override
    def visit_If(self, node: cst.If) -> bool:  # noqa: D102
        value, _ = self._evaluate(node.test)
        removed: list[cst.BaseStatement] = []
        if value is True and isinstance(node.orelse, cst.Else):
            removed = _suite_statements(node.orelse.body)
        elif value is False:
            removed = _suite_statements(node.body)
        prefix = ".".join(self._scope)
        self.names |= {f"{prefix}.{name}" for name in _bound_names(removed)}
        return True


def removed_qualified_names(
    module: cst.Module,
    qualname: str,
    enable: Collection[str],
    disable: Collection[str],
) -> set[str]:
    """
    Qualified names a fold of `module` with the given decisions removes.
    """
    command = FoldFeatureGuardsCommand(
        CodemodContext(), enable=list(enable), disable=list(disable)
    )
    collector = _RemovedQualifiedNames(qualname, command._evaluate)
    module.visit(collector)
    return collector.names


class _SurvivingFlagFinder(cst.CSTVisitor):
    """
    Find decided flag names that survived the fold.
    """

    def __init__(self, decided: Collection[str]) -> None:
        self._decided = set(decided)
        self.found: list[str] = []

    @override
    def visit_Name(self, node: cst.Name) -> None:  # noqa: D102
        if node.value in self._decided:
            self.found.append(node.value)


class FoldFeatureGuardsCommand(VisitorBasedCodemodCommand):
    """
    Replace each decided feature guard by the branch it selects.
    """

    DESCRIPTION: str = "Fold EIP feature guards for a decided set of flags."
    METADATA_DEPENDENCIES: ClassVar[Collection[ProviderT]] = ()

    decided: dict[str, bool]
    flags_module: str

    _conditional_names: set[str]
    _annotation_depth: int
    _function_depth: int
    _folded: bool
    _in_flags_module: bool

    @staticmethod
    def add_args(arg_parser: argparse.ArgumentParser) -> None:
        """
        Add command-line args that a user can specify for running this codemod.
        """
        arg_parser.add_argument(
            "--enable",
            dest="enable",
            metavar="FLAG",
            action="append",
            help="Flag whose guards fold to their `if` branch.",
        )
        arg_parser.add_argument(
            "--disable",
            dest="disable",
            metavar="FLAG",
            action="append",
            help="Flag whose guards fold to their `else` branch.",
        )
        arg_parser.add_argument(
            "--flags-module",
            dest="flags_module",
            metavar="NAME",
            default="eips",
            help="Module name (without `.py`) that defines the flags.",
        )
        arg_parser.add_argument(
            "--ref-prefix",
            dest="ref_prefix",
            metavar=("OLD", "NEW"),
            nargs=2,
            help="Move docstring `ref:` links under OLD to NEW.",
        )
        arg_parser.add_argument(
            "--removed-name",
            dest="removed_names",
            metavar="NAME",
            action="append",
            help="Qualified name the fold removes; links to it are dropped.",
        )

    def __init__(
        self,
        context: CodemodContext,
        enable: list[str] | None = None,
        disable: list[str] | None = None,
        flags_module: str = "eips",
        ref_prefix: list[str] | None = None,
        removed_names: list[str] | None = None,
    ) -> None:
        super().__init__(context)
        self.ref_prefix = (
            (ref_prefix[0], ref_prefix[1]) if ref_prefix else None
        )
        self.removed_names = list(removed_names or [])
        self.decided = {}
        for name in enable or []:
            self.decided[_flag_name_check(name)] = True
        for name in disable or []:
            if name in self.decided:
                raise ValueError(f"`{name}` both enabled and disabled")
            self.decided[_flag_name_check(name)] = False
        self.flags_module = flags_module
        self._conditional_names = set()
        self._annotation_depth = 0
        self._function_depth = 0
        self._folded = False
        self._in_flags_module = False

    # Guard evaluation

    def _evaluate(
        self, test: cst.BaseExpression
    ) -> tuple[bool | None, cst.BaseExpression | None]:
        """
        Decide a guard's test.

        Return `(True, None)` or `(False, None)` when the test is decided,
        `(None, None)` when it is not a flag test at all, and
        `(None, simplified)` when a conjunction is only partly decided.
        """
        members = _conjuncts(test)
        remaining: list[cst.BaseExpression] = []
        for member in members:
            value = self._evaluate_member(member)
            if value is None:
                if not self._is_flag_member(member):
                    return None, None
                remaining.append(member)
            elif value is False:
                return False, None
        if not remaining:
            return True, None
        if len(remaining) == len(members):
            return None, None
        simplified = remaining[0]
        for member in remaining[1:]:
            simplified = cst.BooleanOperation(
                left=simplified, operator=cst.And(), right=member
            )
        return None, simplified

    def _is_flag_member(self, member: cst.BaseExpression) -> bool:
        if isinstance(member, cst.UnaryOperation) and isinstance(
            member.operator, cst.Not
        ):
            member = member.expression
        return isinstance(member, cst.Name) and bool(
            FLAG_PATTERN.match(member.value)
        )

    def _evaluate_member(self, member: cst.BaseExpression) -> bool | None:
        negate = False
        if isinstance(member, cst.UnaryOperation) and isinstance(
            member.operator, cst.Not
        ):
            member = member.expression
            negate = True
        if not isinstance(member, cst.Name):
            return None
        value = self.decided.get(member.value)
        if value is None:
            return None
        return (not value) if negate else value

    # Module entry and exit

    @override
    def visit_Module(self, node: cst.Module) -> bool:  # noqa: D102
        filename = self.context.filename or ""
        self._in_flags_module = filename.endswith(f"{self.flags_module}.py")
        collector = _ConditionalNameCollector(self._evaluate)
        node.visit(collector)
        self._conditional_names = collector.names
        return True

    @override
    def leave_Module(  # noqa: D102
        self, original_node: cst.Module, updated_node: cst.Module
    ) -> cst.Module:
        body = self._merge_table_updates(list(updated_node.body))
        if self._folded:
            body = _hoist_surfaced_imports(body)
        if self._in_flags_module:
            body = self._strip_decided_flags(body)
        updated_node = updated_node.with_changes(body=body)

        if self.ref_prefix is not None or self.removed_names:
            rewritten = updated_node.visit(
                _RewriteDocstringLinks(self.ref_prefix, self.removed_names)
            )
            assert isinstance(rewritten, cst.Module)
            updated_node = rewritten

        if not self._in_flags_module:
            finder = _SurvivingFlagFinder(self.decided)
            updated_node.visit(finder)
            if finder.found:
                raise Exception(
                    "decided flag names survived the fold: "
                    + ", ".join(sorted(set(finder.found)))
                )
        return updated_node

    # Guards

    @override
    def leave_If(  # noqa: D102
        self, original_node: cst.If, updated_node: cst.If
    ) -> (
        cst.BaseStatement
        | cst.FlattenSentinel[cst.BaseStatement]
        | cst.RemovalSentinel
    ):
        value, simplified = self._evaluate(updated_node.test)
        if value is None:
            if simplified is None:
                return updated_node
            return updated_node.with_changes(test=simplified)

        self._folded = True
        if value:
            statements = _suite_statements(updated_node.body)
        elif isinstance(updated_node.orelse, cst.Else):
            statements = _suite_statements(updated_node.orelse.body)
        elif isinstance(updated_node.orelse, cst.If):
            statements = [updated_node.orelse]
        else:
            statements = []

        if not statements:
            return cst.RemoveFromParent()

        indent = _block_indent(updated_node.body)
        dedented: list[cst.BaseStatement] = []
        for statement in statements:
            moved = statement.visit(_DedentDocstrings(indent))
            assert isinstance(moved, cst.BaseStatement)
            dedented.append(moved)
        statements = dedented

        first = statements[0]
        assert isinstance(
            first, (cst.SimpleStatementLine, cst.BaseCompoundStatement)
        )
        statements[0] = first.with_changes(
            leading_lines=[*updated_node.leading_lines, *first.leading_lines]
        )
        return cst.FlattenSentinel(statements)

    @override
    def leave_IndentedBlock(  # noqa: D102
        self, original_node: cst.IndentedBlock, updated_node: cst.IndentedBlock
    ) -> cst.IndentedBlock:
        body = self._merge_table_updates(list(updated_node.body))
        if not body:
            body = [cst.SimpleStatementLine(body=[cst.Pass()])]
        return updated_node.with_changes(body=body)

    # Imports

    @override
    def visit_ImportFrom(self, node: cst.ImportFrom) -> bool:  # noqa: D102
        if self._is_flags_import(node) or isinstance(
            node.names, cst.ImportStar
        ):
            return True
        if node.relative and self.context.full_module_name is None:
            # A relative import cannot be resolved without knowing the
            # module's own name, which the codemod tool always supplies.
            return True
        # `from m import x as x` is an explicit re-export, not a use
        # site, so it is never removed as unused.
        removable = [alias for alias in node.names if not _is_re_export(alias)]
        if removable:
            RemoveImportsVisitor.remove_unused_import_by_node(
                self.context, node.with_changes(names=removable)
            )
        return True

    @override
    def visit_Import(self, node: cst.Import) -> bool:  # noqa: D102
        removable = [alias for alias in node.names if not _is_re_export(alias)]
        if removable:
            RemoveImportsVisitor.remove_unused_import_by_node(
                self.context, node.with_changes(names=removable)
            )
        return True

    def _is_flags_import(self, node: cst.ImportFrom) -> bool:
        module = node.module
        if module is None:
            return False
        if isinstance(module, cst.Name):
            return module.value == self.flags_module
        return (
            isinstance(module, cst.Attribute)
            and module.attr.value == self.flags_module
        )

    @override
    def leave_ImportFrom(  # noqa: D102
        self, original_node: cst.ImportFrom, updated_node: cst.ImportFrom
    ) -> cst.ImportFrom | cst.RemovalSentinel:
        if not self._is_flags_import(updated_node) or isinstance(
            updated_node.names, cst.ImportStar
        ):
            return updated_node
        kept = [
            alias
            for alias in updated_node.names
            if alias.name.value not in self.decided
        ]
        if not kept:
            return cst.RemoveFromParent()
        if len(kept) == len(updated_node.names):
            return updated_node
        kept[-1] = kept[-1].with_changes(comma=cst.MaybeSentinel.DEFAULT)
        return updated_node.with_changes(names=kept)

    # Union pruning (rule 2)

    @override
    def visit_Annotation(self, node: cst.Annotation) -> bool:  # noqa: D102
        self._annotation_depth += 1
        return True

    @override
    def leave_Annotation(  # noqa: D102
        self, original_node: cst.Annotation, updated_node: cst.Annotation
    ) -> cst.Annotation:
        self._annotation_depth -= 1
        return updated_node

    @override
    def visit_FunctionDef(self, node: cst.FunctionDef) -> bool:  # noqa: D102
        self._function_depth += 1
        return True

    @override
    def leave_FunctionDef(  # noqa: D102
        self, original_node: cst.FunctionDef, updated_node: cst.FunctionDef
    ) -> cst.FunctionDef:
        self._function_depth -= 1
        return updated_node

    def _prunes_here(self) -> bool:
        return bool(self._conditional_names) and (
            self._annotation_depth > 0 or self._function_depth == 0
        )

    def _is_conditional(self, expression: cst.BaseExpression) -> bool:
        return (
            isinstance(expression, cst.Name)
            and expression.value in self._conditional_names
        )

    @override
    def leave_BinaryOperation(  # noqa: D102
        self,
        original_node: cst.BinaryOperation,
        updated_node: cst.BinaryOperation,
    ) -> cst.BaseExpression:
        if not self._prunes_here() or not isinstance(
            updated_node.operator, cst.BitOr
        ):
            return updated_node
        if self._is_conditional(updated_node.right):
            survivor = updated_node.left
        elif self._is_conditional(updated_node.left):
            survivor = updated_node.right
        else:
            return updated_node
        # The surviving operand inherits the parentheses of the union it
        # was part of, so a multi-line alias stays valid.
        return survivor.with_changes(
            lpar=[*updated_node.lpar, *survivor.lpar],
            rpar=[*survivor.rpar, *updated_node.rpar],
        )

    @override
    def leave_Subscript(  # noqa: D102
        self, original_node: cst.Subscript, updated_node: cst.Subscript
    ) -> cst.BaseExpression:
        if not self._prunes_here():
            return updated_node
        value = updated_node.value
        if not (isinstance(value, cst.Name) and value.value == "Union"):
            return updated_node
        kept = [
            element
            for element in updated_node.slice
            if not (
                isinstance(element.slice, cst.Index)
                and self._is_conditional(element.slice.value)
            )
        ]
        if len(kept) == len(updated_node.slice):
            return updated_node
        if len(kept) == 1 and isinstance(kept[0].slice, cst.Index):
            return kept[0].slice.value
        kept[-1] = kept[-1].with_changes(comma=cst.MaybeSentinel.DEFAULT)
        return updated_node.with_changes(slice=kept)

    # Dict table updates

    @staticmethod
    def _table_literal(
        statement: cst.BaseStatement,
    ) -> tuple[str, cst.Dict] | None:
        """Return `(name, literal)` for `name = {...}` or `name: T = {...}`."""
        if not (
            isinstance(statement, cst.SimpleStatementLine)
            and len(statement.body) == 1
        ):
            return None
        small = statement.body[0]
        if isinstance(small, cst.Assign) and len(small.targets) == 1:
            target: cst.BaseExpression = small.targets[0].target
            value = small.value
        elif isinstance(small, cst.AnnAssign) and small.value is not None:
            target = small.target
            value = small.value
        else:
            return None
        if isinstance(target, cst.Name) and isinstance(value, cst.Dict):
            return target.value, value
        return None

    @staticmethod
    def _table_update(
        statement: cst.BaseStatement,
    ) -> tuple[str, cst.Dict] | None:
        """Return `(name, literal)` for a bare `name.update({...})` call."""
        if not (
            isinstance(statement, cst.SimpleStatementLine)
            and len(statement.body) == 1
            and isinstance(statement.body[0], cst.Expr)
        ):
            return None
        call = statement.body[0].value
        if not (
            isinstance(call, cst.Call)
            and isinstance(call.func, cst.Attribute)
            and isinstance(call.func.value, cst.Name)
            and call.func.attr.value == "update"
            and len(call.args) == 1
            and isinstance(call.args[0].value, cst.Dict)
        ):
            return None
        return call.func.value.value, call.args[0].value

    def _merge_table_updates(
        self, body: list[cst.BaseStatement]
    ) -> list[cst.BaseStatement]:
        """
        Merge `name.update({...})` into a `name = {...}` literal that
        immediately precedes it.
        """
        merged: list[cst.BaseStatement] = []
        for statement in body:
            update = self._table_update(statement)
            if update is not None and merged:
                table = self._table_literal(merged[-1])
                if table is not None and table[0] == update[0]:
                    name, literal = table
                    _, extra = update
                    elements = list(literal.elements)
                    separator = cst.Comma(
                        whitespace_after=literal.lbrace.whitespace_after
                    )
                    # The literal's own closing comma moves to the new
                    # last element; everything before it is separated
                    # the way the literal already separates entries.
                    closing: cst.Comma | cst.MaybeSentinel = (
                        elements[-1].comma if elements else cst.Comma()
                    )
                    if elements:
                        elements[-1] = elements[-1].with_changes(
                            comma=separator
                        )
                    for element in extra.elements:
                        elements.append(element.with_changes(comma=separator))
                    elements[-1] = elements[-1].with_changes(comma=closing)
                    new_literal = literal.with_changes(elements=elements)
                    previous = merged[-1]
                    assert isinstance(previous, cst.SimpleStatementLine)
                    small = previous.body[0]
                    merged[-1] = previous.with_changes(
                        body=[small.with_changes(value=new_literal)]
                    )
                    continue
            merged.append(statement)
        return merged

    # The flags module

    def _strip_decided_flags(
        self, body: list[cst.BaseStatement]
    ) -> list[cst.BaseStatement]:
        """
        Drop decided flags, their docstrings and their table entries.
        """
        decided_numbers = {_flag_number(name) for name in self.decided}
        result: list[cst.BaseStatement] = []
        skip_docstring = False
        for statement in body:
            if skip_docstring and _is_docstring_line(statement):
                skip_docstring = False
                continue
            skip_docstring = False
            if (
                isinstance(statement, cst.SimpleStatementLine)
                and len(statement.body) == 1
                and isinstance(statement.body[0], cst.AnnAssign)
                and isinstance(statement.body[0].target, cst.Name)
                and statement.body[0].target.value in self.decided
            ):
                skip_docstring = True
                continue
            stripped = statement.visit(_DecidedEntryRemover(decided_numbers))
            assert isinstance(stripped, cst.BaseStatement)
            result.append(stripped)
        return result


class _DecidedEntryRemover(cst.CSTTransformer):
    """
    Remove dict entries keyed by, and set members equal to, decided EIP
    numbers inside the flags module's tables.
    """

    def __init__(self, numbers: set[int]) -> None:
        self._numbers = numbers
        self._emptied: set[cst.DictElement] = set()
        self._emptied_calls: set[cst.Call] = set()

    def _is_decided(self, expression: cst.BaseExpression) -> bool:
        return (
            isinstance(expression, cst.Integer)
            and int(expression.value) in self._numbers
        )

    def _empties(self, expression: cst.BaseExpression) -> bool:
        """Whether a `frozenset({...})` value loses every member."""
        if not (
            isinstance(expression, cst.Call)
            and len(expression.args) == 1
            and isinstance(expression.args[0].value, cst.Set)
        ):
            return False
        return all(
            self._is_decided(element.value)
            for element in expression.args[0].value.elements
        )

    @override
    def visit_DictElement(self, node: cst.DictElement) -> bool:  # noqa: D102
        if self._is_decided(node.key) or self._empties(node.value):
            # An entry keyed by a decided EIP, or whose requirements
            # are all decided, goes as a whole; a set literal cannot be
            # left empty.
            self._emptied.add(node)
            return False
        return True

    @override
    def visit_Call(self, node: cst.Call) -> bool:  # noqa: D102
        if _is_name(node.func, "frozenset") and self._empties(node):
            # A set nested in a record, such as a `Target`'s `eips`,
            # keeps its entry and becomes `frozenset()`.
            self._emptied_calls.add(node)
            return False
        return True

    @override
    def leave_Call(  # noqa: D102
        self, original_node: cst.Call, updated_node: cst.Call
    ) -> cst.Call:
        if original_node in self._emptied_calls:
            return updated_node.with_changes(args=[])
        return updated_node

    @override
    def leave_DictElement(  # noqa: D102
        self, original_node: cst.DictElement, updated_node: cst.DictElement
    ) -> cst.DictElement | cst.RemovalSentinel:
        if original_node in self._emptied:
            return cst.RemoveFromParent()
        return updated_node

    @override
    def leave_Dict(  # noqa: D102
        self, original_node: cst.Dict, updated_node: cst.Dict
    ) -> cst.Dict:
        if updated_node.elements:
            return updated_node
        return cst.Dict(elements=[])

    @override
    def leave_Element(  # noqa: D102
        self, original_node: cst.Element, updated_node: cst.Element
    ) -> cst.Element | cst.RemovalSentinel:
        if self._is_decided(updated_node.value):
            return cst.RemoveFromParent()
        return updated_node


def _is_name(node: cst.CSTNode, value: str) -> bool:
    return isinstance(node, cst.Name) and node.value == value


def _flag_name_check(name: str) -> str:
    _flag_number(name)
    return name
