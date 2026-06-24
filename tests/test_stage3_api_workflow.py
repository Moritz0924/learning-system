from __future__ import annotations

import base64

from sqlalchemy import select, text

from backend.app.models import AgentRun, DocumentChunk


def _simple_pdf_bytes(text_content: str) -> bytes:
    escaped = text_content.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 18 Tf 72 720 Td ({escaped}) Tj ET".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 4 0 R >> >> "
        b"/MediaBox [0 0 612 792] /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    pdf = b"%PDF-1.4\n"
    offsets = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf += f"{index} 0 obj\n".encode("ascii") + body + b"\nendobj\n"
    xref_offset = len(pdf)
    pdf += f"xref\n0 {len(objects) + 1}\n".encode("ascii")
    pdf += b"0000000000 65535 f \n"
    for offset in offsets:
        pdf += f"{offset:010d} 00000 n \n".encode("ascii")
    pdf += (
        b"trailer\n"
        + f"<< /Size {len(objects) + 1} /Root 1 0 R >>\n".encode("ascii")
        + b"startxref\n"
        + str(xref_offset).encode("ascii")
        + b"\n%%EOF\n"
    )
    return pdf


def _create_goal_and_diagnosis(client, user_id="stage3-user"):
    goal_response = client.post(
        "/api/goals",
        json={
            "user_id": user_id,
            "email": f"{user_id}@example.com",
            "display_name": "Stage Three Learner",
            "title": "Learn AI application development",
            "target_outcome": "Build and deploy a RAG tutor demo",
            "deadline": "2026-08-15",
            "weekly_hours_target": 10,
            "learning_preferences": {"style": "coach_then_code"},
        },
    )
    assert goal_response.status_code == 201
    goal = goal_response.json()

    diagnosis_response = client.post(
        "/api/onboarding/diagnosis",
        json={
            "user_id": goal["user_id"],
            "goal_id": goal["goal_id"],
            "self_assessment": {
                "python_level": 4,
                "api_level": 3,
                "llm_level": 2,
                "rag_level": 1,
                "langgraph_level": 0,
            },
            "submitted_answers": {
                "questions": [
                    {"node_code": "python_foundations", "is_correct": True},
                    {"node_code": "fastapi_basics", "is_correct": True},
                    {"node_code": "llm_api_basics", "is_correct": False},
                    {"node_code": "rag_foundations", "is_correct": False},
                ]
            },
        },
    )
    assert diagnosis_response.status_code == 201
    return goal


