#!/usr/bin/env python3
"""Zero-dependency validation for GreenValley manual workspaces."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


ENUMS = {
    "operation": {"create_module", "add_feature", "update_feature", "add_locale"},
    "interaction": {"guided", "review", "automation"},
    "capture_mode": {"manual", "assisted", "automated"},
    "task_status": {"draft", "generated", "validation_failed", "ready_for_review", "accepted"},
    "template": {"module-index", "category-index", "operation", "interface", "workflow", "concept", "faq", "api-reference"},
    "snapshot": {"none", "selected", "full"},
    "validation_mode": {"disabled", "advisory", "required", "inherit"},
    "validation_profile": {"quick", "full", "release"},
}
VALIDATION_COMPONENTS = {"static", "html_build", "pdf_build", "visual_review"}


def text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def scalar(source: str, key: str):
    match = re.search(rf"(?m)^\s*{re.escape(key)}:\s*([^#\n]+?)\s*$", source)
    if not match:
        return None
    value = match.group(1).strip().strip("\"'")
    return value


def nested_section(source: str, key: str) -> str:
    lines = source.splitlines()
    start = next((index for index, line in enumerate(lines) if line.strip() == f"{key}:"), None)
    if start is None:
        return ""
    base = len(lines[start]) - len(lines[start].lstrip())
    end = next((index for index in range(start + 1, len(lines)) if lines[index].strip() and len(lines[index]) - len(lines[index].lstrip()) <= base), len(lines))
    return "\n".join(lines[start + 1:end])


def top_level_section(source: str, key: str) -> str:
    lines = source.splitlines()
    start = next((index for index, line in enumerate(lines) if line == f"{key}:"), None)
    if start is None:
        return ""
    end = next((index for index in range(start + 1, len(lines)) if lines[index] and not lines[index][0].isspace()), len(lines))
    return "\n".join(lines[start + 1:end])


def list_item_values(source: str, key: str) -> list[str]:
    return re.findall(rf"(?m)^\s*-\s+{re.escape(key)}:\s*([^#\n]+?)\s*$", source)


def inline_list_values(source: str, key: str) -> list[str]:
    values = []
    for match in re.finditer(rf"(?m)^\s*{re.escape(key)}:\s*\[([^]]*)\]\s*$", source):
        values.extend(item.strip().strip("\"'") for item in match.group(1).split(",") if item.strip())
    return values


def require_keys(path: Path, keys: list[str], errors: list[str]):
    source = text(path)
    for key in keys:
        if not re.search(rf"(?m)^\s*{re.escape(key)}:\s*", source):
            errors.append(f"{path}: missing key {key}")


def duplicates(values: list[str]) -> set[str]:
    seen, repeated = set(), set()
    for value in values:
        if value in seen:
            repeated.add(value)
        seen.add(value)
    return repeated


def validate_workspace(root: Path, errors: list[str]):
    workspace = root / "workspace.yaml"
    profile = root / "product-profile.yaml"
    if not workspace.exists():
        errors.append(f"{root}: workspace.yaml is missing")
    else:
        require_keys(workspace, ["schema_version", "workspace", "product", "repository", "manual", "tasks"], errors)
        source = text(workspace)
        for key, allowed in [("interaction_mode", ENUMS["interaction"]), ("screenshot_capture_mode", ENUMS["capture_mode"])]:
            value = scalar(source, key)
            if value and value not in allowed:
                errors.append(f"{workspace}: invalid {key}: {value}")
        value = scalar(source, "default_profile")
        if value and value not in ENUMS["validation_profile"]:
            errors.append(f"{workspace}: invalid validation default_profile: {value}")
    if not profile.exists():
        errors.append(f"{root}: product-profile.yaml is missing")
    else:
        require_keys(profile, ["schema_version", "product", "manual_layout", "locales", "policies"], errors)
        source = text(profile)
        if scalar(source, "preserve_existing_content") != "true":
            errors.append(f"{profile}: preserve_existing_content must be true")
        if scalar(source, "deletion_requires_confirmation") != "true":
            errors.append(f"{profile}: deletion_requires_confirmation must be true")
        validation_source = top_level_section(source, "validation")
        mode = scalar(validation_source, "mode")
        if mode and mode not in ENUMS["validation_mode"]:
            errors.append(f"{profile}: invalid validation mode: {mode}")
        profile_name = scalar(validation_source, "default_profile")
        if profile_name and profile_name not in ENUMS["validation_profile"]:
            errors.append(f"{profile}: invalid validation default_profile: {profile_name}")
        publish_source = nested_section(validation_source, "publish_policy")
        for component in inline_list_values(publish_source, "required_components"):
            if component not in VALIDATION_COMPONENTS:
                errors.append(f"{profile}: invalid validation component: {component}")


def validate_task(task_dir: Path, errors: list[str], warnings: list[str]):
    task = task_dir / "task.yaml"
    structure = task_dir / "structure.yaml"
    screenshots = task_dir / "screenshots.yaml"
    if not task.exists():
        errors.append(f"{task_dir}: task.yaml is missing")
        return
    require_keys(task, ["schema_version", "task", "operation", "interaction", "capture", "target", "module", "scope"], errors)
    task_source = text(task)
    operation = scalar(task_source, "operation")
    if operation not in ENUMS["operation"]:
        errors.append(f"{task}: invalid operation: {operation}")
    modes = re.findall(r"(?m)^\s*mode:\s*([^#\n]+?)\s*$", task_source)
    if len(modes) >= 1 and modes[0].strip() not in ENUMS["interaction"]:
        errors.append(f"{task}: invalid interaction mode: {modes[0]}")
    if len(modes) >= 2 and modes[1].strip() not in ENUMS["capture_mode"]:
        errors.append(f"{task}: invalid capture mode: {modes[1]}")
    page_ids, shot_ids = [], []
    if structure.exists():
        source = text(structure)
        page_ids = list_item_values(source, "id")
        page_files = list_item_values(source, "file")
        templates = list_item_values(source, "template")
        for value in duplicates(page_ids): errors.append(f"{structure}: duplicate page id: {value}")
        for value in duplicates(page_files): errors.append(f"{structure}: duplicate page file: {value}")
        for value in templates:
            if value not in ENUMS["template"]: errors.append(f"{structure}: invalid page template: {value}")
    else:
        errors.append(f"{task_dir}: structure.yaml is missing")
    if screenshots.exists():
        source = text(screenshots)
        shot_ids = list_item_values(source, "id")
        for value in duplicates(shot_ids): errors.append(f"{screenshots}: duplicate screenshot id: {value}")
        if re.search(r"(?m)^locales:\s*$", source):
            errors.append(f"{screenshots}: malformed top-level locales block")
        if re.search(r"(?m)^review:\s*$", source):
            errors.append(f"{screenshots}: malformed top-level review block")
        capture_statuses = re.findall(r"(?ms)^    capture:\s*\n      status:\s*([^#\n]+)", source)
        screenshot_count = len(shot_ids)
        capture_count = len(re.findall(r"(?m)^    capture:\s*$", source))
        review_count = len(re.findall(r"(?m)^    review:\s*$", source))
        if capture_count != screenshot_count:
            errors.append(f"{screenshots}: expected {screenshot_count} capture blocks, found {capture_count}")
        if review_count != screenshot_count:
            errors.append(f"{screenshots}: expected {screenshot_count} review blocks, found {review_count}")
        allowed_capture = {"pending", "captured", "needs-retake", "approved", "not-applicable", "waived", "blocked"}
        for value in capture_statuses:
            if value.strip() not in allowed_capture: errors.append(f"{screenshots}: invalid capture status: {value.strip()}")
    else:
        errors.append(f"{task_dir}: screenshots.yaml is missing")
    if structure.exists() and screenshots.exists():
        structure_source, screenshot_source = text(structure), text(screenshots)
        referenced_shots = inline_list_values(structure_source, "screenshot_ids")
        for shot_id in referenced_shots:
            if shot_id not in set(shot_ids): errors.append(f"{structure}: missing screenshot id: {shot_id}")
        referenced_pages = re.findall(r"(?m)^\s{6}-\s+([a-z0-9-]+)\s*$", screenshot_source)
        for page_id in referenced_pages:
            if page_id not in set(page_ids): errors.append(f"{screenshots}: missing page id: {page_id}")
    publish = task_dir / "publish-plan.yaml"
    if publish.exists() and re.search(r"(?ms)^\s*delete:\s*\n\s*-\s+", text(publish)):
        warnings.append(f"{publish}: deletion proposals require separate explicit confirmation")
    validation_profile = scalar(task_source, "profile")
    if validation_profile and validation_profile not in ENUMS["validation_profile"]:
        errors.append(f"{task}: invalid validation profile: {validation_profile}")
    validation_source = top_level_section(task_source, "validation")
    task_mode = scalar(validation_source, "mode")
    if task_mode and task_mode not in {"inherit", "advisory", "required"}:
        errors.append(f"{task}: invalid validation mode: {task_mode}")
    for component in inline_list_values(validation_source, "required_components"):
        if component not in VALIDATION_COMPONENTS:
            errors.append(f"{task}: invalid validation component: {component}")


def validate_validation_policy(root: Path, errors: list[str]):
    profile = root / "product-profile.yaml"
    if not profile.exists():
        return
    validation = top_level_section(text(profile), "validation")
    mode = scalar(validation, "mode") or "advisory"
    publish = nested_section(validation, "publish_policy")
    required = scalar(publish, "required_before_publish") == "true"
    if required and mode != "required":
        errors.append(f"{profile}: publish_policy.required_before_publish requires validation.mode: required")
    ranks = {"disabled": 0, "advisory": 1, "required": 2}
    profile_ranks = {"quick": 0, "full": 1, "release": 2}
    base_profile = scalar(validation, "default_profile") or "full"
    required_profile = scalar(publish, "required_profile") or base_profile
    if required and profile_ranks.get(required_profile, -1) < profile_ranks.get(base_profile, 1):
        errors.append(f"{profile}: publish required_profile cannot be weaker than validation default_profile")
    task_root = root / "manual-tasks"
    if task_root.exists():
        for task in task_root.glob("*/task.yaml"):
            task_validation = top_level_section(text(task), "validation")
            task_mode = scalar(task_validation, "mode") or "inherit"
            if task_mode != "inherit" and ranks.get(task_mode, 99) < ranks.get(mode, 1):
                errors.append(f"{task}: task validation mode cannot weaken product mode {mode}")
            task_profile = scalar(task_validation, "profile") or base_profile
            if profile_ranks.get(task_profile, -1) < profile_ranks.get(base_profile, 1):
                errors.append(f"{task}: task validation profile cannot weaken product profile {base_profile}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("workspace", type=Path)
    args = parser.parse_args()
    root = args.workspace.resolve()
    errors, warnings = [], []
    validate_workspace(root, errors)
    validate_validation_policy(root, errors)
    task_root = root / "manual-tasks"
    if task_root.exists():
        for task_dir in sorted(path for path in task_root.iterdir() if path.is_dir()):
            validate_task(task_dir, errors, warnings)
    for warning in warnings: print(f"WARNING: {warning}")
    for error in errors: print(f"ERROR: {error}")
    if errors:
        print(f"FAILED: {len(errors)} error(s), {len(warnings)} warning(s)")
        return 1
    print(f"PASSED: 0 errors, {len(warnings)} warning(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
