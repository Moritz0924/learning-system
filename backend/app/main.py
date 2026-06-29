from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError

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


@app.exception_handler(OperationalError)
async def database_operational_error_handler(request: Request, exc: OperationalError) -> JSONResponse:
    message = str(exc).lower()
    if "no such table" in message or "does not exist" in message:
        return JSONResponse(
            status_code=503,
            content={
                "detail": (
                    "Database schema is not migrated. Run "
                    "`.\\.venv\\Scripts\\python.exe -m alembic -c backend\\alembic.ini upgrade head` "
                    "before starting the API."
                )
            },
        )
    raise exc
