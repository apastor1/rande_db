from alembic import op
import sqlalchemy as sa
revision = "0001_create_schema"
down_revision = None

def upgrade():
    op.execute(sa.text('CREATE SCHEMA IF NOT EXISTS "datalake"'))

def downgrade():
    op.execute(sa.text('DROP SCHEMA IF EXISTS "datalake" CASCADE'))
