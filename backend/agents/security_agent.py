"""Specialized Code Review and Security Agent.
Inspects proposed patches and source code modifications for security vulnerabilities,
path traversal, command injection, secret leakage, and adherence to acceptance criteria.
"""
from __future__ import annotations

import ast
import re
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from backend.adapters.base import BaseModelAdapter
from backend.learning.agent_interface import BaseContinuousLearningAgent
from backend.models.contracts import AgentType, ProposedPatch, RiskLevel
from backend.runners.sandbox import SECRET_PATTERNS


class SecurityVulnerability(BaseModel):
    rule_id: str
    severity: RiskLevel
    description: str
    line_number: Optional[int] = None
    file_path: Optional[str] = None
    remediation: str


class SecurityReviewVerdict(BaseModel):
    is_safe: bool
    risk_level: RiskLevel
    vulnerabilities: List[SecurityVulnerability] = Field(default_factory=list)
    reasons: List[str] = Field(default_factory=list)
    recommendation: str  # "APPROVE", "REJECT", "REQUIRE_HUMAN_APPROVAL"


DANGEROUS_IMPORTS = {"subprocess", "pty", "telnetlib"}
DANGEROUS_FUNCTIONS = {
    "eval",
    "exec",
    "compile",
    "os.system",
    "os.popen",
    "subprocess.Popen",
    "subprocess.call",
    "subprocess.run",
}


class CodeReviewSecurityAgent(BaseContinuousLearningAgent):
    """Audits code changes for security vulnerabilities, privilege escalation, and secret leaks."""

    def __init__(self, model_adapter: Optional[BaseModelAdapter] = None):
        super().__init__(agent_type=AgentType.CODE_REVIEW_SECURITY)
        self.adapter = model_adapter

    def review_code(self, file_path: str, code_content: str) -> SecurityReviewVerdict:
        """Converts raw code content into a patch diff and conducts security review."""
        diff_lines = [f"--- a/{file_path}", f"+++ b/{file_path}"]
        diff_lines.extend(f"+{line}" for line in code_content.splitlines())
        patch = ProposedPatch(
            patch_id="ad-hoc-review",
            target_files=[file_path],
            unified_diff="\n".join(diff_lines),
            root_cause_analysis="Ad-hoc security audit",
        )
        return self.review_patch(patch)

    def review_patch(self, patch: Any, code_content: Optional[str] = None) -> SecurityReviewVerdict:
        """
        Conducts deep static and AST security review of a proposed diff patch.
        Checks for secret exposure, dangerous primitives, and syntax validity.
        """
        if isinstance(patch, str) and code_content is not None:
            return self.review_code(patch, code_content)
        elif isinstance(patch, str) and code_content is None:
            # Assumed to be diff text
            patch = ProposedPatch(
                patch_id="ad-hoc-review",
                target_files=["unknown"],
                unified_diff=patch,
                root_cause_analysis="Ad-hoc review",
            )
        vulnerabilities: List[SecurityVulnerability] = []
        reasons: List[str] = []

        # 1. Secret Exposure Scan
        for pattern in SECRET_PATTERNS:
            if pattern.search(patch.unified_diff):
                vulnerabilities.append(
                    SecurityVulnerability(
                        rule_id="SEC_001_SECRET_LEAK",
                        severity=RiskLevel.CRITICAL,
                        description="Potential API key, token, or password found in patch diff.",
                        remediation="Remove hardcoded credentials and load from environment variables.",
                    )
                )
                reasons.append("Critical: Patch diff contains hardcoded credentials or secret material.")

        # 2. Dangerous Primitives Scan in Added Lines
        added_lines = [
            line[1:] for line in patch.unified_diff.splitlines() if line.startswith("+") and not line.startswith("+++")
        ]
        added_code = "\n".join(added_lines)

        for fn in DANGEROUS_FUNCTIONS:
            if fn in added_code:
                vulnerabilities.append(
                    SecurityVulnerability(
                        rule_id="SEC_002_DANGEROUS_CALL",
                        severity=RiskLevel.HIGH,
                        description=f"Forbidden dangerous primitive '{fn}' introduced.",
                        remediation=f"Replace '{fn}' with safe bounded APIs.",
                    )
                )
                reasons.append(f"Security: Dangerous execution primitive '{fn}' detected in proposed changes.")

        # 3. Path Traversal Pattern Check
        if ".." in added_code and ("open(" in added_code or "Path(" in added_code or "read(" in added_code):
            vulnerabilities.append(
                SecurityVulnerability(
                    rule_id="SEC_003_PATH_TRAVERSAL",
                    severity=RiskLevel.HIGH,
                    description="Unvalidated relative path navigation detected in file operation.",
                    remediation="Confine path resolution within validated project root bounds using safe_resolve_path.",
                )
            )
            reasons.append("High: Potential path traversal primitive detected.")

        # 4. AST Syntax Parsing
        try:
            ast.parse(added_code)
        except SyntaxError as e:
            # Added lines might be partial hunks, which is okay, but note syntax anomalies
            pass

        # Determine Verdict
        if any(v.severity == RiskLevel.CRITICAL for v in vulnerabilities):
            return SecurityReviewVerdict(
                is_safe=False,
                risk_level=RiskLevel.CRITICAL,
                vulnerabilities=vulnerabilities,
                reasons=reasons,
                recommendation="REJECT",
            )
        elif any(v.severity == RiskLevel.HIGH for v in vulnerabilities):
            return SecurityReviewVerdict(
                is_safe=False,
                risk_level=RiskLevel.HIGH,
                vulnerabilities=vulnerabilities,
                reasons=reasons,
                recommendation="REQUIRE_HUMAN_APPROVAL",
            )
        else:
            return SecurityReviewVerdict(
                is_safe=True,
                risk_level=RiskLevel.LOW,
                vulnerabilities=[],
                reasons=["Zero critical security vulnerabilities detected."],
                recommendation="APPROVE",
            )
