"""Add derived consultant knowledge and finite control state.

Revision ID: e9a21c40b735
Revises: d8a14f90bc21
"""

from sqlalchemy import exists, select, table, text

from alembic import op

revision = "e9a21c40b735"
down_revision = "d8a14f90bc21"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Extension binaries must already exist on the authorized database host.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    statements = """
CREATE TABLE consultant_releases (id varchar(64) PRIMARY KEY, manifest jsonb NOT NULL, profile varchar(64) NOT NULL, approval text NOT NULL, prepared boolean NOT NULL);
CREATE TABLE consultant_sources (id varchar(64) PRIMARY KEY, product_id integer NOT NULL REFERENCES products(id) ON DELETE RESTRICT, language varchar(2) NOT NULL CHECK(language IN ('ru','uz')), provenance jsonb NOT NULL, withdrawn boolean NOT NULL);
CREATE TABLE consultant_sections (id varchar(100) PRIMARY KEY, source_id varchar(64) NOT NULL REFERENCES consultant_sources(id) ON DELETE RESTRICT, ordinal integer NOT NULL, body text NOT NULL, body_hash varchar(64) NOT NULL, heading text NOT NULL, caution boolean NOT NULL, pdf_page integer NOT NULL, UNIQUE(source_id,ordinal));
CREATE TABLE consultant_embeddings (section_id varchar(100) REFERENCES consultant_sections(id) ON DELETE RESTRICT, profile varchar(64), vector vector(1536) NOT NULL, PRIMARY KEY(section_id,profile));
CREATE TABLE consultant_memberships (release_id varchar(64) REFERENCES consultant_releases(id) ON DELETE RESTRICT, source_id varchar(64) REFERENCES consultant_sources(id) ON DELETE RESTRICT, PRIMARY KEY(release_id,source_id));
CREATE TABLE consultant_control (id integer PRIMARY KEY CHECK(id=1), enabled boolean NOT NULL, active varchar(64) REFERENCES consultant_releases(id), lease_owner varchar(36), lease_until timestamptz);
CREATE TABLE consultant_runs (id varchar(64) PRIMARY KEY, tab varchar(64) NOT NULL, owner varchar(36) NOT NULL UNIQUE, created timestamptz NOT NULL DEFAULT now(), cancelled boolean NOT NULL, done boolean NOT NULL);
CREATE TABLE consultant_rates (id serial PRIMARY KEY, network varchar(64) NOT NULL, lane varchar(10) NOT NULL, created timestamptz NOT NULL DEFAULT now());
CREATE INDEX ix_consultant_rates_network ON consultant_rates(network);
CREATE TABLE consultant_budgets (day date PRIMARY KEY, attempts integer NOT NULL, received integer NOT NULL, input_tokens integer NOT NULL, output_tokens integer NOT NULL, embedding_tokens integer NOT NULL);
CREATE TABLE consultant_reservations (id varchar(36) PRIMARY KEY, owner varchar(36) NOT NULL, day date NOT NULL REFERENCES consultant_budgets(day), created timestamptz NOT NULL DEFAULT now(), kind varchar(10) NOT NULL, received boolean NOT NULL);
INSERT INTO consultant_control(id,enabled) VALUES (1,false);
"""
    for statement in statements.split(";"):
        if statement.strip():
            op.execute(statement)


def downgrade() -> None:
    connection = op.get_bind()
    tables = [
        "consultant_reservations",
        "consultant_rates",
        "consultant_runs",
        "consultant_budgets",
        "consultant_memberships",
        "consultant_embeddings",
        "consultant_sections",
        "consultant_sources",
        "consultant_releases",
    ]
    # Empty disposable schemas can exercise the existing chain roundtrip. Never
    # delete derived evidence, reservations or enabled production state.
    for name in tables:
        if connection.scalar(select(exists().select_from(table(name)))):
            raise RuntimeError("Consultant downgrade refused: retain data and disable application")
    if connection.execute(
        text("SELECT EXISTS(SELECT 1 FROM consultant_control WHERE enabled OR active IS NOT NULL)")
    ).scalar():
        raise RuntimeError("Consultant downgrade refused: active state")
    op.drop_table("consultant_control")
    for name in tables:
        op.drop_table(name)
