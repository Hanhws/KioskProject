"""질문 계획 생성, 답변 생성, 근거 값 검증."""

from __future__ import annotations

import json
import os
import re
from copy import deepcopy
from typing import Any, Iterable, Optional

from langchain_openai import ChatOpenAI

from .data_loader import RagConfig, RagData, normalize_text
from .menu_lookup import recommendation_attributes, variants_for_menu_id
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
    previous_requested_fields: Optional[list[str]] = None,
    recent_history: Optional[list[dict[str, str]]] = None,
) -> QueryPlan:
    planner = PLANNER_PROMPT | make_llm(config).with_structured_output(
        QueryPlan, method="json_schema"
    )
    return planner.invoke({
        "question": question,
        "prior_conditions": json.dumps(prior_conditions or {}, ensure_ascii=False),
        "previous_requested_fields": json.dumps(
            previous_requested_fields or [], ensure_ascii=False
        ),
        "recent_history": json.dumps(
            recent_history or [], ensure_ascii=False
        ),
        "types": sorted({menu["type"] for menu in data.tables["menu_catalog"]}),
        "categories": sorted({menu["category"] for menu in data.tables["menu_catalog"]}),
        "recommendation_attributes": json.dumps(recommendation_attributes(data), ensure_ascii=False),
    })


HIGH_SWEETNESS_PATTERNS = (
    "달달한", "달콤한", "단거", "단음료", "당충전", "디저트같은", "엄청단",
)
NEGATIVE_SWEETNESS_PATTERNS = (
    "달달하지않", "달콤하지않", "달지않", "안단", "덜단",
    "단맛적은", "단건싫", "단거말고", "단음료말고",
)
EXPLICIT_RECOMMENDATION_DETAIL_PATTERNS = (
    "가격", "얼마", "카페인", "칼로리", "당류", "영양", "알레르기", "용량", "성분",
    "설명", "특징", "어떤맛", "맛은", "맛도", "비교",
)


def _contains_any(normalized_text: str, patterns: tuple[str, ...]) -> bool:
    return any(normalize_text(pattern) in normalized_text for pattern in patterns)


def normalize_query_plan(
    plan: QueryPlan,
    data: RagData,
    question: str = "",
) -> QueryPlan:
    """LLM 계획의 명백한 오해석만 기존 QueryPlan 필드 안에서 보정한다."""
    normalized_question = normalize_text(question)
    vocabulary = recommendation_attributes(data)
    allowed_preferences = {value for values in vocabulary.values() for value in values}
    plan.inferred_preferences = list(dict.fromkeys(
        term for term in plan.inferred_preferences if term in allowed_preferences
    ))[:6]
    if plan.intent == "recommend" and (plan.inferred_preferences or plan.situation_response):
        # 감정 원문을 필수 문자열 일치로 취급하지 않는다. 의미 해석은 LLM이 담당한다.
        searchable = normalize_text(" ".join(str(value or "") for menu in data.catalog_by_id.values() for value in menu.values()))
        plan.preferred_terms = [term for term in plan.preferred_terms if normalize_text(term) and normalize_text(term) in searchable]
        if "required_situations" not in plan.explicit_constraint_updates:
            inferred_situations = [term for term in plan.required_situations if term in vocabulary["recommended_situations"]]
            plan.inferred_preferences = list(dict.fromkeys([*plan.inferred_preferences, *inferred_situations]))[:6]
            plan.required_situations = []
    if plan.situation_response and any(term in plan.situation_response for term in (
        "도움", "치료", "회복", "개선", "완화", "해소", "낫게", "풀릴",
    )):
        # 공감 문장에 검증되지 않은 감정·건강 개선 약속이 끼면 노출하지 않는다.
        plan.situation_response = None
    if plan.intent == "recommend" and re.search(
        r"우유(?:를|가|는)?(?:못먹|못마|안먹|안마|없는|없이|빼|제외|알레르기|알러지)", normalized_question
    ):
        plan.excluded_allergens = list(dict.fromkeys([*plan.excluded_allergens, "우유"]))
        plan.preferred_terms = [term for term in plan.preferred_terms if "우유" not in term]
    positive_sweetness = _contains_any(
        normalized_question, HIGH_SWEETNESS_PATTERNS
    )
    negative_sweetness = _contains_any(
        normalized_question, NEGATIVE_SWEETNESS_PATTERNS
    )
    if plan.intent == "recommend" and positive_sweetness and not negative_sweetness:
        plan.required_sweetness_level = "높음"
        plan.preferred_terms = [
            term
            for term in plan.preferred_terms
            if not _contains_any(normalize_text(term), HIGH_SWEETNESS_PATTERNS)
        ]

    if (
        plan.intent == "recommend"
        and not _contains_any(
            normalized_question, EXPLICIT_RECOMMENDATION_DETAIL_PATTERNS
        )
    ):
        plan.requested_fields = []

    if plan.intent == "compare" and not plan.requested_fields:
        plan.requested_fields = ["맛", "가격", "커피 진하기"]
    plan.excluded_menu_ids = sorted(
        set(plan.excluded_menu_ids) & set(data.catalog_by_id)
    )
    plan.clarification_count = max(0, plan.clarification_count)
    return plan


