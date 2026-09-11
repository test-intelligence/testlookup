// The scopes the server enforces (re-audit N20, QA-R3-11, QA round 4):
// stream:write for live streaming and result ingest, project:admin for project
// administration. A key with no scopes keeps full access. Labels the server did
// not check (test:read, report:write, ...) were offered here and minted keys
// that read as limited while they were not; they return when enforced.
export const AVAILABLE_SCOPES = ['stream:write', 'project:admin']
