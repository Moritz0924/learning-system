from __future__ import annotations

from alembic.command import downgrade, upgrade
from alembic.config import Config
from sqlalchemy import create_engine, inspect


def test_saved_learning_nodes_migration_round_trips(tmp_path) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'saved-learning-nodes.db'}"
    config = Config("backend/alembic.ini")
    config.set_main_option("script_location", "backend/alembic")
    config.set_main_option("sqlalchemy.url", database_url)
    upgrade(config, "20260831_0026")

    engine = create_engine(database_url)
    upgrade(config, "20260831_0027")
    schema = inspect(engine)
    assert "saved_learning_nodes" in schema.get_table_names()
    assert set(
        schema.get_pk_constraint("saved_learning_nodes")["constrained_columns"]
    ) == {"user_id", "goal_id", "knowledge_node_id"}
    assert "fk_saved_learning_nodes_user_goal" in {
        constraint["name"]
        for constraint in schema.get_foreign_keys("saved_learning_nodes")
    }

    downgrade(config, "20260831_0026")
    assert "saved_learning_nodes" not in inspect(engine).get_table_names()
    upgrade(config, "20260831_0027")
    assert "saved_learning_nodes" in inspect(engine).get_table_names()
    engine.dispose()
