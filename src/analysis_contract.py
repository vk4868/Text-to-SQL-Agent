"""Check that an analysis has the shape the prompt asked for.

The result-analysis prompt mandates five sections. A local model mostly obeys
but occasionally drifts, and a portfolio demo should not die on formatting —
so violations are reported alongside the analysis rather than raised.
"""

import re

#: The sections build_result_analysis_prompt requires, in order.
REQUIRED_SECTIONS = (
    "DIRECT ANSWER",
    "KEY INSIGHTS",
    "SUPPORTING NUMBERS",
    "LIMITATIONS",
    "SUGGESTED FOLLOW-UP",
)

# Markdown tables are forbidden by the prompt; they render badly in a terminal
# and in most of the places this text ends up.
_MARKDOWN_TABLE = re.compile(r"^\s*\|.*\|\s*$", re.MULTILINE)


def _section_pattern(section: str) -> re.Pattern[str]:
    """Match a section header at the start of a line, colon optional."""

    return re.compile(
        rf"^\s*{re.escape(section)}\s*:?\s*$",
        re.MULTILINE | re.IGNORECASE,
    )


def find_missing_sections(analysis: str) -> list[str]:
    """Return the required sections that are absent."""

    return [
        section
        for section in REQUIRED_SECTIONS
        if not _section_pattern(section).search(analysis)
    ]


def validate_analysis_structure(analysis: str) -> list[str]:
    """Return a list of contract violations; empty means well-formed."""

    violations: list[str] = []

    if not analysis.strip():
        return ["The analysis is empty."]

    for section in find_missing_sections(analysis):
        violations.append(f"Missing required section: {section}")

    if _MARKDOWN_TABLE.search(analysis):
        violations.append(
            "The analysis contains a Markdown table, which the prompt "
            "forbids."
        )

    return violations
