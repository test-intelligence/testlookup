"""OIDC identity providers on ``sso_configurations`` (W20 ADM-08b).

* ``idp_entity_id``, ``idp_sso_url``, ``idp_certificate``, ``sp_entity_id`` and
  ``sp_acs_url`` become NULL-able: an OIDC row has none of them. The CHECK
  ``ck_sso_config_fields_by_provider`` keeps a SAML row complete (all five set)
  and an OIDC row usable (issuer + client id set), so relaxing NOT NULL cannot
  leave an incomplete SAML configuration behind.
* ``oidc_issuer``, ``oidc_client_id``, ``oidc_scopes``, ``oidc_groups_claim``:
  the OIDC settings. The client secret is NOT a column — it lives in
  ``secret_refs`` (scope ``sso_oidc``, key = the config id).

Every existing row is SAML with the five fields set, so the CHECK holds without
a backfill. The downgrade refuses while an OIDC row exists (restoring NOT NULL
would fail on it anyway) — delete those configurations first.

Revision ID: 0237
Revises: 0236
"""
from alembic import op
import sqlalchemy as sa

revision = "0237"
down_revision = "0236"
branch_labels = None
depends_on = None

_SAML_COLUMNS = (
    ("idp_entity_id", sa.String(1000)),
    ("idp_sso_url", sa.String(2000)),
    ("idp_certificate", sa.Text()),
    ("sp_entity_id", sa.String(1000)),
    ("sp_acs_url", sa.String(2000)),
)

_CHECK = (
    "(provider_type <> 'SAML' OR (idp_entity_id IS NOT NULL AND idp_sso_url IS NOT NULL"
    " AND idp_certificate IS NOT NULL AND sp_entity_id IS NOT NULL AND sp_acs_url IS NOT NULL))"
    " AND (provider_type <> 'OIDC' OR (oidc_issuer IS NOT NULL AND oidc_client_id IS NOT NULL))"
)


def upgrade() -> None:
    op.add_column("sso_configurations", sa.Column("oidc_issuer", sa.String(1000), nullable=True))
    op.add_column("sso_configurations", sa.Column("oidc_client_id", sa.String(255), nullable=True))
    op.add_column("sso_configurations", sa.Column("oidc_scopes", sa.String(500), nullable=True))
    op.add_column("sso_configurations", sa.Column("oidc_groups_claim", sa.String(255), nullable=True))
    for name, type_ in _SAML_COLUMNS:
        op.alter_column("sso_configurations", name, existing_type=type_, nullable=True)
    op.create_check_constraint("ck_sso_config_fields_by_provider", "sso_configurations", _CHECK)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT count(*) FROM sso_configurations WHERE provider_type = 'OIDC'")).scalar():
        raise RuntimeError("0237 downgrade: delete the OIDC SSO configurations first")
    op.drop_constraint("ck_sso_config_fields_by_provider", "sso_configurations", type_="check")
    for name, type_ in _SAML_COLUMNS:
        op.alter_column("sso_configurations", name, existing_type=type_, nullable=False)
    for name in ("oidc_groups_claim", "oidc_scopes", "oidc_client_id", "oidc_issuer"):
        op.drop_column("sso_configurations", name)
