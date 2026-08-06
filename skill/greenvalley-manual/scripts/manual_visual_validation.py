#!/usr/bin/env python3
"""Create and maintain risk-driven manual PDF visual-review state."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

VISUAL_RULES = {
    "GVMD-HTML-BLANK-LINE": ["emphasis_visible", "links_work"],
    "GVMD-LIST-COMPAT": ["ordered_list_valid"],
    "GVMD-PARAGRAPH-LONG": ["paragraph_wrap_valid"],
    "GVMD-IMAGE-WIDTH": ["images_visible", "image_pagination_valid"],
    "GVMD-IMAGE-DIMENSION": ["images_visible", "image_pagination_valid"],
    "GVMD-TABLE-VISUAL": ["table_layout_valid"],
    "GVMD-TABLE-SYNTAX": ["table_layout_valid"],
}


def digest(paths: list[Path]) -> str:
    value = hashlib.sha256()
    for path in sorted(paths):
        value.update(path.as_posix().encode("utf-8"))
        if path.exists() and path.is_file():
            value.update(path.read_bytes())
    return value.hexdigest()


def create(report: Path, output: Path, pdf: Path | None) -> int:
    data = json.loads(report.read_text(encoding="utf-8"))
    grouped: dict[str, dict] = {}
    for finding in data.get("findings", []):
        checks = VISUAL_RULES.get(finding.get("rule_id"))
        if not checks or finding.get("suppressed") or not finding.get("file"):
            continue
        case = grouped.setdefault(finding["file"], {"reasons": [], "checks": set(), "lines": set()})
        case["reasons"].append(finding["rule_id"])
        case["checks"].update(checks)
        case["lines"].add(finding.get("line", 0))
    cases = []
    for index, (source_file, item) in enumerate(sorted(grouped.items()), 1):
        cases.append({"id": f"VV-{index:03d}", "source_file": source_file, "source_lines": sorted(item["lines"]), "rendered_pages": [], "reasons": sorted(set(item["reasons"])), "checks": sorted(item["checks"]), "review": {"status": "pending", "reviewer": None, "note": None}})
    inputs = [report] + ([pdf] if pdf else [])
    state = {"schema_version": 1, "status": "pending" if cases else "not_requested", "input_digest": digest(inputs), "report": report.name, "pdf": pdf.name if pdf else None, "cases": cases}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"CREATED: {len(cases)} visual review case(s) in {output}")
    return 0


def review(state_path: Path, case_id: str, status: str, reviewer: str, note: str | None) -> int:
    data = json.loads(state_path.read_text(encoding="utf-8"))
    for case in data.get("cases", []):
        if case["id"] == case_id:
            case["review"] = {"status": status, "reviewer": reviewer, "note": note}
            break
    else:
        raise SystemExit(f"unknown case: {case_id}")
    statuses = {case["review"]["status"] for case in data.get("cases", [])}
    data["status"] = "needs-fix" if "needs-fix" in statuses else ("approved" if statuses <= {"approved", "not-applicable"} else "pending")
    state_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"UPDATED: {case_id} -> {status}; overall {data['status']}")
    return 0


def verify(state_path: Path, report: Path, pdf: Path | None) -> int:
    data = json.loads(state_path.read_text(encoding="utf-8"))
    if digest([report] + ([pdf] if pdf else [])) != data.get("input_digest"):
        data["status"] = "stale"
        state_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("STALE: visual review inputs changed")
        return 1
    print(f"VALID: visual review status is {data.get('status')}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    create_parser = sub.add_parser("create")
    create_parser.add_argument("report", type=Path)
    create_parser.add_argument("output", type=Path)
    create_parser.add_argument("--pdf", type=Path)
    review_parser = sub.add_parser("review")
    review_parser.add_argument("state", type=Path)
    review_parser.add_argument("case_id")
    review_parser.add_argument("status", choices=("approved", "needs-fix", "not-applicable"))
    review_parser.add_argument("--reviewer", required=True)
    review_parser.add_argument("--note")
    verify_parser = sub.add_parser("verify")
    verify_parser.add_argument("state", type=Path)
    verify_parser.add_argument("report", type=Path)
    verify_parser.add_argument("--pdf", type=Path)
    args = parser.parse_args()
    if args.command == "create":
        return create(args.report, args.output, args.pdf)
    if args.command == "review":
        return review(args.state, args.case_id, args.status, args.reviewer, args.note)
    return verify(args.state, args.report, args.pdf)


if __name__ == "__main__":
    sys.exit(main())
