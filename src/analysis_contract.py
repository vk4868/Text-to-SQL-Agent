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

#: The sections that assert something about the returned data, and are
#: therefore the ones worth checking figures in.
#:
#: LIMITATIONS and SUGGESTED FOLLOW-UP are deliberately excluded: the prompt
#: asks them to talk about data that is NOT in the result — "compare against
#: 2024", "retrieve products ranked 6 through 15". Numbers there are
#: proposals, not claims, and checking them reports a category error as a
#: hallucination.
CLAIM_BEARING_SECTIONS = (
    "DIRECT ANSWER",
    "KEY INSIGHTS",
    "SUPPORTING NUMBERS",
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


def split_sections(analysis: str) -> dict[str, str]:
    """Split an analysis into its named sections.

    Text before the first recognised header is returned under "" so that a
    model which omits headers entirely is still checked rather than skipped.
    """

    positions: list[tuple[int, str]] = []

    for section in REQUIRED_SECTIONS:
        match = _section_pattern(section).search(analysis)
        if match:
            positions.append((match.start(), section))

    positions.sort()

    if not positions:
        return {"": analysis}

    sections: dict[str, str] = {}

    preamble = analysis[: positions[0][0]].strip()
    if preamble:
        sections[""] = preamble

    for index, (start, name) in enumerate(positions):
        end = (
            positions[index + 1][0]
            if index + 1 < len(positions)
            else len(analysis)
        )
        sections[name] = analysis[start:end]

    return sections


def claim_bearing_text(analysis: str) -> str:
    """Return only the parts of an analysis that assert something."""

    sections = split_sections(analysis)

    if list(sections) == [""]:
        # No recognisable structure, so check everything.
        return analysis

    return "\n".join(
        text
        for name, text in sections.items()
        if name in CLAIM_BEARING_SECTIONS or name == ""
    )
