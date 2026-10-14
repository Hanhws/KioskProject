"""질문 계획 생성, 답변 생성, 근거 값 검증."""

from __future__ import annotations

import json
import os
import re
from typing import Any, Iterable, Optional

from langchain_openai import ChatOpenAI

from .data_loader import RagConfig, RagData, normalize_text
from .menu_lookup import variants_for_menu_id
from .prompts import (
    ANSWER_PROMPT,
    PLANNER_PROMPT,
    AnswerClaim,
    AnswerDraft,
    QueryPlan,
)
from .retriever import RetrievedEvidence, evidence_to_context


def make_llm(config: RagConfig) -> ChatOpenAI:
    if not os.getenv("OPENAI_API_KEY", "").strip():
        raise RuntimeError("OPENAI_API_KEY가 설정되지 않았습니다.")
    return ChatOpenAI(model=config.answer_model, temperature=0)


def plan_question(
    question: str,
    data: RagData,
    config: RagConfig,
    prior_conditions: Optional[dict[str, Any]] = None,
) -> QueryPlan:
    planner = PLANNER_PROMPT | make_llm(config).with_structured_output(
        QueryPlan, method="json_schema"
    )
    return planner.invoke({
        "question": question,
        "prior_conditions": json.dumps(prior_conditions or {}, ensure_ascii=False),
        "types": sorted({menu["type"] for menu in data.tables["menu_catalog"]}),
        "categories": sorted({menu["category"] for menu in data.tables["menu_catalog"]}),
    })


def normalize_query_plan(plan: QueryPlan, data: RagData) -> QueryPlan:
    if plan.intent == "compare" and not plan.requested_fields:
        plan.requested_fields = ["맛", "가격", "커피 진하기"]
    plan.excluded_menu_ids = sorted(
        set(plan.excluded_menu_ids) & set(data.catalog_by_id)
    )
    plan.clarification_count = max(0, plan.clarification_count)
    return plan


def policy_context(data: RagData) -> str:
    return json.dumps(
        [
            {
                "policy_id": row["policy_id"],
                "topic": row["topic"],
                "response_policy": row["response_policy"],
                "source_type": row["source_type"],
            }
            for row in data.tables["faq_policy"]
        ],
        ensure_ascii=False,
        indent=2,
    )


def source_record(source_id: str, data: RagData) -> Optional[dict[str, Any]]:
    if source_id in data.catalog_by_id:
        return data.catalog_by_id[source_id]
    if source_id in data.variant_by_id:
        return data.variant_by_id[source_id]
    if source_id.startswith("DETAIL-") and source_id.removeprefix("DETAIL-") in data.variant_by_id:
        return data.variant_by_id[source_id.removeprefix("DETAIL-")]
    if source_id in data.option_by_id:
        return data.option_by_id[source_id]
    if source_id in data.policy_by_id:
        return data.policy_by_id[source_id]
    for table_name, id_field in [
        ("pairings", "pairing_id"),
        ("rag_documents", "document_id"),
    ]:
        row = next(
            (row for row in data.tables[table_name] if row.get(id_field) == source_id),
            None,
        )
        if row:
            return row
    return None


def claim_value_matches(actual: Any, claimed: str) -> bool:
    if actual is None:
        return False
    actual_text = str(actual)
    claimed_text = str(claimed)
    temperature_aliases = {
        "HOT": {"hot", "따뜻한", "뜨거운"},
        "ICE": {"ice", "아이스", "차가운", "시원한"},
    }
    if actual_text in temperature_aliases:
        normalized_claim = normalize_text(claimed_text)
        return any(
            normalize_text(alias) in normalized_claim
            for alias in temperature_aliases[actual_text]
        )
    actual_digits = re.sub(r"[^0-9.-]", "", actual_text)
    claimed_digits = re.sub(r"[^0-9.-]", "", claimed_text)
    if actual_digits and claimed_digits:
        return actual_digits == claimed_digits
    return normalize_text(actual_text) == normalize_text(claimed_text)


def validate_answer_claims(
    claims: Iterable[AnswerClaim], data: RagData
) -> tuple[list[dict[str, str]], list[str]]:
    strict_fields = {
        "temperature", "price_krw", "volume_ml", "calories_kcal",
        "sugar_g", "protein_g", "saturated_fat_g", "caffeine_mg",
        "sodium_mg", "cholesterol_mg", "carbohydrate_g", "fat_g",
        "trans_fat_g", "decaf_available", "size_policy",
        "max_quantity", "price_delta_krw",
    }
    valid, issues = [], []
    for claim in claims:
        record = source_record(claim.source_id, data)
        if (
            record is not None
            and claim.field in record
            and claim_value_matches(record.get(claim.field), claim.value)
        ):
            valid.append(claim.model_dump())
            continue

        candidate_rows: list[tuple[str, dict[str, Any]]] = []
        if claim.source_id in data.catalog_by_id:
            candidate_rows.extend(
                (variant["variant_id"], variant)
                for variant in variants_for_menu_id(data, claim.source_id)
            )
        matching_rows = [
            (source_id, row)
            for source_id, row in candidate_rows
            if claim.field in row
            and claim_value_matches(row.get(claim.field), claim.value)
        ]
        if matching_rows:
            corrected_source_id, _ = matching_rows[0]
            valid.append({
                "field": claim.field,
                "value": claim.value,
                "source_id": corrected_source_id,
            })
            continue

        if claim.field in strict_fields:
            if record is None:
                issues.append(f"확인할 수 없는 근거 ID: {claim.source_id}")
            elif claim.field not in record:
                issues.append(f"{claim.source_id}에 없는 필드: {claim.field}")
            else:
                issues.append(
                    f"근거와 다른 값: {claim.source_id}.{claim.field}={claim.value}"
                )
    return valid, issues


def generate_rag_answer(
    question: str,
    plan: QueryPlan,
    resolved: dict[str, Any],
    exact_facts: dict[str, Any],
    evidence: list[RetrievedEvidence],
    data: RagData,
    config: RagConfig,
) -> AnswerDraft:
    chain = ANSWER_PROMPT | make_llm(config).with_structured_output(
        AnswerDraft, method="json_schema"
    )
    return chain.invoke({
        "question": question,
        "plan": json.dumps(plan.model_dump(), ensure_ascii=False, indent=2),
        "resolved": json.dumps(resolved, ensure_ascii=False, indent=2),
        "retrieved_context": evidence_to_context(evidence),
        "exact_facts": json.dumps(exact_facts, ensure_ascii=False, indent=2),
        "policies": policy_context(data),
    })
