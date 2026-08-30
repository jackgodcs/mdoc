# Publishing Transactions

Formal manuals are changed only by mdoc publishing transactions. Agents write task staging; the transaction copies staged files into formal book locations after Quality Gate passes.

## Automatic Publishing

After task Quality Gate passes, mdoc creates a derived publish operation report and executes ordinary create/update writes automatically. It pauses when it sees deletion, target conflict, baseline drift, out-of-scope staging, missing source files, or a failed post-publish check.

Deletion requires an exact target approval:

```powershell
mdoc task approve-deletion --workspace <manual-repository-root> --task <task-id> --target <locale/path>
```

When a declared `update` target changed after the task baseline was recorded, publishing pauses. If the staged output has already been manually verified as the intended final output, a coordinator may explicitly rebase only those `target_changed` conflicts and continue the ordinary transaction:

```powershell
mdoc task approve-publish-conflict --workspace <manual-repository-root> --task <task-id> --confirm
```

The approval records the affected targets and previous baselines, clears the derived Quality Gate result, and reruns normal verification and publishing. It cannot approve create-target, deletion, or other conflict kinds.

## Transaction Steps

1. Recheck the frozen manifest and baseline hashes under the book publish lock.
2. Record the transaction as `started`.
3. Back up every existing target before changing it.
4. Atomically replace, create, or delete each declared target.
5. Run published-task Quality Gate.
6. Mark the transaction `committed`, or roll back every touched target and mark it `rolled_back`.

Interrupted `started` transactions are recovered before the next task action. Recovery rolls back only files recorded in the transaction.

## Revisions

Publishing creates a task revision and moves the task to `ready_for_review`. The user may request output revision before final acceptance; `accepted` and `cancelled` are terminal and cannot be reopened.