HARD_RECOMMENDATION_FIELDS = (
    "temperature", "max_price_krw", "max_caffeine_mg", "decaf_required", "required_type",
    "required_category", "required_sweetness_level", "required_coffee_strength",
    "required_situations", "preferred_terms",
)


def verified_recommendation_constraints(
    plan: QueryPlan, data: RagData, question: str, prior: Optional[dict[str, Any]] = None,
    *, trusted_plan: bool = False,
) -> dict[str, Any]:
    """사용자 원문으로 확인한 제한만 필터에 두고 추론은 순위 조건으로 옮긴다.

    hard_constraints는 서버가 생성하는 누적 조건이다. 이전 버전의 상태는 유효한
    카탈로그 값만 이관하며 가격·카페인·알레르기 제한을 임의로 완화하지 않는다.
    """
    prior = prior or {}
    vocabulary = recommendation_attributes(data)
    aliases = {"required_type": "type", "required_category": "category",
               "required_sweetness_level": "sweetness_level", "required_coffee_strength": "coffee_strength",
               "required_situations": "recommended_situations"}
    searchable = normalize_text(" ".join(str(v or "") for menu in data.catalog_by_id.values() for v in menu.values()))

    def valid(field, value):
        if value is None or value == []:
            return False
        if field in aliases:
            if field == "required_situations":
                return isinstance(value, list) and all(v in vocabulary[aliases[field]] for v in value)
            return isinstance(value, str) and value in vocabulary[aliases[field]]
        if field == "preferred_terms":
            return isinstance(value, list) and all(normalize_text(v) and normalize_text(v) in searchable for v in value)
        if field == "temperature":
            return value in {"HOT", "ICE"}
        if field in {"max_price_krw", "max_caffeine_mg"}:
            return isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0
        return isinstance(value, bool)

    def grounded(field, value, quote):
        text = normalize_text(quote)
        if not text or text not in normalize_text(question):
            return False
        if field in {"max_price_krw", "max_caffeine_mg"}:
            unit = "원" if field == "max_price_krw" else "mg|밀리그램"
            numeric_text = re.sub(r"[,\s]", "", quote).lower()
            for word, number in {"일": "1", "이": "2", "삼": "3", "사": "4", "오": "5", "육": "6", "칠": "7", "팔": "8", "구": "9", "한": "1", "두": "2"}.items():
                numeric_text = re.sub(word + r"(?=천|만)", number, numeric_text)
            amounts = re.findall(r"(\d+(?:\.\d+)?)(천|만)?(?:" + unit + r")", numeric_text)
            return any(float(number) * {"": 1, "천": 1000, "만": 10000}[scale] == value for number, scale in amounts)
        if field == "temperature":
            return bool(re.search(r"따뜻|뜨거|핫|hot" if value == "HOT" else r"아이스|차갑|차가|시원|ice", text))
        if field == "decaf_required":
            return "디카페인" in text
        if field == "required_type":
            positive = bool(re.search(r"음료|마실|마시" if value == "음료" else r"디저트|간식|먹을", text))
            return positive and not bool(re.search(normalize_text(value) + r"(?:는|은)?(?:말고|빼|제외)", text))
        if field == "required_coffee_strength" and value == "없음":
            return bool(re.search(r"커피.*(?:말고|빼|제외|싫|안|않|별로|없는|없이)", text))
        if field == "required_coffee_strength" and value == "진함":
            return bool(re.search(r"진한|진하|진함|진하게", text))
        if field == "required_category" and value == "티":
            return "차" in text or "티" in text
        if field == "required_category" and value == "커피" and re.search(r"커피.*(?:말고|빼|제외|싫|않|별로)", text):
            return False
        if field == "required_sweetness_level":
            return bool(re.search({"높음": r"달달|달콤|단거|단음료|당충전", "낮음": r"달지|덜달|안단|단건싫", "보통": r"보통|적당"}[value], text))
        values = value if isinstance(value, list) else [value]
        return all(normalize_text(v) in text for v in values)

    recorded = prior.get("hard_constraints")
    old = recorded if isinstance(recorded, dict) else prior
    hard = {field: old[field] for field in HARD_RECOMMENDATION_FIELDS if field in old and valid(field, old[field])}
    defaults = QueryPlan()
    evidence = {row.field: row.quote for row in plan.constraint_evidence}
    for field in HARD_RECOMMENDATION_FIELDS:
        value = getattr(plan, field)
        quote = evidence.get(field, "")
        quoted = normalize_text(quote)
        if (not valid(field, value) and isinstance(value, str) and quoted
                and quoted in normalize_text(question) and normalize_text(value) in quoted
                and field in plan.explicit_constraint_updates):
            # 명시했지만 카탈로그로 확인할 수 없는 제한은 조용히 없애지 않고 확인한다.
            hard.setdefault("_unsupported", []).append(quote)
        accepted = valid(field, value) and (trusted_plan or grounded(field, value, quote))
        if accepted and (field not in hard or trusted_plan or field in plan.explicit_constraint_updates):
            hard[field] = value
        elif value is not None and value != [] and field not in hard:
            values = value if isinstance(value, list) else [value]
            allowed = {v for items in vocabulary.values() for v in items}
            plan.inferred_preferences.extend(v for v in values if isinstance(v, str) and v in allowed)
        setattr(plan, field, hard.get(field, getattr(defaults, field)))
    plan.inferred_preferences = list(dict.fromkeys(plan.inferred_preferences))[:6]
    return hard


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
    answer_exact_facts = scope_exact_facts_for_answer(plan, resolved, exact_facts)
    answer_evidence = evidence_to_context(evidence)
    if plan.intent == "menu_info" and plan.requested_fields and resolved.get("exact_menu_ids"):
        # 정확한 메뉴를 특정해 한두 항목만 물은 경우에는 전체 문서를 함께 주지 않는다.
        # 전체 문서의 가격·용량·카페인 등이 답변에 불필요하게 따라붙는 것을 막고,
        # 아래에서 요청 항목만 남긴 정확 조회 사실을 답변 근거로 사용한다.
        answer_evidence = "정확 조회 사실의 요청 항목만 사용하세요."
    chain = ANSWER_PROMPT | make_llm(config).with_structured_output(
        AnswerDraft, method="json_schema"
    )
    return chain.invoke({
        "question": question,
        "plan": json.dumps(plan.model_dump(), ensure_ascii=False, indent=2),
        "resolved": json.dumps(resolved, ensure_ascii=False, indent=2),
        "retrieved_context": answer_evidence,
        "exact_facts": json.dumps(answer_exact_facts, ensure_ascii=False, indent=2),
        "policies": policy_context(data),
    })


