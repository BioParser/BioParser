# Collaboration practices

## Branching

- **`main` is protected.** Do not push to it directly. It stays production-ready.
- All work happens on feature branches created from up-to-date `main`.
- Branch names should be short and descriptive, for example:
  - `feature/fasta-parser`
  - `fix/empty-file-crash`
  - `docs/dod`
- Keep branches short-lived. Prefer small PRs over long-running branches.

## Pull requests

1. Open a PR from the feature branch into `main`.
2. Describe what changed and why. Link the issue if there is one.
3. **One review is required** before merge. The author cannot approve their own PR.
4. Address review comments or discuss them; then get approval.
5. Merge only when the [Definition of Done](dod.md) checklist is satisfied.
6. Prefer **squash merge** so `main` stays a linear, readable history.
7. Delete the feature branch after merge.

## Reviews

- Reviewer checks correctness, readability, tests, and that `main` will stay usable.
- Approval means: “I would merge this.”
- Nitpicks are optional; blocking comments are for bugs, missing tests, or DoD gaps.
- If you are stuck waiting for a review, ping the team.

## Issues and size of work

- One issue should fit in one PR.
- If a task grows, split it. Half-finished features do not go into `main`.

## Repository settings (GitHub)

On `main`, enable branch protection:

- Require a pull request before merging
- Require 1 approving review
- Do not allow bypassing (except in a true emergency, then fix `main` immediately)
- Require status checks to pass once CI exists
