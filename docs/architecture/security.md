# Security and policy boundaries

[Documentation home](../README.md)

This page describes implemented boundaries, not a penetration-test certification. [SECURITY.md](../../SECURITY.md) covers vulnerability reporting and [THREAT_MODEL.md](../../THREAT_MODEL.md) contains additional threat analysis.

## Identity and authorization

Most API domains inherit `get_current_user_or_api_key` in [bootstrap](../../backend/app/bootstrap.py). Resource guards resolve the owning run/test/project on the server; role and membership checks are additional constraints. A globally privileged role and a project-bound key are not interchangeable. Key scopes with a non-empty list restrict actions; legacy empty scopes have broader semantics documented in [deps](../../backend/app/core/deps.py). Human governance actions can require an interactive JWT rather than a machine credential.

Login is form-encoded and can return an access/refresh pair, MFA challenge or required-enrollment response. Challenge tokens are not access tokens. Refresh rotation/revocation, forced password reset and MFA policy have their own failure paths. SAML and SCIM are implemented but disabled by default settings until configured; SCIM bearer credentials are distinct from normal JWTs. [Auth API](../reference/api/authentication.md), [SSO](../reference/api/sso-saml.md), [SCIM](../reference/api/scim-2-0.md).

“Public router” includes alternate credential protocols: signed webhooks, share tokens, SCIM auth and single-use invocation stream tickets. WebSocket auth happens after connection with membership validation; the live event POST path applies project-key rules. Browser route visibility is a convenience, not a security boundary.

## Input and output boundaries

Uploads use bounded reads and archive entry/count/ratio/expanded-size checks. XML parsers use safe parsing dependencies. Pydantic and service validation restrict typed inputs. URL/connector safety applies SSRF and egress controls. Evidence, prompts, reports and logs have sanitization/redaction services; the presence of a redactor is not proof that every possible data type or secret is removed. Review sensitive integration data in a controlled environment before production rollout.

Reports carry AI/review metadata. `REVIEW_GATE_ENFORCED` defaults false: distribution decisions are observed/audited instead of generally blocking. When enabled, accepted or non-AI reports can pass; permitted pending drafts require watermark/audit; rejected/superseded reports are not draft exceptions. QA-lead interactive draft overrides still require handler authorization. [report policy](../../backend/app/services/report_distribution_policy.py).

## Offline and external connectivity

`AI_OFFLINE_MODE=true` constrains AI provider policy and local transport. [llm_egress](../../backend/app/services/llm_egress.py) resolves and pins validated non-routable addresses at connection time to avoid a DNS rebind between checking and connecting. This can include private-network services; “local-first” does not mean packets never leave the process or machine. Local embedding guards avoid unplanned weight downloads. Notification egress has separate allowed-host/recipient-domain controls, and other connectors have their own gates.

A sealed installation additionally requires staged dependencies/models, controlled DNS/routing/firewall or network policies, and verification of every enabled connector. Do not interpret one environment flag as a host-wide firewall. [Air-gap operations](../operations/deployment.md).

## Deployment protections

Production/staging startup checks reject critical unsafe secrets/settings. Compose defaults to production with quick login disabled unless a generated development environment overrides them. Keep JWT/app/webhook/storage/database secrets out of Git; align database URLs with credentials. Only trust forwarded headers from configured proxy addresses. Preserve TLS validation and use trusted CA injection for corporate interception instead of disabling verification.

Health endpoints and metrics have operational exposure considerations; protect them at ingress as appropriate to the deployment. Auth IP rate limiting is process-local, while account lockout is durable and global. Project ingest quotas use Redis. Capacity/admission failures and auth revocation failures do not necessarily use the same fail-open/closed policy; inspect the relevant service rather than applying a universal assumption.
