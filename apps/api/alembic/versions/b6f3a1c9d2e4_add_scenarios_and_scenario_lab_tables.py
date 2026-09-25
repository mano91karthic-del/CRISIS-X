"""add scenarios and scenario lab tables

Revision ID: b6f3a1c9d2e4
Revises: 103163b987c2
Create Date: 2026-09-19 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b6f3a1c9d2e4'
down_revision: Union[str, None] = '103163b987c2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'scenarios',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('project_id', sa.String(length=36), nullable=False),
        sa.Column('twin_id', sa.String(length=36), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['twin_id'], ['digital_twins.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('scenarios', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_scenarios_project_id'), ['project_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_scenarios_twin_id'), ['twin_id'], unique=False)

    op.create_table(
        'scenario_baseline_layers',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('scenario_id', sa.String(length=36), nullable=False),
        sa.Column('twin_layer_id', sa.String(length=36), nullable=True),
        sa.Column('dataset_id', sa.String(length=36), nullable=True),
        sa.Column('dataset_type', sa.String(length=64), nullable=False),
        sa.Column('category', sa.String(length=32), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['scenario_id'], ['scenarios.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['twin_layer_id'], ['twin_layers.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['dataset_id'], ['datasets.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('scenario_baseline_layers', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_scenario_baseline_layers_scenario_id'), ['scenario_id'], unique=False)
        batch_op.create_index(
            batch_op.f('ix_scenario_baseline_layers_twin_layer_id'), ['twin_layer_id'], unique=False
        )
        batch_op.create_index(batch_op.f('ix_scenario_baseline_layers_dataset_id'), ['dataset_id'], unique=False)

    op.create_table(
        'scenario_layer_overrides',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('scenario_id', sa.String(length=36), nullable=False),
        sa.Column('dataset_type', sa.String(length=64), nullable=False),
        sa.Column('category', sa.String(length=32), nullable=False),
        sa.Column('override_dataset_id', sa.String(length=36), nullable=True),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['scenario_id'], ['scenarios.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['override_dataset_id'], ['datasets.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('scenario_layer_overrides', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_scenario_layer_overrides_scenario_id'), ['scenario_id'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_scenario_layer_overrides_dataset_type'), ['dataset_type'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_scenario_layer_overrides_override_dataset_id'), ['override_dataset_id'], unique=False
        )

    # scenario_id added to the four existing run-record tables -- ordinary
    # one-directional FKs (no use_alter needed: `scenarios` has no FK
    # pointing back at any of these four tables, unlike the datasets <->
    # {hazard_scenarios,...} circularity every prior phase's migration
    # needed). No column is added to `datasets` itself -- Scenario Lab
    # produces no new Dataset rows of its own; its output datasets are
    # ordinary hazard/exposure/risk/route-analysis outputs, already
    # reachable via the existing hazard_scenario_id/exposure_analysis_id/
    # risk_analysis_id/route_analysis_id columns.
    for table_name in ('hazard_scenarios', 'exposure_analyses', 'risk_analyses', 'route_analyses'):
        with op.batch_alter_table(table_name, schema=None) as batch_op:
            batch_op.add_column(sa.Column('scenario_id', sa.String(length=36), nullable=True))
            batch_op.create_index(batch_op.f(f'ix_{table_name}_scenario_id'), ['scenario_id'], unique=False)
            batch_op.create_foreign_key(
                f'fk_{table_name}_scenario_id', 'scenarios', ['scenario_id'], ['id'], ondelete='SET NULL'
            )


def downgrade() -> None:
    for table_name in ('route_analyses', 'risk_analyses', 'exposure_analyses', 'hazard_scenarios'):
        with op.batch_alter_table(table_name, schema=None) as batch_op:
            batch_op.drop_constraint(f'fk_{table_name}_scenario_id', type_='foreignkey')
            batch_op.drop_index(batch_op.f(f'ix_{table_name}_scenario_id'))
            batch_op.drop_column('scenario_id')

    with op.batch_alter_table('scenario_layer_overrides', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_scenario_layer_overrides_override_dataset_id'))
        batch_op.drop_index(batch_op.f('ix_scenario_layer_overrides_dataset_type'))
        batch_op.drop_index(batch_op.f('ix_scenario_layer_overrides_scenario_id'))
    op.drop_table('scenario_layer_overrides')

    with op.batch_alter_table('scenario_baseline_layers', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_scenario_baseline_layers_dataset_id'))
        batch_op.drop_index(batch_op.f('ix_scenario_baseline_layers_twin_layer_id'))
        batch_op.drop_index(batch_op.f('ix_scenario_baseline_layers_scenario_id'))
    op.drop_table('scenario_baseline_layers')

    with op.batch_alter_table('scenarios', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_scenarios_twin_id'))
        batch_op.drop_index(batch_op.f('ix_scenarios_project_id'))
    op.drop_table('scenarios')
