"""JUnit XML parser for extracting granular test case results and stack traces."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List, Optional
from backend.models.contracts import TestCaseResult, TestStatus, TestSuiteReport


def parse_junit_xml(junit_path: Path, suite_name: str = "Test Suite") -> Optional[TestSuiteReport]:
    """
    Parses a JUnit XML file (generated via `pytest --junitxml=junit.xml`).
    Extracts individual test cases, durations, failure messages, and stack traces.
    """
    if not junit_path.exists():
        return None

    try:
        tree = ET.parse(junit_path)
        root = tree.getroot()

        # Root could be <testsuite> or <testsuites>
        if root.tag == "testsuite":
            suite_elem = root
        else:
            suite_elem = root.find("testsuite")
            if suite_elem is None:
                suite_elem = root

        total = int(suite_elem.attrib.get("tests", 0))
        failures = int(suite_elem.attrib.get("failures", 0))
        errors = int(suite_elem.attrib.get("errors", 0))
        skipped = int(suite_elem.attrib.get("skipped", 0))
        time_sec = float(suite_elem.attrib.get("time", 0.0))
        duration_ms = round(time_sec * 1000.0, 2)
        passed = max(0, total - (failures + errors + skipped))

        overall_status = TestStatus.PASSED
        if failures > 0:
            overall_status = TestStatus.FAILED
        elif errors > 0:
            overall_status = TestStatus.ERROR
        elif total == 0:
            overall_status = TestStatus.SKIPPED

        test_cases: List[TestCaseResult] = []
        for case in suite_elem.findall("testcase"):
            classname = case.attrib.get("classname", "")
            name = case.attrib.get("name", "")
            file_attr = case.attrib.get("file", "")
            case_time = float(case.attrib.get("time", 0.0))
            case_duration_ms = round(case_time * 1000.0, 2)
            case_id = f"{classname}::{name}"

            status = TestStatus.PASSED
            error_message = None
            stack_trace = None

            failure_node = case.find("failure")
            error_node = case.find("error")
            skipped_node = case.find("skipped")

            if failure_node is not None:
                status = TestStatus.FAILED
                error_message = failure_node.attrib.get("message", "AssertionError")
                stack_trace = failure_node.text
            elif error_node is not None:
                status = TestStatus.ERROR
                error_message = error_node.attrib.get("message", "Error")
                stack_trace = error_node.text
            elif skipped_node is not None:
                status = TestStatus.SKIPPED
                error_message = skipped_node.attrib.get("message", "Skipped")

            test_cases.append(
                TestCaseResult(
                    test_id=case_id,
                    name=name,
                    file_path=file_attr or classname,
                    status=status,
                    duration_ms=case_duration_ms,
                    error_message=error_message,
                    stack_trace=stack_trace,
                )
            )

        return TestSuiteReport(
            suite_name=suite_name,
            status=overall_status,
            total_tests=total,
            passed_tests=passed,
            failed_tests=failures,
            skipped_tests=skipped,
            error_tests=errors,
            duration_ms=duration_ms,
            test_cases=test_cases,
        )

    except Exception:
        return None