def scope_exact_facts_for_answer(
    plan: QueryPlan,
    resolved: dict[str, Any],
    exact_facts: dict[str, Any],
) -> dict[str, Any]:
    """정확한 단일 메뉴 정보 질문에는 사용자가 요청한 필드만 LLM에 노출한다."""
    if (
        plan.intent != "menu_info"
        or not plan.requested_fields
        or not resolved.get("exact_menu_ids")
    ):
        return exact_facts

    normalized_fields = {normalize_text(field) for field in plan.requested_fields}
    catalog_fields = {"menu_group_id", "menu_name", "type", "category"}
    variant_fields = {
        "variant_id", "base_menu_name", "display_name", "temperature", "data_source",
    }

    field_aliases = {
        "price_krw": ("가격", "price"),
        "price_summary": ("가격", "price"),
        "volume_ml": ("용량", "volume", "ml"),
        "calories_kcal": ("칼로리", "calorie", "kcal"),
        "sugar_g": ("당류", "당", "sugar"),
        "protein_g": ("단백질", "protein"),
        "saturated_fat_g": ("포화지방", "saturatedfat"),
        "caffeine_mg": ("카페인", "caffeine"),
        "sodium_mg": ("나트륨", "sodium"),
        "cholesterol_mg": ("콜레스테롤", "cholesterol"),
        "carbohydrate_g": ("탄수화물", "carbohydrate"),
        "fat_g": ("지방", "fat"),
        "trans_fat_g": ("트랜스지방", "transfat"),
        "allergens": ("알레르기", "알러지", "allergen"),
        "allergen_note": ("알레르기", "알러지", "allergen"),
        "decaf_available": ("디카페인", "decaf"),
        "size_policy": ("사이즈", "size"),
        "available_temperatures": ("온도", "hot", "ice", "따뜻", "아이스"),
        "short_description": ("설명", "특징", "맛", "description", "flavor"),
        "rich_description": ("설명", "특징", "맛", "description", "flavor"),
        "flavor_tags": ("설명", "특징", "맛", "description", "flavor"),
        "sweetness_level": ("설명", "특징", "맛", "당도", "sweet"),
        "coffee_strength": ("설명", "특징", "맛", "커피진하기", "strength"),
        "acidity_level": ("설명", "특징", "맛", "산미", "acidity"),
        "texture": ("설명", "특징", "맛", "질감", "texture"),
    }
    nutrition_requested = any(
        token in field
        for field in normalized_fields
        for token in ("영양", "nutrition")
    )
    description_requested = any(
        token in field
        for field in normalized_fields
        for token in ("설명", "description")
    )

    for key, aliases in field_aliases.items():
        if any(
            normalize_text(alias) in field or field in normalize_text(alias)
            for field in normalized_fields
            for alias in aliases
        ):
            catalog_fields.add(key)
            variant_fields.add(key)
    if nutrition_requested:
        variant_fields.update({
            "calories_kcal", "sugar_g", "protein_g", "saturated_fat_g",
            "caffeine_mg", "sodium_mg", "cholesterol_mg", "carbohydrate_g",
            "fat_g", "trans_fat_g",
        })
    if description_requested:
        catalog_fields.update({
            "short_description", "rich_description", "flavor_tags",
            "sweetness_level", "coffee_strength", "acidity_level", "texture",
            "price_summary",
        })
        variant_fields.add("price_krw")

    scoped = deepcopy(exact_facts)
    scoped["resolved_menus"] = [
        {
            "catalog": {
                key: value
                for key, value in item.get("catalog", {}).items()
                if key in catalog_fields
            },
            "variants": [
                {
                    key: value
                    for key, value in variant.items()
                    if key in variant_fields
                }
                for variant in item.get("variants", [])
            ],
        }
        for item in exact_facts.get("resolved_menus", [])
    ]
    return scoped
