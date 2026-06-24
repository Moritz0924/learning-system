from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import Curriculum, KnowledgeEdge, KnowledgeNode


CURRICULUM_ID = "curriculum-ai-app-v1"
NODES = [
    ("python_foundations", "Python Foundations"),
    ("fastapi_basics", "FastAPI Basics"),
    ("llm_api_basics", "LLM API Basics"),
    ("rag_foundations", "RAG Foundations"),
    ("langgraph_basics", "LangGraph Basics"),
]


def ensure_curriculum_seeded(session: Session) -> Curriculum:
    curriculum = session.get(Curriculum, CURRICULUM_ID)
    if curriculum is None:
        curriculum = Curriculum(
            id=CURRICULUM_ID,
            code="ai_app_v1",
            version="v1",
            title="AI Application Development V1",
            domain="ai_app_dev",
            is_active=True,
        )
        session.add(curriculum)
        session.flush()

    existing = {
        node.code
        for node in session.scalars(
            select(KnowledgeNode).where(KnowledgeNode.curriculum_id == curriculum.id)
        )
    }
    for index, (code, title) in enumerate(NODES, start=1):
        if code not in existing:
            session.add(
                KnowledgeNode(
                    id=f"node-{code}",
                    curriculum_id=curriculum.id,
                    code=code,
                    title=title,
                    sequence=index,
                    node_type="concept" if index < len(NODES) else "project",
                    difficulty=min(5, index),
                    estimated_minutes=45,
                    mastery_threshold=70,
                    metadata_json={"stage": "v1", "node_code": code},
                )
            )
    session.flush()

    nodes_by_code = {
        node.code: node
        for node in session.scalars(
            select(KnowledgeNode).where(KnowledgeNode.curriculum_id == curriculum.id)
        )
    }
    existing_edges = {
        (edge.from_node_id, edge.to_node_id)
        for edge in session.scalars(
            select(KnowledgeEdge).where(KnowledgeEdge.curriculum_id == curriculum.id)
        )
    }
    ordered_codes = [code for code, _ in NODES]
    for before, after in zip(ordered_codes, ordered_codes[1:]):
        from_id = nodes_by_code[before].id
        to_id = nodes_by_code[after].id
        if (from_id, to_id) not in existing_edges:
            session.add(
                KnowledgeEdge(
                    id=f"edge-{before}-{after}",
                    curriculum_id=curriculum.id,
                    from_node_id=from_id,
                    to_node_id=to_id,
                    relation_type="prerequisite",
                )
            )
    session.commit()
    return curriculum


def ordered_nodes(session: Session, curriculum_id: str) -> list[KnowledgeNode]:
    return list(
        session.scalars(
            select(KnowledgeNode)
            .where(KnowledgeNode.curriculum_id == curriculum_id)
            .order_by(KnowledgeNode.sequence)
        )
    )
