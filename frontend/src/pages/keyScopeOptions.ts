// The first two are the scopes the server enforces: stream:write for live
// streaming, project:admin for the project-administration routes a project-
// bound key may use (re-audit N20, QA-R3-11). A key with no scopes keeps full
// access. The rest are labels the server does not check yet.
export const AVAILABLE_SCOPES = ['stream:write', 'project:admin', 'test:read', 'test:write', 'report:read', 'report:write', 'admin:read']
