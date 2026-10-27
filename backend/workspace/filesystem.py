"""Secure Filesystem Management Service for Autonomous AI Software Development Workspace.
Enforces strict workspace directory boundary confinement (preventing path traversal),
optimistic concurrency conflict detection, and high-performance recursive directory trees.
"""
from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from backend.models.contracts import (
    FileOperationRequest,
    FileReadResponse,
    FileTreeNode,
    FileWriteRequest,
    FileWriteResponse,
)

IGNORED_DIRECTORIES: Set[str] = {
    ".git",
    "__pycache__",
    ".venv",
    "node_modules",
    ".pytest_cache",
    ".mypy_cache",
    ".coverage",
    ".agent_memory.db-journal",
}

LANGUAGE_EXTENSION_MAP: Dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".html": "html",
    ".css": "css",
    ".json": "json",
    ".md": "markdown",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".sql": "sql",
    ".sh": "shell",
    ".bash": "shell",
    ".ps1": "powershell",
    ".bat": "bat",
    ".txt": "plaintext",
    ".toml": "ini",
    ".ini": "ini",
    ".env": "ini",
    ".xml": "xml",
}


def safe_resolve_path(project_root: Path, rel_path: str) -> Path:
    """
    Strict Path Confinement: Ensures target path strictly resolves within the project_root.
    Protects against path traversal attacks (e.g. '../', absolute system paths, null bytes).
    """
    root_resolved = project_root.resolve()
    # Normalize path separators
    clean_rel = rel_path.lstrip("/\\")
    target_resolved = (root_resolved / clean_rel).resolve()

    try:
        target_resolved.relative_to(root_resolved)
    except ValueError:
        raise PermissionError(
            f"Security Violation: Target path '{rel_path}' resolves outside project root '{project_root}'."
        )

    return target_resolved


def detect_file_language(file_path: Path) -> str:
    """Maps file extension to Monaco Editor language mode identifier."""
    ext = file_path.suffix.lower()
    return LANGUAGE_EXTENSION_MAP.get(ext, "plaintext")


