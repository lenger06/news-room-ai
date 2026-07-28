"""
Read-only aggregation over tools/agent_outcomes.py's outcome log — Phase 7.1
of SELF_IMPROVEMENT_ROADMAP.md. Not an agent, not scheduled, not autonomous:
run it on request and read the output, the same way this roadmap itself gets
updated by hand. This is the diagnostic step that Phase 7.4's human-approved
prompt tuning reads from — it does not itself propose or apply any change.
"""

import sys
from collections import defaultdict
from pathlib import Path

project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from tools.agent_outcomes import load_recent

_FACT_CHECK_HOLD_VERDICTS = {"HOLD FOR CORRECTIONS"}
_COMPLIANCE_HOLD_VERDICTS = {"HOLD FOR REVIEW"}


def _rate(numerator: int, denominator: int) -> float:
    return round(100.0 * numerator / denominator, 1) if denominator else 0.0


def _section_by(entries: list[dict], key: str, title: str) -> list[str]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for e in entries:
        label = e.get(key) or "(none)"
        grouped[label].append(e)

    lines = [f"## {title}"]
    for label, rows in sorted(grouped.items(), key=lambda kv: -len(kv[1])):
        fc_holds = sum(1 for e in rows if e.get("fact_check_verdict") in _FACT_CHECK_HOLD_VERDICTS)
        comp_holds = sum(1 for e in rows if e.get("compliance_verdict") in _COMPLIANCE_HOLD_VERDICTS)
        lines.append(
            f"- {label}: {len(rows)} production(s) — "
            f"fact-check hold rate {_rate(fc_holds, len(rows))}%, "
            f"compliance hold rate {_rate(comp_holds, len(rows))}%"
        )
    lines.append("")
    return lines


def generate_report(days: float = 7.0) -> str:
    """Build a human-readable summary of production outcomes over the trailing window."""
    entries = load_recent(days)
    if not entries:
        return f"No production outcomes recorded in the last {days:g} days."

    published = sum(1 for e in entries if e.get("published"))
    succeeded = sum(1 for e in entries if e.get("succeeded"))
    fc_holds = sum(1 for e in entries if e.get("fact_check_verdict") in _FACT_CHECK_HOLD_VERDICTS)
    comp_holds = sum(1 for e in entries if e.get("compliance_verdict") in _COMPLIANCE_HOLD_VERDICTS)
    avg_fc_attempts = round(sum(e.get("fact_check_attempts", 0) for e in entries) / len(entries), 2)
    avg_duration = round(sum(e.get("duration_seconds", 0) for e in entries) / len(entries))

    lines = [
        f"# Outcome Report — last {days:g} days ({len(entries)} production(s))",
        "",
        f"- Succeeded: {succeeded}/{len(entries)} ({_rate(succeeded, len(entries))}%)",
        f"- Published: {published}/{len(entries)} ({_rate(published, len(entries))}%)",
        f"- Fact-check hold rate: {fc_holds}/{len(entries)} ({_rate(fc_holds, len(entries))}%)",
        f"- Compliance hold rate: {comp_holds}/{len(entries)} ({_rate(comp_holds, len(entries))}%)",
        f"- Avg fact-check attempts: {avg_fc_attempts}",
        f"- Avg duration: {avg_duration}s",
        "",
    ]
    lines += _section_by(entries, "desk", "By desk")
    lines += _section_by(entries, "anchor_name", "By anchor")
    lines += _section_by(entries, "workflow", "By workflow")

    return "\n".join(lines).rstrip() + "\n"


if __name__ == "__main__":
    _days = float(sys.argv[1]) if len(sys.argv) > 1 else 7.0
    print(generate_report(_days))
