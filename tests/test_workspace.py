"""Acceptance tests for Autonomous AI Software Development Workspace.

Validates:
1. WorkspaceFilesystem: safe path confinement, tree building, file read/write,
   optimistic concurrency conflict detection, and file operations.
2. WorkspaceTerminalService: sandboxed command execution, timeouts, secret redaction.
3. SoftwareDevelopmentAgent: autonomous feature code implementation & diff generation.
4. CodeReviewSecurityAgent: AST analysis for dangerous calls, secret leakage, security verdict.
5. REST API Endpoints: /api/fs/*, /api/terminal/execute, and /api/orchestrator/* lifecycle.
"""

import os
import time
import pytest
from pathlib import Path
from fastapi.testclient import TestClient

from backend.api.app import app
from backend.workspace.filesystem import WorkspaceFilesystem
from backend.workspace.terminal import WorkspaceTerminalService
from backend.agents.dev_agent import SoftwareDevelopmentAgent
from backend.agents.security_agent import CodeReviewSecurityAgent
from backend.models.contracts import (
    FileWriteRequest,
    FileOperationRequest,
    TerminalExecuteRequest,
    TaskStatus,
)


@pytest.fixture
def workspace_dir(tmp_path):
    """Fixture providing an isolated project workspace directory with sample files."""
    proj = tmp_path / "sample_project"
    proj.mkdir()
    (proj / "src").mkdir()
    (proj / "tests").mkdir()
    
    (proj / "README.md").write_text("# Sample Project\nAn autonomous test workspace.", encoding="utf-8")
    (proj / "src" / "app.py").write_text("def hello():\n    return 'world'\n", encoding="utf-8")
    (proj / "tests" / "test_app.py").write_text("from src.app import hello\ndef test_hello():\n    assert hello() == 'world'\n", encoding="utf-8")
    return proj


def test_filesystem_safe_path_resolution(workspace_dir):
    """Verify path traversal attacks outside workspace are rejected with PermissionError."""
    fs = WorkspaceFilesystem(str(workspace_dir))
    
    # Safe internal path
    safe_resolved = fs.safe_resolve_path("src/app.py")
    assert safe_resolved.exists()
    assert safe_resolved.is_file()
    
    # Path traversal attack using ../
    with pytest.raises(PermissionError):
        fs.safe_resolve_path("../outside.txt")
        
    with pytest.raises(PermissionError):
        fs.safe_resolve_path("../../etc/passwd")


def test_filesystem_tree_generation(workspace_dir):
    """Verify recursive directory tree matches actual project structure."""
    fs = WorkspaceFilesystem(str(workspace_dir))
    tree = fs.get_directory_tree()
    
    names = [c.name for c in tree]
    assert "src" in names
    assert "tests" in names
    assert "README.md" in names
    
    src_node = next(c for c in tree if c.name == "src")
    assert src_node.is_directory is True
    assert any(c.name == "app.py" for c in src_node.children)


def test_filesystem_read_and_write(workspace_dir):
    """Verify reading file metadata and writing updates."""
    fs = WorkspaceFilesystem(str(workspace_dir))
    
    # Read
    read_resp = fs.read_file("src/app.py")
    assert "def hello():" in read_resp.content
    assert read_resp.language == "python"
    assert read_resp.modified_time > 0
    
    # Write normal
    write_req = FileWriteRequest(
        path="src/app.py",
        content="def hello():\n    return 'antigravity'\n",
        base_modified_time=read_resp.modified_time,
    )
    write_resp = fs.write_file(write_req)
    assert write_resp.success is True
    assert write_resp.conflict_detected is False
    
    # Verify file updated
    updated = fs.read_file("src/app.py")
    assert "antigravity" in updated.content


def test_filesystem_optimistic_concurrency_conflict(workspace_dir):
    """Verify conflict detection when file was modified on disk since base_modified_time."""
    fs = WorkspaceFilesystem(str(workspace_dir))
    read_resp = fs.read_file("src/app.py")
    
    # Simulate an external modification or agent write
    time.sleep(0.3)
    (workspace_dir / "src" / "app.py").write_text("def hello():\n    return 'agent_write'\n", encoding="utf-8")
    
    # User attempts to save with stale base_modified_time
    write_req = FileWriteRequest(
        path="src/app.py",
        content="def hello():\n    return 'user_conflict'\n",
        base_modified_time=read_resp.modified_time,
        force=False,
    )
    write_resp = fs.write_file(write_req)
    assert write_resp.success is False
    assert write_resp.conflict_detected is True
    assert "agent_write" in write_resp.server_content


def test_filesystem_file_operations(workspace_dir):
    """Verify file creation, renaming, and deletion."""
    fs = WorkspaceFilesystem(str(workspace_dir))
    
    # Create file
    create_req = FileOperationRequest(operation="create_file", path="src/utils.py")
    assert fs.create_file_or_dir(create_req) is True
    assert (workspace_dir / "src" / "utils.py").exists()
    
    # Rename file
    rename_req = FileOperationRequest(operation="rename", path="src/utils.py", new_path="src/helpers.py")
    assert fs.rename_file_or_dir(rename_req) is True
    assert not (workspace_dir / "src" / "utils.py").exists()
    assert (workspace_dir / "src" / "helpers.py").exists()
    
    # Delete file
    delete_req = FileOperationRequest(operation="delete", path="src/helpers.py")
    assert fs.delete_file_or_dir(delete_req) is True
    assert not (workspace_dir / "src" / "helpers.py").exists()


