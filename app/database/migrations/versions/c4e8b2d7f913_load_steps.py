"""load_steps: per-step record of congestion ramp experiments (iperf3)

Revision ID: c4e8b2d7f913
Revises: a7c3e91f4b20
Create Date: 2026-09-28 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4e8b2d7f913'
down_revision: Union[str, Sequence[str], None] = 'a7c3e91f4b20'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # init_db()'s create_all may already have created it on app start.
    if sa.inspect(op.get_bind()).has_table('load_steps'):
        return
    op.create_table(
        'load_steps',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('experiment_id', sa.String(length=64), nullable=False),
        sa.Column('data_source', sa.String(length=8), nullable=False),
        sa.Column('step_index', sa.Integer(), nullable=False),
        sa.Column('direction', sa.String(length=16), nullable=False),
        sa.Column('target_pct', sa.Float(), nullable=False),
        sa.Column('target_mbps', sa.Float(), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('achieved_mbps', sa.Float(), nullable=True),
        sa.Column('retransmits', sa.Integer(), nullable=True),
        sa.Column('success', sa.Boolean(), nullable=False),
        sa.Column('error', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('load_steps', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_load_steps_experiment_id'), ['experiment_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_load_steps_data_source'), ['data_source'], unique=False)
        batch_op.create_index(batch_op.f('ix_load_steps_started_at'), ['started_at'], unique=False)
        batch_op.create_index('ix_load_steps_exp_step', ['experiment_id', 'step_index'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('load_steps')
