"""Coverage report parser for extracting line and branch coverage metrics from coverage.py / pytest-cov."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List
from backend.models.contracts import CoverageGap


def parse_coverage_json(coverage_json_path: Path) -> Tuple[Optional[float], Optional[float], List[CoverageGap]]:
    """
    Parses a coverage.json file produced by `pytest --cov --cov-report=json:coverage.json`.
    Returns:
        (line_coverage_pct, branch_coverage_pct, coverage_gaps)
    """
    if not coverage_json_path.exists():
        return None, None, []

    try:
        with open(coverage_json_path, "r", encoding="utf-8") as f:
            data: Dict[str, Any] = json.load(f)

        totals = data.get("totals", {})
        line_pct = totals.get("percent_covered")
        if line_pct is not None:
            line_pct = round(float(line_pct), 2)

        # Branch coverage if enabled
        total_branches = totals.get("num_branches", 0)
        covered_branches = totals.get("covered_branches", 0)
        branch_pct = None
        if total_branches > 0:
            branch_pct = round((covered_branches / total_branches) * 100.0, 2)

        gaps: List[CoverageGap] = []
        files = data.get("files", {})
        for filepath, file_info in files.items():
            missing_lines = file_info.get("missing_lines", [])
            missing_branches = file_info.get("missing_branches", [])
            if missing_lines or missing_branches:
                # Group consecutive missing lines
                if missing_lines:
                    start = missing_lines[0]
                    prev = start
                    for line in missing_lines[1:]:
                        if line == prev + 1:
                            prev = line
                        else:
                            gaps.append(
                                CoverageGap(
                                    file_path=filepath,
                                    symbol_name=f"lines_{start}_{prev}",
                                    symbol_type="lines",
                                    start_line=start,
                                    end_line=prev,
                                )
                            )
                            start = line
                            prev = line
                    gaps.append(
                        CoverageGap(
                            file_path=filepath,
                            symbol_name=f"lines_{start}_{prev}",
                            symbol_type="lines",
                            start_line=start,
                            end_line=prev,
                            missing_branches=[b[0] if isinstance(b, list) else b for b in missing_branches],
                        )
                    )

        return line_pct, branch_pct, gaps

    except Exception:
        return None, None, []