def test_terminal_service_execution(workspace_dir):
    """Verify terminal runs sandboxed shell commands and captures exit code and output."""
    terminal = WorkspaceTerminalService()
    cmd = "cmd.exe /c echo Hello Terminal" if os.name == "nt" else "echo 'Hello Terminal'"
    resp = terminal.execute_command(workspace_dir, cmd, timeout_seconds=5)
    
    assert resp.exit_code == 0
    assert "Hello Terminal" in resp.stdout
    assert resp.duration_ms >= 0


def test_terminal_service_secret_redaction():
    """Verify secret scrubbing in terminal output."""
    terminal = WorkspaceTerminalService()
    text = "Authorization: Bearer sk-ant-api03-secret1234567890abcdef and token ghp_123456789012345678901234567890123456"
    scrubbed = terminal.redact_secrets(text)
    assert "sk-ant-api03-secret1234567890abcdef" not in scrubbed
    assert "ghp_123456789012345678901234567890123456" not in scrubbed


def test_software_development_agent():
    """Verify SoftwareDevelopmentAgent generates implementation patches."""
    agent = SoftwareDevelopmentAgent()
    orig_code = "def calculate_total(items):\n    return sum(items)\n"
    res = agent.implement_feature(
        requirement="Add tax calculation of 10% to calculate_total",
        target_file="billing.py",
        existing_content=orig_code,
    )
    assert res.target_file == "billing.py"
    assert res.unified_diff != ""
    assert "calculate_total" in res.modified_code


def test_code_review_security_agent_dangerous_patterns():
    """Verify CodeReviewSecurityAgent detects dangerous AST calls and secrets."""
    agent = CodeReviewSecurityAgent()
    vulnerable_code = (
        "import os\n"
        "def run_user_cmd(user_input):\n"
        "    api_key = 'sk-1234567890abcdef1234567890'\n"
        "    return os.system(user_input)\n"
    )
    review = agent.review_patch("cmd_runner.py", vulnerable_code)
    assert review.recommendation == "REJECT"
    assert len(review.vulnerabilities) >= 1
    
    severities = [v.severity.value if hasattr(v.severity, "value") else str(v.severity) for v in review.vulnerabilities]
    assert "critical" in severities or "high" in severities


def test_workspace_api_endpoints(workspace_dir):
    """Verify FastAPI workspace endpoints work against an initialized project."""
    client = TestClient(app)
    
    # 1. Initialize project
    init_res = client.post(
        "/api/projects/create",
        json={
            "name": "Integration Workspace Proj",
            "repo_path": str(workspace_dir),
            "requirements": ["Build real API", "Add testing"],
            "acceptance_criteria": ["All unit tests pass", "Zero critical defects"],
        },
    )
    assert init_res.status_code == 200
    proj_data = init_res.json()
    proj_id = proj_data["project"]["project_id"]
    
    # 2. Query FS Tree
    tree_res = client.get(f"/api/fs/tree?project_id={proj_id}")
    assert tree_res.status_code == 200
    tree_items = tree_res.json()
    assert any(item["name"] == "src" for item in tree_items)
    
    # 3. Read file
    read_res = client.get(f"/api/fs/read?project_id={proj_id}&path=src/app.py")
    assert read_res.status_code == 200
    data = read_res.json()
    assert "def hello():" in data["content"]
    
    # 4. Save file
    write_res = client.post(
        "/api/fs/write",
        json={
            "project_id": proj_id,
            "path": "src/app.py",
            "content": "def hello():\n    return 'fastapi_saved'\n",
            "base_modified_time": data["modified_time"],
            "force": False,
        },
    )
    assert write_res.status_code == 200
    assert write_res.json()["success"] is True
    
    # 5. Terminal execution endpoint
    cmd = "cmd.exe /c echo Workspace API Terminal" if os.name == "nt" else "echo 'Workspace API Terminal'"
    term_res = client.post(
        "/api/terminal/execute",
        json={
            "project_id": proj_id,
            "command": cmd,
            "timeout_seconds": 5,
        },
    )
    assert term_res.status_code == 200
    assert "Workspace API Terminal" in term_res.json()["stdout"]
    
    # 6. Orchestrator lifecycle
    start_res = client.post(f"/api/orchestrator/{proj_id}/start")
    assert start_res.status_code == 200
    assert start_res.json()["state"] in ["RUNNING", "ANALYZING"]
    
    pause_res = client.post(f"/api/orchestrator/{proj_id}/pause")
    assert pause_res.status_code == 200
    assert pause_res.json()["state"] == "PAUSED"
    
    resume_res = client.post(f"/api/orchestrator/{proj_id}/resume")
    assert resume_res.status_code == 200
    assert resume_res.json()["state"] == "RUNNING"
    
    tasks_res = client.get(f"/api/orchestrator/{proj_id}/tasks")
    assert tasks_res.status_code == 200
    assert isinstance(tasks_res.json(), list)
    assert len(tasks_res.json()) > 0
    
    changes_res = client.get(f"/api/orchestrator/{proj_id}/changes")
    assert changes_res.status_code == 200
    assert isinstance(changes_res.json(), list)
    
    report_res = client.get(f"/api/orchestrator/{proj_id}/report")
    assert report_res.status_code == 200
    assert "project_id" in report_res.json()
    
    stop_res = client.post(f"/api/orchestrator/{proj_id}/stop")
    assert stop_res.status_code == 200
    assert stop_res.json()["state"] == "STOPPED"
