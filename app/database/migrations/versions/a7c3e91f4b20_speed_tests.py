"""speed_tests: hourly active throughput tests (Ookla Speedtest CLI)

Revision ID: a7c3e91f4b20
Revises: bd2008cdf0c5
Create Date: 2026-09-28 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7c3e91f4b20'
down_revision: Union[str, Sequence[str], None] = 'bd2008cdf0c5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # init_db()'s create_all may already have created it on app start.
    if sa.inspect(op.get_bind()).has_table('speed_tests'):
        return
    op.create_table(
        'speed_tests',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('data_source', sa.String(length=8), nullable=False),
        sa.Column('trigger', sa.String(length=16), nullable=False),
        sa.Column('success', sa.Boolean(), nullable=False),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('ping_ms', sa.Float(), nullable=True),
        sa.Column('jitter_ms', sa.Float(), nullable=True),
        sa.Column('download_mbps', sa.Float(), nullable=True),
        sa.Column('upload_mbps', sa.Float(), nullable=True),
        sa.Column('download_latency_ms', sa.Float(), nullable=True),
        sa.Column('upload_latency_ms', sa.Float(), nullable=True),
        sa.Column('packet_loss_pct', sa.Float(), nullable=True),
        sa.Column('download_bytes', sa.BigInteger(), nullable=True),
        sa.Column('upload_bytes', sa.BigInteger(), nullable=True),
        sa.Column('server_id', sa.Integer(), nullable=True),
        sa.Column('server_name', sa.String(length=128), nullable=True),
        sa.Column('server_location', sa.String(length=128), nullable=True),
        sa.Column('isp', sa.String(length=128), nullable=True),
        sa.Column('result_url', sa.String(length=256), nullable=True),
        sa.Column('raw_json', sa.JSON(none_as_null=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('speed_tests', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_speed_tests_started_at'), ['started_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_speed_tests_data_source'), ['data_source'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('speed_tests')
