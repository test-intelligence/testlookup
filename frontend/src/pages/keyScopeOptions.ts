// The scopes the server enforces (backend/app/core/deps.py; re-audit N20,
// QA-R3-11, QA round 4, N31/N32): stream:write for live streaming, project:write
// for writes, project:admin for project administration (it implies
// project:write). A key with no scopes keeps full access. The server refuses any
// other scope name, so this list must match its vocabulary exactly.
export const AVAILABLE_SCOPES = ['stream:write', 'project:write', 'project:admin']
