# Workflow

## Phases

1. Discover or initialize the workspace.
2. Validate the product profile and repository binding.
3. Create or resume a task using one of: create_module, add_feature, update_feature, add_locale.
4. Propose and approve structure.
5. Propose and approve screenshot tasks.
6. Generate locale workbooks and capture required screenshots. Capture completion is target-PNG existence only.
7. Generate manuals in staging.
8. Validate staging and create a publish plan. Before publishing, either complete independent screenshot review or record the user's aggregate visual acceptance of the current capture fingerprint.
9. Apply minimal changes to the formal manual repository.
10. Validate again and produce reports.
11. Stop at ready_for_review until the user accepts.

## Confirmation gates

Workspace configuration is confirmed only when new or invalid. Structure, screenshot plan, and screenshot acceptance are the normal task gates. Configuration-driven review or automation may skip conversational repetition when approved state is internally consistent.

## Task states

Use draft, generated, validation_failed, ready_for_review, and accepted. Do not set accepted without explicit user confirmation.

## Local Git

Local Git tracks the skill source and process assets only. Do not track formal manual Markdown, formal screenshots, staging manuals, source packages, or local path files. Commit meaningful process milestones; never configure or push a remote unless requested.
