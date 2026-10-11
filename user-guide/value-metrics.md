# Value Metrics (moved)

The engineer-hours-saved model behind **Reports → Value** moved into the in-app user guide: **Reports and outputs → Value metrics** (`/docs/reports` in the app, source [reports.md](../frontend/src/content/guide/reports.md)). It covers the three parts of the estimate, the honest-labelling caveats, the tunable assumptions and when the headline is hidden.

The same method is served by the API at `GET /api/v1/value-metrics/methodology`. QA leads and administrators can tune a project's assumptions on the page (**Model assumptions**) or with `PUT /api/v1/projects/{project_id}/value-metrics/assumptions`; any project member can read them with `GET` on the same path.
