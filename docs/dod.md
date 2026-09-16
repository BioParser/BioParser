# Definition of Done

A task is done when all of the below are true.

- The task's agreed acceptance criteria are met. If the scope changes, project owners revise or split the task and record the decision.
- Tests cover the new behaviour, including error paths added in the change.
- The change has been tested manually where automated tests are not practical.
- Errors shown to users are clear and actionable.
- Work is on `main` via a PR with one non-author approval.
- Required GitHub CI checks pass.
- When a staging environment exists, the change is deployed there.

### Coding Standards

- Code follows the project's established formatting, naming, and architectural conventions.
- Configuration, credentials, and other secrets are not hard-coded or committed.
- Duplicated logic is avoided where a suitable existing abstraction exists.
- Error handling does not silently ignore failures without a documented reason.
- Logging does not expose sensitive information.

### When applicable

- For dependency changes, new dependencies have a clear purpose and updates do not introduce known compatibility or security issues.
- For HTTP API changes, the API’s Swagger UI (`/docs`) is up to date with the current endpoints, parameters, and responses.
- For changes that affect existing API clients or stored data, backward compatibility and migration needs are considered.
- For changes that add user input, validation and invalid-input behaviour are covered.
- User-facing documentation is updated when using or calling the product has changed.