class WorkspaceFilesystem:
    """Handles real filesystem operations within validated project workspace bounds."""

    def __init__(self, project_root: Union[str, Path]):
        self.project_root = Path(project_root).resolve()
        if not self.project_root.exists():
            raise FileNotFoundError(f"Project directory '{project_root}' does not exist.")

    def safe_resolve_path(self, rel_path: str) -> Path:
        """Resolves target relative path safely within this workspace root."""
        return safe_resolve_path(self.project_root, rel_path)

    def get_directory_tree(self, sub_dir: str = "", max_depth: int = 5) -> List[FileTreeNode]:
        """
        Recursively builds an hierarchical tree structure of the project filesystem.
        Excludes heavy transient directories (.git, .venv, node_modules).
        """
        base_path = safe_resolve_path(self.project_root, sub_dir)
        return self._build_tree_recursive(base_path, current_depth=0, max_depth=max_depth)

    def _build_tree_recursive(self, current_dir: Path, current_depth: int, max_depth: int) -> List[FileTreeNode]:
        if current_depth > max_depth or not current_dir.is_dir():
            return []

        nodes: List[FileTreeNode] = []
        try:
            entries = sorted(list(current_dir.iterdir()), key=lambda p: (not p.is_dir(), p.name.lower()))
        except PermissionError:
            return []

        for entry in entries:
            if entry.name in IGNORED_DIRECTORIES:
                continue

            rel_path = str(entry.relative_to(self.project_root)).replace("\\", "/")
            stat = entry.stat()
            is_dir = entry.is_dir()

            node = FileTreeNode(
                name=entry.name,
                path=rel_path,
                is_directory=is_dir,
                size_bytes=stat.st_size if not is_dir else 0,
                modified_time=stat.st_mtime,
                git_status=None,
            )

            if is_dir:
                node.children = self._build_tree_recursive(entry, current_depth + 1, max_depth)

            nodes.append(node)

        return nodes

    def read_file(self, rel_path: str) -> FileReadResponse:
        """Reads real file content, language mode, and modification timestamp."""
        target_file = safe_resolve_path(self.project_root, rel_path)
        if not target_file.exists() or not target_file.is_file():
            raise FileNotFoundError(f"File '{rel_path}' not found in workspace.")

        stat = target_file.stat()
        try:
            content = target_file.read_text(encoding="utf-8")
            is_readonly = False
        except UnicodeDecodeError:
            content = f"// Binary file or non-UTF8 format ({stat.st_size} bytes)"
            is_readonly = True

        return FileReadResponse(
            path=rel_path,
            content=content,
            size_bytes=stat.st_size,
            modified_time=stat.st_mtime,
            language=detect_file_language(target_file),
            is_readonly=is_readonly,
        )

    def write_file(
        self,
        rel_path_or_req: Union[str, FileWriteRequest],
        content: Optional[str] = None,
        base_modified_time: Optional[float] = None,
    ) -> FileWriteResponse:
        """
        Writes file with optimistic concurrency conflict detection.
        If file on disk was modified externally after base_modified_time, returns conflict flag.
        """
        if isinstance(rel_path_or_req, FileWriteRequest):
            rel_path = rel_path_or_req.path
            content = rel_path_or_req.content
            base_modified_time = rel_path_or_req.base_modified_time
            force = getattr(rel_path_or_req, "force", False)
        else:
            rel_path = rel_path_or_req
            content = content or ""
            force = False

        target_file = safe_resolve_path(self.project_root, rel_path)

        if not force and target_file.exists() and base_modified_time is not None:
            disk_mtime = target_file.stat().st_mtime
            # Check if disk mtime differs by more than 0.2s from client base
            if abs(disk_mtime - base_modified_time) > 0.2:
                server_content = target_file.read_text(encoding="utf-8", errors="replace")
                return FileWriteResponse(
                    status="conflict",
                    path=rel_path,
                    size_bytes=target_file.stat().st_size,
                    modified_time=disk_mtime,
                    conflict_detected=True,
                    server_content=server_content,
                    success=False,
                )

        # Ensure parent directories exist
        target_file.parent.mkdir(parents=True, exist_ok=True)
        target_file.write_text(content, encoding="utf-8")
        new_stat = target_file.stat()

        return FileWriteResponse(
            status="saved",
            path=rel_path,
            size_bytes=new_stat.st_size,
            modified_time=new_stat.st_mtime,
            conflict_detected=False,
            success=True,
        )

    def create_entry(self, rel_path: str, is_directory: bool = False) -> bool:
        """Creates a new file or directory within workspace bounds."""
        target = safe_resolve_path(self.project_root, rel_path)
        if target.exists():
            return False

        if is_directory:
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("", encoding="utf-8")
        return True

    def create_file_or_dir(self, req: Union[str, FileOperationRequest], is_directory: bool = False) -> bool:
        """Creates entry using string or FileOperationRequest."""
        if isinstance(req, FileOperationRequest):
            is_dir = req.is_directory or (getattr(req, "operation", "") == "create_dir")
            return self.create_entry(req.path, is_directory=is_dir)
        return self.create_entry(req, is_directory=is_directory)

    def rename_entry(self, old_rel: str, new_rel: str) -> bool:
        """Renames an existing file or directory within workspace bounds."""
        old_target = safe_resolve_path(self.project_root, old_rel)
        new_target = safe_resolve_path(self.project_root, new_rel)

        if not old_target.exists() or new_target.exists():
            return False

        new_target.parent.mkdir(parents=True, exist_ok=True)
        old_target.rename(new_target)
        return True

    def rename_file_or_dir(self, req: Union[str, FileOperationRequest], new_path: Optional[str] = None) -> bool:
        """Renames entry using string or FileOperationRequest."""
        if isinstance(req, FileOperationRequest):
            return self.rename_entry(req.path, req.new_path or "")
        return self.rename_entry(req, new_path or "")

    def delete_entry(self, rel_path: str) -> bool:
        """Deletes a file or directory with workspace root protection."""
        target = safe_resolve_path(self.project_root, rel_path)
        if target == self.project_root:
            raise PermissionError("Forbidden: Cannot delete project root workspace directory.")

        if not target.exists():
            return False

        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
        return True

    def delete_file_or_dir(self, req: Union[str, FileOperationRequest]) -> bool:
        """Deletes entry using string or FileOperationRequest."""
        if isinstance(req, FileOperationRequest):
            return self.delete_entry(req.path)
        return self.delete_entry(req)
