"""AST Symbol Extractor and Static Code Analyzer for Unit Testing Agent.
Parses Python source files to extract classes, functions, arguments, complexity,
and branch points.
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class FunctionSymbol(BaseModel):
    name: str
    qualname: str
    start_line: int
    end_line: int
    args: List[str] = Field(default_factory=list)
    return_annotation: Optional[str] = None
    docstring: Optional[str] = None
    is_async: bool = False
    is_method: bool = False
    class_name: Optional[str] = None
    cyclomatic_complexity: int = 1
    branch_lines: List[int] = Field(default_factory=list)


class ClassSymbol(BaseModel):
    name: str
    start_line: int
    end_line: int
    bases: List[str] = Field(default_factory=list)
    docstring: Optional[str] = None
    methods: List[FunctionSymbol] = Field(default_factory=list)


class ModuleSymbols(BaseModel):
    file_path: str
    classes: List[ClassSymbol] = Field(default_factory=list)
    functions: List[FunctionSymbol] = Field(default_factory=list)
    imports: List[str] = Field(default_factory=list)


class ASTSymbolExtractor(ast.NodeVisitor):
    """Visits Python AST nodes to extract architectural symbols and complexity."""

    def __init__(self, source_code: str, file_path: str = ""):
        self.source_code = source_code
        self.file_path = file_path
        self.classes: List[ClassSymbol] = []
        self.functions: List[FunctionSymbol] = []
        self.imports: List[str] = []
        self._current_class: Optional[str] = None

    def _calc_complexity(self, node: ast.AST) -> tuple[int, List[int]]:
        """Calculates cyclomatic complexity and locates branch decision lines."""
        complexity = 1
        branch_lines: List[int] = []

        for subnode in ast.walk(node):
            if isinstance(subnode, (ast.If, ast.While, ast.For, ast.ExceptHandler, ast.With)):
                complexity += 1
                if hasattr(subnode, "lineno"):
                    branch_lines.append(subnode.lineno)
            elif isinstance(subnode, ast.BoolOp):
                # 'and' or 'or' adds decision branches
                complexity += len(subnode.values) - 1
                if hasattr(subnode, "lineno"):
                    branch_lines.append(subnode.lineno)

        return complexity, sorted(list(set(branch_lines)))

    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            self.imports.append(alias.name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        mod = node.module or ""
        for alias in node.names:
            self.imports.append(f"{mod}.{alias.name}")
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef):
        prev_class = self._current_class
        self._current_class = node.name

        bases = [ast.unparse(b) for b in node.bases]
        docstring = ast.get_docstring(node)

        class_symbol = ClassSymbol(
            name=node.name,
            start_line=node.lineno,
            end_line=node.end_lineno or node.lineno,
            bases=bases,
            docstring=docstring,
        )

        self.classes.append(class_symbol)
        self.generic_visit(node)
        self._current_class = prev_class

    def visit_FunctionDef(self, node: ast.FunctionDef):
        self._handle_function(node, is_async=False)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        self._handle_function(node, is_async=True)

    def _handle_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef, is_async: bool):
        args = [arg.arg for arg in node.args.args]
        return_ann = ast.unparse(node.returns) if node.returns else None
        docstring = ast.get_docstring(node)
        complexity, branch_lines = self._calc_complexity(node)

        is_method = self._current_class is not None
        qualname = f"{self._current_class}.{node.name}" if is_method else node.name

        fn_symbol = FunctionSymbol(
            name=node.name,
            qualname=qualname,
            start_line=node.lineno,
            end_line=node.end_lineno or node.lineno,
            args=args,
            return_annotation=return_ann,
            docstring=docstring,
            is_async=is_async,
            is_method=is_method,
            class_name=self._current_class,
            cyclomatic_complexity=complexity,
            branch_lines=branch_lines,
        )

        if is_method and self.classes:
            self.classes[-1].methods.append(fn_symbol)
        else:
            self.functions.append(fn_symbol)

        # Do not recurse into inner functions for top-level collection, or call generic_visit
        self.generic_visit(node)


def extract_symbols_from_file(file_path: Path) -> ModuleSymbols:
    """Parses a Python file and returns its architectural symbols."""
    text = file_path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(file_path))
    visitor = ASTSymbolExtractor(source_code=text, file_path=str(file_path))
    visitor.visit(tree)

    return ModuleSymbols(
        file_path=str(file_path),
        classes=visitor.classes,
        functions=visitor.functions,
        imports=visitor.imports,
    )
