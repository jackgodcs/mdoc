# Workflow

## Phases

1. Discover or initialize the workspace.
2. Validate the product profile and repository binding.
3. Create or resume a task using one of: create_module, add_feature, update_feature, add_locale.
4. Propose and approve structure.
5. Propose and approve screenshot tasks.
6. Generate the screenshot-assistant manifest and launcher, then capture required screenshots in the GUI. Capture completion requires an eligible target PNG or an explicit waived/not-applicable decision; retained exception PNGs are reference-only.
7. Generate manuals in staging using only manifest `manual_assets`; never select images from path existence alone.
8. Apply screenshot eligibility only to the task's fixed staging directory, validate that staging rejects remaining ineligible references, and create a publish plan. Before publishing, either complete independent screenshot review or record the user's aggregate visual acceptance of the current capture fingerprint. Existing formal references that become ineligible are publish conflicts and require explicit confirmation before removal.
9. Apply minimal changes to the formal manual repository.
10. If requested or configured, run the optional Quality Gate. In advisory mode, report findings without blocking publishing. In required mode, complete the configured profile and components before publishing.
11. Validate the ordinary task invariants again and produce reports.
12. Stop at ready_for_review until the user accepts.

## Confirmation gates

Workspace configuration is confirmed only when new or invalid. Structure, screenshot plan, and screenshot acceptance are the normal task gates. Configuration-driven review or automation may skip conversational repetition when approved state is internally consistent.

Quality Gate is not a normal confirmation gate. Missing validation configuration preserves the existing workflow. Run it explicitly for an audit, automatically only when configured, and make it a publishing prerequisite only when `validation.mode` is `required` and `publish_policy.required_before_publish` is true.

## Task states

Use draft, generated, validation_failed, ready_for_review, and accepted. Do not set accepted without explicit user confirmation.

## Source control and installation

The GitHub repository jackgodcs/mdoc on main is the single source of truth. Use the local checkout as the maintenance workspace and pull with --ff-only before editing. Make skill changes under skill/mdoc, validate them there, commit meaningful milestones, and push to main.

Treat $CODEX_HOME/skills/mdoc as an installed runtime copy. Do not use it as the durable maintenance source. After GitHub-backed changes are validated and committed, synchronize skill/mdoc from the checkout into the installed directory. If the installed copy has uncommitted divergence, reconcile it back into the checkout and GitHub before replacing it.

Git tracks the skill source and portable process assets only. Do not track formal manual Markdown, formal screenshots, staging manuals, source packages, local repository bindings, *.local.yaml, screenshot-assistant local files, captures, credentials, or other machine-specific paths.
