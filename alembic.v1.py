# alembic revision -m "voter data schema v1 (agnostic)"
from alembic import op
import sqlalchemy as sa

revision = "0001_voter_schema_agnostic"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # --- voter_file ----------------------------------------------------------
    op.create_table(
        "voter_file",
        sa.Column("id", sa.String(length=36), primary_key=True),  # store UUID as text
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=True, unique=True),
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("row_count", sa.Integer(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
    )
    op.create_index("ix_voter_file_received_at", "voter_file", ["received_at"])

    # --- voter_record --------------------------------------------------------
    op.create_table(
        "voter_record",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("file_id", sa.String(length=36), sa.ForeignKey("voter_file.id", ondelete="SET NULL"), nullable=True),

        # Full original row (lossless)
        sa.Column("raw", sa.JSON(), nullable=False),

        # Standardized name
        sa.Column("first_name", sa.Text(), nullable=True),
        sa.Column("middle_name", sa.Text(), nullable=True),
        sa.Column("last_name", sa.Text(), nullable=True),

        # Standardized address
        sa.Column("street_number", sa.Text(), nullable=True),
        sa.Column("street_name", sa.Text(), nullable=True),
        sa.Column("municipality", sa.Text(), nullable=True),
        sa.Column("state", sa.String(length=2), nullable=True),
        sa.Column("zip5", sa.String(length=5), nullable=True),

        # Materialized (app-maintained) canonical + hashes (portable)
        sa.Column("name_canonical", sa.Text(), nullable=False, server_default=""),
        sa.Column("address_canonical", sa.Text(), nullable=False, server_default=""),
        sa.Column("name_hash", sa.String(length=64), nullable=False, server_default=""),     # hex
        sa.Column("address_hash", sa.String(length=64), nullable=False, server_default=""),  # hex

        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_index("ix_voter_record_file_id", "voter_record", ["file_id"])
    op.create_index("ix_voter_record_name_hash", "voter_record", ["name_hash"])
    op.create_index("ix_voter_record_address_hash", "voter_record", ["address_hash"])
    op.create_index("ix_voter_record_zip5", "voter_record", ["zip5"])
    op.create_index("ix_voter_record_state", "voter_record", ["state"])
    op.create_index("ix_voter_record_municipality", "voter_record", ["municipality"])

    # --- voter_geocode -------------------------------------------------------
    op.create_table(
        "voter_geocode",
        sa.Column("record_id", sa.String(length=36), sa.ForeignKey("voter_record.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("benchmark", sa.Text(), primary_key=True),
        sa.Column("vintage", sa.Text(), primary_key=True),
        sa.Column("geoid", sa.Text(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("status", sa.Text(), nullable=True),
        sa.Column("geocoded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("notes", sa.Text(), nullable=True),
    )

    op.create_index("ix_voter_geocode_combo", "voter_geocode", ["benchmark", "vintage"])
    op.create_index("ix_voter_geocode_geoid", "voter_geocode", ["geoid"])


def downgrade():
    op.drop_index("ix_voter_geocode_geoid", table_name="voter_geocode")
    op.drop_index("ix_voter_geocode_combo", table_name="voter_geocode")
    op.drop_table("voter_geocode")

    op.drop_index("ix_voter_record_municipality", table_name="voter_record")
    op.drop_index("ix_voter_record_state", table_name="voter_record")
    op.drop_index("ix_voter_record_zip5", table_name="voter_record")
    op.drop_index("ix_voter_record_address_hash", table_name="voter_record")
    op.drop_index("ix_voter_record_name_hash", table_name="voter_record")
    op.drop_index("ix_voter_record_file_id", table_name="voter_record")
    op.drop_table("voter_record")

    op.drop_index("ix_voter_file_received_at", table_name="voter_file")
    op.drop_table("voter_file")
