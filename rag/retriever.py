"""FAISS 의미 검색과 CSV의 정확 연결 근거를 함께 수집."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Optional

from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document

from .build_index import (
    build_embedding_text,
    document_link_metadata,
    validate_embedding_inputs,
)
from .data_loader import RagConfig, RagData, normalize_text
from .menu_lookup import (
    applicable_options_for_menu_ids,
    pairing_rules_for_menu_ids,
    variants_for_menu_id,
)
from .prompts import QueryPlan


def search_only(
    question: str,
    vectorstore: FAISS,
    config: RagConfig,
    top_k: Optional[int] = None,
) -> list[dict[str, Any]]:
    if not question.strip():
        raise ValueError("질문을 입력하세요.")
    validate_embedding_inputs([question], config)
    hits = vectorstore.similarity_search_with_score(question, k=top_k or config.top_k)
    rows = []
    for document, distance in hits:
        rows.append({
            "document_id": document.metadata.get("document_id"),
            "title": document.metadata.get("title"),
            "document_type": document.metadata.get("document_type"),
            "distance": float(distance),
            "content": document.metadata.get("original_content"),
            "source_type": document.metadata.get("source_type"),
            "menu_group_id": document.metadata.get("menu_group_id"),
            "variant_id": document.metadata.get("variant_id"),
            "temperature": document.metadata.get("temperature"),
        })
    return rows


@dataclass
class RetrievedEvidence:
    document: Document
    distance: Optional[float]
    reason: str


def document_from_source_row(row: dict[str, Any], data: RagData) -> Document:
    return Document(
        page_content=build_embedding_text(row),
        metadata={
            "document_id": row["document_id"],
            "document_type": row["document_type"],
            "source_type": row["source_type"],
            "title": row["title"],
            "search_keywords": row.get("search_keywords"),
            "original_content": row.get("content"),
            **document_link_metadata(row, data),
        },
    )


def exact_document_ids_for_menus(menu_ids: Iterable[str], data: RagData) -> list[str]:
    ids = []
    for menu_id in menu_ids:
        if menu_id in data.document_by_id:
            ids.append(menu_id)
        for variant in variants_for_menu_id(data, menu_id):
            detail_id = f"DETAIL-{variant['variant_id']}"
            if detail_id in data.document_by_id:
                ids.append(detail_id)
    return ids


def semantic_menu_candidate_ids(
    question: str,
    vectorstore: FAISS,
    data: RagData,
    *,
    excluded_menu_ids: Iterable[str] = (),
    allowed_menu_ids: Optional[Iterable[str]] = None,
    limit: int = 3,
) -> list[str]:
    excluded = set(excluded_menu_ids)
    allowed = set(allowed_menu_ids) if allowed_menu_ids is not None else set(data.catalog_by_id)
    candidates: list[str] = []
    for document, _ in vectorstore.similarity_search_with_score(question, k=30):
        if document.metadata.get("document_type") != "menu":
            continue
        menu_id = document.metadata.get("menu_group_id")
        if (
            menu_id in data.catalog_by_id
            and menu_id in allowed
            and menu_id not in excluded
            and menu_id not in candidates
        ):
            candidates.append(menu_id)
        if len(candidates) >= limit:
            break
    return candidates


def retrieve_evidence(
    question: str,
    plan: QueryPlan,
    resolved: dict[str, Any],
    recommendation_ids: list[str],
    option_ids: list[str],
    unavailable_fallback_ids: list[str],
    temperature_alternative_menu_ids: list[str],
    data: RagData,
    vectorstore: FAISS,
    config: RagConfig,
) -> list[RetrievedEvidence]:
    validate_embedding_inputs([question], config)
    evidence: list[RetrievedEvidence] = []
    seen: set[str] = set()

    for document, distance in vectorstore.similarity_search_with_score(
        question, k=config.fetch_k
    ):
        document_id = document.metadata["document_id"]
        if document_id in seen:
            continue
        evidence.append(RetrievedEvidence(document, float(distance), "semantic"))
        seen.add(document_id)
        if len([item for item in evidence if item.reason == "semantic"]) >= config.top_k:
            break

    exact_menu_ids = list(resolved["exact_menu_ids"])
    if plan.intent == "recommend":
        exact_menu_ids.extend(recommendation_ids[:8])
    exact_menu_ids.extend(unavailable_fallback_ids)
    exact_menu_ids.extend(temperature_alternative_menu_ids)

    exact_ids = exact_document_ids_for_menus(exact_menu_ids, data)
    exact_ids.extend(option_ids)
    if plan.intent == "pairing":
        exact_ids.extend(
            row["pairing_id"]
            for row in pairing_rules_for_menu_ids(data, resolved["exact_menu_ids"])
        )
    if plan.list_options:
        exact_ids.extend(
            item["option"]["option_id"]
            for item in applicable_options_for_menu_ids(data, resolved["exact_menu_ids"])
        )
    if plan.unavailable_term:
        for row in data.tables["rag_documents"]:
            if (
                row["document_type"] == "alternative"
                and normalize_text(plan.unavailable_term) in normalize_text(row["title"])
            ):
                exact_ids.append(row["document_id"])

    for document_id in exact_ids:
        if document_id in seen or document_id not in data.document_by_id:
            continue
        document = document_from_source_row(data.document_by_id[document_id], data)
        evidence.append(RetrievedEvidence(document, None, "exact_csv_link"))
        seen.add(document_id)
    return evidence


def evidence_to_context(evidence: list[RetrievedEvidence]) -> str:
    blocks = []
    for item in evidence:
        metadata = item.document.metadata
        distance = "N/A" if item.distance is None else f"{item.distance:.6f}"
        blocks.append(
            f"[문서 ID: {metadata['document_id']} | 유형: {metadata['document_type']} | "
            f"출처: {metadata['source_type']} | 확보 방식: {item.reason} | 거리: {distance}]\n"
            f"{metadata.get('original_content') or item.document.page_content}"
        )
    return "\n\n---\n\n".join(blocks)
