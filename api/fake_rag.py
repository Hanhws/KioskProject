"""Fixed development responses. This module never imports or initializes the real RAG."""

from typing import Any


class FakeRAG:
    def ask(
        self,
        question: str,
        *,
        prior_conditions: dict | None = None,
        recent_history: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        if "아메리카노" in question and "가격" in question:
            return {
                "answer": "[개발·시험용 가짜 RAG 응답] 아메리카노 가격 질문을 받았습니다. 실제 메뉴 검색 결과는 아직 연결되지 않았습니다.",
                "status": "ok",
                "choices": [],
                "related_menu_ids": [],
                "evidence_document_ids": ["FAKE-RAG-001"],
                "next_action": None,
                "resume_prompt": None,
                "error": None,
            }
        return {
            "answer": "[개발·시험용 가짜 RAG 응답] 현재는 '아메리카노 가격' 질문만 시험할 수 있습니다.",
            "status": "not_found",
            "choices": [],
            "related_menu_ids": [],
            "evidence_document_ids": [],
            "next_action": None,
            "resume_prompt": None,
            "error": None,
        }
