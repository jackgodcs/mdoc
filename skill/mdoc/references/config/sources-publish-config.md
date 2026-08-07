# sources.yaml and publish-plan.yaml

sources.yaml registers evidence by semantic ID, type, purpose, trust, local path reference, and snapshot policy: none, selected, or full. selected is the default. Formal manuals must not link to task snapshots.

publish-plan.yaml lists add and update operations plus proposed deletes. Delete entries never execute without separate explicit confirmation. The plan must include scope checks for unrelated changes, overwrites, and deletions.

