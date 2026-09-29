"""add_study_area_dataset_id_to_digital_twins

Revision ID: 6ff339e1d1ae
Revises: b6f3a1c9d2e4
Create Date: 2026-09-28 09:16:28.148377

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6ff339e1d1ae'
down_revision: Union[str, None] = 'b6f3a1c9d2e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('digital_twins', schema=None) as batch_op:
        batch_op.add_column(sa.Column('study_area_dataset_id', sa.String(length=36), nullable=True))
        batch_op.create_foreign_key(
            'fk_digital_twins_study_area_dataset_id_datasets',
            'datasets',
            ['study_area_dataset_id'],
            ['id'],
            ondelete='SET NULL',
        )


def downgrade() -> None:
    with op.batch_alter_table('digital_twins', schema=None) as batch_op:
        batch_op.drop_constraint('fk_digital_twins_study_area_dataset_id_datasets', type_='foreignkey')
        batch_op.drop_column('study_area_dataset_id')
