"""A shared dashboard's viewer (``ctx.read_only``) cannot change the owner's documents
or chat history."""

from __future__ import annotations

import asyncio
import io

import pytest
from fastapi import HTTPException, UploadFile

from mate.sdk.testing import build_test_context, sample_log

# module.py imports the module's own venv deps (langgraph et al.); see test_ingest.py.
pytest.importorskip("langchain_core")
from modules.concept_drift_explainer.module import (
    DOCS_SUBDIR,
    ChatRequest,
    ConceptDriftExplainerModule,
)


def test_a_read_only_viewer_cannot_change_documents_or_chats() -> None:
    async def check() -> None:
        module = ConceptDriftExplainerModule()
        ctx = await build_test_context(sample_log(), module_id=module.id, read_only=True)
        upload = UploadFile(io.BytesIO(b"notes"), filename="2024-01-01_notes.txt")
        calls = [
            module.upload_doc(ctx, upload),
            module.delete_doc(ctx, "2024-01-01_notes.txt"),
            module.chat(ctx, ChatRequest(drift_key="d", user_question="why?")),
        ]
        for call in calls:
            with pytest.raises(HTTPException) as exc:
                await call
            assert exc.value.status_code == 403
        assert upload.file.tell() == 0
        async with ctx.event_log as log:
            assert not (log.events_path.parent / DOCS_SUBDIR).exists()
        assert not ctx.cache.data  # type: ignore[attr-defined]

    asyncio.run(check())
