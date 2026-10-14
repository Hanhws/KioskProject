"""Lazy adapter to the existing rag branch service and its saved FAISS index."""

from __future__ import annotations

import os
from functools import cached_property
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


class LiveRAG:
    @cached_property
    def service(self):
        if not os.getenv("OPENAI_API_KEY", "").strip():
            raise RuntimeError("KIOSK_MODE=live requires OPENAI_API_KEY")
        from rag import CafeRAGService, RagConfig

        config = RagConfig(
            data_dir=ROOT / "data/megacoffee_rag_enriched",
            index_dir=ROOT / "rag/rag_index/faiss_openai_v1",
            embedding_json_path=ROOT / "rag/rag_embeddings.json",
            answer_model=os.getenv("RAG_ANSWER_MODEL", "gpt-4.1-mini"),
            rebuild_index=False,
        )
        return CafeRAGService.from_config(config)

    def ask(self, question: str, *, prior_conditions: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.service.ask(question, prior_conditions=prior_conditions)
