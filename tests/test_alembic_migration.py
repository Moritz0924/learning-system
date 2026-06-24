from alembic.command import upgrade
from alembic.config import Config
from sqlalchemy import create_engine, inspect


def test_alembic_migration_creates_stage1_tables(tmp_path):
    db_path = tmp_path / "migration.db"
    database_url = f"sqlite+pysqlite:///{db_path}"

    config = Config("backend/alembic.ini")
    config.set_main_option("script_location", "backend/alembic")
    config.set_main_option("sqlalchemy.url", database_url)

    upgrade(config, "head")

    engine = create_engine(database_url)
    inspector = inspect(engine)

    assert "learning_state_snapshots" in inspector.get_table_names()
    assert "baseline_diagnostics" in inspector.get_table_names()
    assert "knowledge_nodes" in inspector.get_table_names()
    assert "plan_tasks" in inspector.get_table_names()
    assert "assessments" in inspector.get_table_names()
    assert "assessment_items" in inspector.get_table_names()
    assert "assessment_attempts" in inspector.get_table_names()
    assert "assessment_answers" in inspector.get_table_names()
    assert "plan_adjustments" in inspector.get_table_names()
    assert "phase_assessment_states" in inspector.get_table_names()
    assert "documents" in inspector.get_table_names()
    assert "document_chunks" in inspector.get_table_names()
    assert "agent_runs" in inspector.get_table_names()
    assert "tool_calls" in inspector.get_table_names()
    assert "learning_sessions" in inspector.get_table_names()
    assert "learning_events" in inspector.get_table_names()

    indexes = inspector.get_indexes("learning_state_snapshots")
    constraints = inspector.get_unique_constraints("learning_state_snapshots")
    unique_names = {item["name"] for item in indexes + constraints}
    assert "uq_learning_state_snapshots_user_goal" in unique_names
