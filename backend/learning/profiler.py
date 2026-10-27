"""Automated Project Profiler and Applicability Gate.
Fingerprints software repositories by scanning dependencies, file extensions, and architectures.
Validates lesson applicability preconditions to prevent blind cross-project contamination.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional, Tuple

from backend.learning.models import ProjectProfile
from backend.memory.models import SemanticLesson


class ProjectProfiler:
    """Detects repository language, framework, database, and testing runner."""

    @staticmethod
    def profile_directory(project_dir: Path, project_id: Optional[str] = None) -> ProjectProfile:
        """Statically inspects project files to generate an architectural profile."""
        pid = project_id or project_dir.name
        pname = project_dir.name

        language = "python"
        framework = "generic"
        test_runner = "pytest"
        database_layer = None
        tags = []

        # 1. Dependency and package scan
        req_file = project_dir / "requirements.txt"
        package_json = project_dir / "package.json"
        pyproject = project_dir / "pyproject.toml"

        content = ""
        if req_file.exists():
            content += req_file.read_text(encoding="utf-8").lower()
        if pyproject.exists():
            content += pyproject.read_text(encoding="utf-8").lower()

        if package_json.exists():
            language = "typescript"
            tags.append("node")
            try:
                data = json.loads(package_json.read_text(encoding="utf-8"))
                deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
                if "react" in deps:
                    framework = "react"
                elif "express" in deps:
                    framework = "express"
                if "playwright" in deps or "@playwright/test" in deps:
                    test_runner = "playwright"
                elif "jest" in deps:
                    test_runner = "jest"
            except Exception:
                pass

        if "fastapi" in content:
            framework = "fastapi"
            tags.append("async_api")
        elif "flask" in content:
            framework = "flask"
        elif "django" in content:
            framework = "django"

        if "sqlalchemy" in content:
            database_layer = "sqlalchemy"
        elif "tortoise-orm" in content:
            database_layer = "tortoise"

        if "pytest" in content:
            test_runner = "pytest"

        return ProjectProfile(
            project_id=pid,
            project_name=pname,
            root_path=str(project_dir),
            primary_language=language,
            framework=framework,
            test_runner=test_runner,
            database_layer=database_layer,
            tags=tags,
        )

    @staticmethod
    def check_applicability(lesson: SemanticLesson, profile: ProjectProfile) -> Tuple[bool, str]:
        """
        Applicability Gate:
        Evaluates whether a reusable lesson's conditions match the target project's profile.
        Returns:
            (is_applicable: bool, reason: str)
        """
        conditions = lesson.applicable_conditions or {}

        # 1. Language constraint
        req_lang = conditions.get("language")
        if req_lang and req_lang.lower() != profile.primary_language.lower():
            return False, f"Language mismatch: requires {req_lang}, project is {profile.primary_language}"

        # 2. Framework constraint
        req_framework = conditions.get("framework")
        if req_framework and req_framework.lower() != profile.framework.lower() and req_framework.lower() != "generic":
            return False, f"Framework mismatch: requires {req_framework}, project is {profile.framework}"

        # 3. Test runner constraint
        req_runner = conditions.get("test_runner")
        if req_runner and req_runner.lower() != profile.test_runner.lower():
            return False, f"Test runner mismatch: requires {req_runner}, project is {profile.test_runner}"

        # 4. Database layer constraint
        req_db = conditions.get("database_layer")
        if req_db and req_db.lower() != (profile.database_layer or "").lower():
            return False, f"Database layer mismatch: requires {req_db}, project is {profile.database_layer or 'none'}"

        return True, "Preconditions satisfied by target project profile."
