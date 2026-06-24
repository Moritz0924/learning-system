from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.routers import assessments, documents, goals, onboarding, plans, state, tasks, tools, tutor

app = FastAPI(title="Adaptive Private Tutor Stage 1")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:3000",
        "http://localhost:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(goals.router)
app.include_router(onboarding.router)
app.include_router(state.router)
app.include_router(tutor.router)
app.include_router(assessments.router)
app.include_router(plans.router)
app.include_router(documents.router)
app.include_router(tasks.router)
app.include_router(tools.router)