def test_stage3_api_workflow_runs_tutor_assessment_replan_documents_and_tools(client, session_factory):
    goal = _create_goal_and_diagnosis(client)
    headers = {"X-User-Id": goal["user_id"]}

    chat_response = client.post(
        "/api/tutor/chat",
        json={
            "user_id": goal["user_id"],
            "goal_id": goal["goal_id"],
            "thread_id": "thread-stage3",
            "message": "How should I think about RAG retrieval?",
        },
    )
    assert chat_response.status_code == 200
    chat_payload = chat_response.json()
    assert chat_payload["final_answer"]
    assert chat_payload["citations"]
    assert chat_payload["citations"][0]["source_url"]
    with session_factory() as session:
        agent_run = session.scalar(select(AgentRun).order_by(AgentRun.created_at.desc()))
        assert agent_run.graph_name == "phase2_tutor_graph"

    state_response = client.get(f"/api/state/current?goal_id={goal['goal_id']}", headers=headers)
    assert state_response.status_code == 200
    state_payload = state_response.json()
    assert "review_queue" in state_payload["current_state"]
    node_id = state_payload["today_tasks"][0]["knowledge_node_id"]

    assessment_response = client.post(
        "/api/assessments",
        json={
            "user_id": goal["user_id"],
            "goal_id": goal["goal_id"],
            "thread_id": "thread-stage3",
            "assessment_type": "daily",
            "knowledge_node_ids": [node_id],
        },
    )
    assert assessment_response.status_code == 201
    assessment_payload = assessment_response.json()
    assert assessment_payload["assessment_type"] == "daily"
    assert len(assessment_payload["items"]) == 3

    submit_response = client.post(
        f"/api/assessments/{assessment_payload['assessment_id']}/submit",
        json={
            "user_id": goal["user_id"],
            "answers": {item["item_id"]: "wrong" for item in assessment_payload["items"]},
        },
    )
    assert submit_response.status_code == 200
    submit_payload = submit_response.json()
    assert submit_payload["score"] < 70
    assert submit_payload["mastery_updates"]
    assert submit_payload["answers"][0]["evidence_json"]["wrong_reason_tags"]

    replan_response = client.post(
        "/api/plans/replan",
        json={
            "user_id": goal["user_id"],
            "goal_id": goal["goal_id"],
            "thread_id": "thread-stage3",
            "message": "Please rebalance my plan based on the latest evidence.",
        },
    )
    assert replan_response.status_code == 200
    replan_payload = replan_response.json()
    assert replan_payload["adjustment_id"]
    assert replan_payload["decision"] == "remediate"
    assert replan_payload["change_summary"]
    assert replan_payload["rationale_json"]
    assert replan_payload["evidence_json"]["observer_signals"]["correctness_rate"] < 0.6
    assert replan_payload["evidence_json"]["manual_request"] == "Please rebalance my plan based on the latest evidence."

    document_response = client.post(
        "/api/documents/upload",
        json={
            "user_id": goal["user_id"],
            "filename": "rag-notes.md",
            "mime_type": "text/markdown",
            "content": "# RAG\nUse trusted chunks and citations.",
        },
    )
    assert document_response.status_code == 201
    assert document_response.json()["parse_status"] == "success"
    with session_factory() as session:
        chunk_count = session.execute(text("select count(*) from document_chunks")).scalar_one()
        assert chunk_count >= 1

    uploaded_chat_response = client.post(
        "/api/tutor/chat",
        json={
            "user_id": goal["user_id"],
            "goal_id": goal["goal_id"],
            "thread_id": "thread-stage3-doc",
            "message": "How do trusted chunks and citations work?",
        },
    )
    assert uploaded_chat_response.status_code == 200
    uploaded_chat = uploaded_chat_response.json()
    assert uploaded_chat["citations"][0]["source_title"] == "rag-notes.md"
    assert uploaded_chat["citations"][0]["metadata"]["source_type"] == "markdown"

    pdf_response = client.post(
        "/api/documents/upload",
        json={
            "user_id": goal["user_id"],
            "filename": "rag-guide.pdf",
            "mime_type": "application/pdf",
            "content_base64": base64.b64encode(_simple_pdf_bytes("PDF citations enter RAG")).decode("ascii"),
        },
    )
    assert pdf_response.status_code == 201
    assert pdf_response.json()["parse_status"] == "success"
    with session_factory() as session:
        pdf_chunk = session.scalar(
            select(DocumentChunk)
            .where(DocumentChunk.document_id == pdf_response.json()["id"])
            .order_by(DocumentChunk.chunk_index)
        )
        assert "PDF citations enter RAG" in pdf_chunk.content
        assert pdf_chunk.metadata_json["source_type"] == "pdf"
        assert pdf_chunk.metadata_json["page_number"] == 1

    documents_response = client.get("/api/documents", params={"user_id": goal["user_id"]})
    assert documents_response.status_code == 200
    assert documents_response.json()["documents"][0]["filename"] in {"rag-notes.md", "rag-guide.pdf"}

    search_response = client.post(
        "/api/tools/search-official-learning-sources",
        json={
            "query": "FastAPI dependency injection",
            "domains": ["fastapi.tiangolo.com"],
        },
    )
    assert search_response.status_code == 200
    search_payload = search_response.json()
    assert search_payload["results"][0]["source_level"] == "official"
    assert search_payload["results"][0]["retrieved_at"]

    phase_response = client.post(
        "/api/assessments/phase",
        json={
            "user_id": goal["user_id"],
            "goal_id": goal["goal_id"],
            "thread_id": "thread-stage3",
            "phase_code": "phase-ai-app-v1",
            "knowledge_node_ids": [node_id],
        },
    )
    assert phase_response.status_code == 201
    with session_factory() as session:
        phase_count = session.execute(text("select count(*) from phase_assessment_states")).scalar_one()
        assert phase_count == 1

    refreshed = client.get(f"/api/state/current?goal_id={goal['goal_id']}", headers=headers).json()
    assert refreshed["latest_plan_adjustment"]["adjustment_id"] == replan_payload["adjustment_id"]
    assert refreshed["latest_plan_adjustment"]["change_summary"] == replan_payload["change_summary"]
    assert refreshed["latest_plan_adjustment"]["rationale_json"] == replan_payload["rationale_json"]
