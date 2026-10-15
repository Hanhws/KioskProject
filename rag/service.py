"""외부 코드가 호출하는 카페 RAG 서비스 입구."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from langchain_community.vectorstores import FAISS

from .build_index import build_langchain_documents, build_or_load_vectorstore, resolve_exact_menu_terms
from .data_loader import RagConfig, RagData, load_rag_data, normalize_text, pipe_values
from .generator import (
    generate_rag_answer,
    normalize_query_plan,
    plan_question,
    validate_answer_claims,
    verified_recommendation_constraints,
)
from .menu_lookup import (
    exact_fact_bundle,
    filter_recommendation_candidates,
    find_unavailable_rule,
    rank_unavailable_fallback_candidates,
    resolve_option_ids,
    resolve_plan_menus,
    temperature_alternative_ids,
    variants_for_menu_id,
)
from .prompts import AnswerDraft, QueryPlan
from .retriever import retrieve_evidence, search_only, semantic_menu_candidate_ids


PERSISTED_RECOMMENDATION_FILTERS = (
    "temperature", "max_price_krw", "max_caffeine_mg", "decaf_required", "required_type",
    "required_category", "required_sweetness_level", "required_coffee_strength",
    "required_situations", "preferred_terms", "excluded_menu_ids",
)


def brief_recommendation_answer(menu_ids: list[str], data: RagData) -> str:
    suggestions = []
    for menu_id in menu_ids[:4]:
        menu = data.catalog_by_id.get(menu_id)
        if not menu:
            continue
        reasons = pipe_values(menu.get("flavor_tags"))[:2]
        if not reasons and menu.get("sweetness_level"):
            reasons = [f"당도 {menu['sweetness_level']}"]
        reason = f" ({', '.join(reasons)})" if reasons else ""
        suggestions.append(f"{menu['menu_name']}{reason}")
    if not suggestions:
        return "조건에 맞는 메뉴를 찾지 못했어요. 다른 조건으로 말씀해 주세요."
    return f"{', '.join(suggestions)}를 추천해요. 어떤 메뉴가 궁금하세요?"


def recommendation_choices(menu_ids: list[str], data: RagData) -> list[dict[str, Any]]:
    return [
        {
            "field": "menu_group_id",
            "value": menu_id,
            "label": data.catalog_by_id[menu_id]["menu_name"],
            "source_id": menu_id,
        }
        for menu_id in menu_ids
        if menu_id in data.catalog_by_id
    ]


def validated_ui_choices(
    draft: AnswerDraft,
    plan: QueryPlan,
    exact_facts: dict[str, Any],
    data: RagData,
) -> list[dict[str, Any]]:
    valid_source_ids = set(data.catalog_by_id) | set(data.document_by_id)
    choices = [
        choice.model_dump()
        for choice in draft.choices
        if choice.source_id is None or choice.source_id in valid_source_ids
    ]
    needs_examples = plan.intent == "ambiguous" or bool(plan.ambiguous_conditions)
    if needs_examples:
        existing_menu_ids = {
            choice["source_id"] for choice in choices
            if choice.get("source_id") in data.catalog_by_id
        }
        candidate_rows = [
            *exact_facts.get("menu_candidates_for_confirmation", []),
            *[
                item["catalog"]
                for item in exact_facts.get("recommendation_candidates_after_required_filters", [])
            ],
        ]
        for row in candidate_rows:
            menu_id = row.get("menu_group_id")
            if menu_id in data.catalog_by_id and menu_id not in existing_menu_ids:
                choices.append({
                    "field": "menu_group_id",
                    "value": menu_id,
                    "label": data.catalog_by_id[menu_id]["menu_name"],
                    "source_id": menu_id,
                })
                existing_menu_ids.add(menu_id)
            if len(existing_menu_ids) >= 4:
                break
        if not any(choice.get("value") == "free_text" for choice in choices):
            choices.append({
                "field": "free_text",
                "value": "free_text",
                "label": "원하는 조건을 직접 입력하기",
                "source_id": None,
            })
    return choices


@dataclass
class CafeRAGService:
    data: RagData
    vectorstore: FAISS
    config: RagConfig

    @classmethod
    def from_config(cls, config: RagConfig) -> "CafeRAGService":
        data = load_rag_data(config.data_dir)
        documents = build_langchain_documents(data)
        vectorstore, _ = build_or_load_vectorstore(documents, config)
        return cls(data=data, vectorstore=vectorstore, config=config)

    @property
    def retriever(self):
        return self.vectorstore.as_retriever(
            search_type="similarity",
            search_kwargs={"k": self.config.top_k},
        )

    def search(self, question: str, top_k: Optional[int] = None) -> list[dict[str, Any]]:
        return search_only(question, self.vectorstore, self.config, top_k)

    def ask(
        self,
        question: str,
        *,
        plan: Optional[QueryPlan | dict[str, Any]] = None,
        prior_conditions: Optional[dict[str, Any]] = None,
        recent_history: Optional[list[dict[str, str]]] = None,
        use_llm: Optional[bool] = None,
    ) -> dict[str, Any]:
        use_llm = self.config.run_llm if use_llm is None else use_llm
        if not question.strip():
            follow_up = (
                "궁금한 메뉴나 조건을 적어 주세요. 예: '아메리카노 가격', "
                "'HOT 3,500원 이하 부드러운 커피'. 다른 표현으로 직접 적어도 됩니다."
            )
            return {
                "answer": follow_up,
                "related_menu_ids": [],
                "evidence_document_ids": ["FAQ-007"] if "FAQ-007" in self.data.document_by_id else [],
                "retrieved_documents": [],
                "status": "needs_clarification",
                "follow_up_question": follow_up,
                "choices": [],
                "applied_conditions": prior_conditions or {},
                "query_plan": None,
                "claims": [],
                "grounding_validation": {"status": "not_applicable", "issues": []},
                "next_action": "continue_order",
                "resume_prompt": None,
                "error": None,
            }

        try:
            if plan is None:
                if use_llm:
                    planning_conditions = dict(prior_conditions or {})
                    planning_conditions.pop("explicit_constraint_updates", None)
                    planning_conditions.pop("situation_response", None)
                    previous_requested_fields = planning_conditions.pop(
                        "requested_fields", []
                    )
                    query_plan = plan_question(
                        question,
                        self.data,
                        self.config,
                        planning_conditions or None,
                        previous_requested_fields=(
                            previous_requested_fields
                            if isinstance(previous_requested_fields, list)
                            else []
                        ),
                        recent_history=(recent_history or [])[-6:],
                    )
                else:
                    query_plan = QueryPlan()
            else:
                query_plan = plan if isinstance(plan, QueryPlan) else QueryPlan.model_validate(plan)
        except Exception as error:
            return self._error_result("planner_error", error)

        intent_hint = (prior_conditions or {}).get("_dialogue_intent")
        if query_plan.intent in {"general", "ambiguous"} and (
            intent_hint == "recommend" or (intent_hint != "information" and query_plan.inferred_preferences)
        ):
            query_plan.intent = "recommend"
        if query_plan.intent == "ambiguous" and (prior_conditions or {}).get("clarification_count", 0) >= 2:
            query_plan.intent = "recommend"
        query_plan = normalize_query_plan(query_plan, self.data, question)
        hard_constraints = None
        if query_plan.intent == "recommend":
            hard_constraints = verified_recommendation_constraints(
                query_plan, self.data, question, prior_conditions, trusted_plan=plan is not None,
            )
            previous = prior_conditions or {}
            query_plan.excluded_menu_ids = list(dict.fromkeys([
                *previous.get("excluded_menu_ids", []), *query_plan.excluded_menu_ids,
            ]))
            query_plan.clarification_count = max(
                query_plan.clarification_count, int(previous.get("clarification_count", 0))
            )
            named_exclusions = [term for term in query_plan.excluded_allergens
                                if plan is not None or (
                                    normalize_text(term) in normalize_text(question)
                                    and any(word in normalize_text(question) for word in ["못먹", "알레르기", "알러지", "제외", "빼", "없는", "없이"])
                                )]
            query_plan.excluded_allergens = list(dict.fromkeys([
                *previous.get("excluded_allergens", []), *named_exclusions,
            ]))

        unavailable_rule = find_unavailable_rule(question, self.data)
        if not unavailable_rule and query_plan.unavailable_term:
            unavailable_rule = next(
                (
                    row for row in self.data.tables["unavailable_menu_alternatives"]
                    if normalize_text(row["requested_menu"])
                    == normalize_text(query_plan.unavailable_term)
                ),
                None,
            )
        if unavailable_rule:
            query_plan.unavailable_term = unavailable_rule["requested_menu"]
            if query_plan.intent in {"general", "menu_info", "ambiguous"}:
                query_plan.intent = "unavailable"

        resolved = resolve_plan_menus(question, query_plan, self.data)
        recommendation_ids = (
            filter_recommendation_candidates(self.data, query_plan)
            if query_plan.intent == "recommend" and not (hard_constraints or {}).get("_unsupported")
            else []
        )
        unavailable_fallback_ids: list[str] = []
        if (
            query_plan.intent == "unavailable"
            and not unavailable_rule
            and not resolved["exact_menu_ids"]
        ):
            unavailable_fallback_ids = rank_unavailable_fallback_candidates(
                self.data, query_plan, limit=3
            )
            if not unavailable_fallback_ids:
                eligible_ids = filter_recommendation_candidates(self.data, query_plan)
                unavailable_fallback_ids = semantic_menu_candidate_ids(
                    question,
                    self.vectorstore,
                    self.data,
                    excluded_menu_ids=query_plan.excluded_menu_ids,
                    allowed_menu_ids=eligible_ids,
                    limit=3,
                )
        temperature_alternative_menu_ids = temperature_alternative_ids(
            self.data,
            resolved["exact_menu_ids"],
            query_plan.temperature,
            limit=3,
        )
        if temperature_alternative_menu_ids and query_plan.temperature:
            temperature_eligible_ids = [
                menu_id for menu_id in self.data.catalog_by_id
                if menu_id not in resolved["exact_menu_ids"]
                and any(
                    variant["temperature"] == query_plan.temperature
                    for variant in variants_for_menu_id(self.data, menu_id)
                )
            ]
            semantic_temperature_ids = semantic_menu_candidate_ids(
                question,
                self.vectorstore,
                self.data,
                excluded_menu_ids=resolved["exact_menu_ids"],
                allowed_menu_ids=temperature_eligible_ids,
                limit=3,
            )
            if semantic_temperature_ids:
                temperature_alternative_menu_ids = semantic_temperature_ids
        option_ids = resolve_option_ids(query_plan.option_terms, self.data)

        try:
            evidence = retrieve_evidence(
                question,
                query_plan,
                resolved,
                recommendation_ids,
                option_ids,
                unavailable_fallback_ids,
                temperature_alternative_menu_ids,
                self.data,
                self.vectorstore,
                self.config,
            )
        except Exception as error:
            return self._error_result("retrieval_error", error, query_plan)

        exact_facts = exact_fact_bundle(
            self.data,
            resolved["exact_menu_ids"],
            option_ids,
            query_plan,
            recommendation_ids,
            resolved["candidate_menu_ids"],
            unavailable_fallback_ids,
            temperature_alternative_menu_ids,
        )
        retrieved_rows = [
            {
                "document_id": item.document.metadata["document_id"],
                "title": item.document.metadata["title"],
                "document_type": item.document.metadata["document_type"],
                "source_type": item.document.metadata["source_type"],
                "distance": item.distance,
                "reason": item.reason,
                "content": item.document.metadata.get("original_content"),
            }
            for item in evidence
        ]

        if not use_llm:
            return {
                "answer": "LLM 호출이 꺼져 있어 검색·정확 조회 결과까지만 반환했습니다.",
                "related_menu_ids": resolved["exact_menu_ids"],
                "evidence_document_ids": [row["document_id"] for row in retrieved_rows],
                "retrieved_documents": retrieved_rows,
                "status": "retrieval_only",
                "follow_up_question": None,
                "choices": [],
                "applied_conditions": query_plan.model_dump(exclude_none=True),
                "query_plan": query_plan.model_dump(),
                "claims": [],
                "grounding_validation": {"status": "not_run", "issues": []},
                "next_action": None,
                "resume_prompt": query_plan.pending_question,
                "exact_facts": exact_facts,
                "error": None,
            }

        try:
            draft = generate_rag_answer(
                question,
                query_plan,
                resolved,
                exact_facts,
                evidence,
                self.data,
                self.config,
            )
        except Exception as error:
            return self._error_result("model_error", error, query_plan, retrieved_rows)

        allowed_evidence_ids = {
            item.document.metadata["document_id"] for item in evidence
        } | set(self.data.policy_by_id)
        valid_evidence_ids = sorted(
            set(draft.evidence_document_ids) & allowed_evidence_ids & set(self.data.document_by_id)
        )
        deterministic_menu_ids = set(resolved["exact_menu_ids"])
        registered_alternative_ids: set[str] = set()
        if query_plan.intent == "unavailable" and exact_facts.get("unavailable_menu_rule"):
            alternative_ids, _ = resolve_exact_menu_terms(
                pipe_values(exact_facts["unavailable_menu_rule"].get("alternatives")),
                self.data,
            )
            registered_alternative_ids.update(alternative_ids)
            deterministic_menu_ids.update(registered_alternative_ids)
        deterministic_menu_ids.update(unavailable_fallback_ids)
        deterministic_menu_ids.update(temperature_alternative_menu_ids)

        allowed_menu_ids = set(self.data.catalog_by_id)
        if query_plan.intent == "recommend":
            allowed_menu_ids = set(recommendation_ids)
            deterministic_menu_ids.intersection_update(allowed_menu_ids)
        elif query_plan.intent == "unavailable":
            allowed_menu_ids = registered_alternative_ids | set(unavailable_fallback_ids)
        if query_plan.intent != "recommend":
            allowed_menu_ids.update(resolved["exact_menu_ids"])
            allowed_menu_ids.update(temperature_alternative_menu_ids)
        rejected_menu_ids = sorted(set(draft.related_menu_ids) - allowed_menu_ids)
        validated_draft_menu_ids = (
            set(draft.related_menu_ids)
            & allowed_menu_ids
            & set(self.data.catalog_by_id)
        )
        if query_plan.intent == "recommend":
            valid_menu_ids = list(dict.fromkeys([
                *[
                    menu_id
                    for menu_id in draft.related_menu_ids
                    if menu_id in validated_draft_menu_ids
                ],
                *sorted(deterministic_menu_ids),
            ]))[:4]
        else:
            valid_menu_ids = sorted(
                validated_draft_menu_ids | deterministic_menu_ids
            )
        valid_choices = validated_ui_choices(draft, query_plan, exact_facts, self.data)
        valid_claims, grounding_issues = validate_answer_claims(draft.claims, self.data)
        if rejected_menu_ids:
            grounding_issues.append(
                f"필수 조건 또는 대안 후보 밖의 메뉴 ID: {rejected_menu_ids}"
            )
        stop_clarifying = query_plan.intent == "recommend" and query_plan.clarification_count >= 2
        is_brief_recommendation = query_plan.intent == "recommend" and (
            stop_clarifying or (query_plan.excluded_allergens and not recommendation_ids)
            or (not query_plan.requested_fields and (query_plan.inferred_preferences or hard_constraints or query_plan.situation_response))
            or (not query_plan.requested_fields and draft.status != "needs_clarification")
        )
        if is_brief_recommendation:
            recommended_menu_ids = [
                menu_id
                for menu_id in draft.related_menu_ids
                if menu_id in recommendation_ids
            ]
            if query_plan.inferred_preferences:
                # 모델이 고른 임의 순서가 실제 속성으로 계산한 우선순위를 덮어쓰지 않게 한다.
                recommended_menu_ids = recommendation_ids
            if not recommended_menu_ids:
                recommended_menu_ids = recommendation_ids
            recommended_menu_ids = list(dict.fromkeys(recommended_menu_ids))[:3 if stop_clarifying else 4]
            answer = brief_recommendation_answer(recommended_menu_ids, self.data)
            if not recommended_menu_ids:
                answer = ("말씀하신 조건은 메뉴 정보에서 확인하기 어려워요. 다른 조건으로 찾아볼까요?"
                          if (hard_constraints or {}).get("_unsupported") else
                          "말씀하신 조건에 맞는 다른 메뉴가 없어요. 조건을 하나 바꿔서 찾아볼까요?")
            if query_plan.situation_response:
                answer = query_plan.situation_response.strip() + "\n" + answer
            status = "ok" if recommended_menu_ids else "no_candidates"
            valid_menu_ids = recommended_menu_ids
            valid_evidence_ids = [
                menu_id
                for menu_id in recommended_menu_ids
                if menu_id in self.data.document_by_id
            ]
            valid_claims = []
            grounding_issues = []
            valid_choices = recommendation_choices(recommended_menu_ids, self.data)
            if not recommended_menu_ids:
                valid_choices = [{"field": "free_text", "value": "free_text", "label": "조건 바꾸기", "source_id": None}]
            next_action = "choose_menu" if recommended_menu_ids else None
        elif grounding_issues:
            answer = (
                "일부 정보는 정확히 확인되지 않아 제외했어요. "
                "확인된 메뉴 정보만 기준으로 다시 안내해 드릴게요."
            )
            status = "partial"
            next_action = draft.next_action
        else:
            answer = draft.answer
            status = draft.status
            next_action = draft.next_action

        if query_plan.intent == "general" and intent_hint == "information":
            valid_menu_ids = []
            valid_choices = [choice for choice in valid_choices if choice.get("field") == "free_text"]
            if next_action == "choose_menu":
                next_action = None

        if query_plan.intent == "recommend":
            if status == "ok" and valid_menu_ids:
                valid_choices = recommendation_choices(valid_menu_ids, self.data)
                next_action = "choose_menu"
            else:
                valid_choices = [
                    choice
                    for choice in valid_choices
                    if (
                        choice.get("value")
                        if choice.get("field") == "menu_group_id"
                        else choice.get("source_id")
                    ) not in self.data.catalog_by_id
                ]
                if next_action == "choose_menu":
                    next_action = None
            uncertain_names = [
                self.data.catalog_by_id[menu_id]["menu_name"] for menu_id in valid_menu_ids
                if query_plan.excluded_allergens and any(
                    not variant.get("allergens") for variant in variants_for_menu_id(self.data, menu_id)
                )
            ]
            if uncertain_names:
                excluded = ', '.join(query_plan.excluded_allergens)
                answer += (f"\n{', '.join(uncertain_names)}는 알레르기 정보가 없어 "
                           f"{excluded} 포함 여부를 직원에게 확인해 주세요.")
            if status == "ok" and valid_menu_ids and "우유" in query_plan.excluded_allergens:
                answer = "우유가 포함된 것으로 표시된 메뉴를 제외하고 추천했어요.\n" + answer
        applied_conditions = query_plan.model_dump(exclude_none=True, exclude={"explicit_constraint_updates", "constraint_evidence", "situation_response"})
        if hard_constraints is not None:
            applied_conditions["hard_constraints"] = hard_constraints
        if query_plan.intent != "recommend" and prior_conditions:
            # 정보·잡담을 사이에 끼워도 다음 추천의 필수 조건을 지우지 않는다.
            for field in (*PERSISTED_RECOMMENDATION_FILTERS, "excluded_allergens", "clarification_count", "hard_constraints"):
                if field in prior_conditions:
                    applied_conditions[field] = prior_conditions[field]
        return {
            "answer": answer,
            "related_menu_ids": valid_menu_ids,
            "evidence_document_ids": valid_evidence_ids,
            "retrieved_documents": retrieved_rows,
            "status": status,
            "follow_up_question": None if stop_clarifying else draft.follow_up_question,
            "choices": valid_choices,
            "applied_conditions": applied_conditions,
            "query_plan": query_plan.model_dump(),
            "claims": valid_claims,
            "grounding_validation": {
                "status": "blocked" if grounding_issues else "verified",
                "issues": grounding_issues,
            },
            "next_action": next_action,
            "resume_prompt": draft.resume_prompt or (
                query_plan.pending_question
                if query_plan.intent == "unrelated" else None
            ),
            "error": None,
        }

    def _error_result(
        self,
        status: str,
        error: Exception,
        plan: Optional[QueryPlan] = None,
        retrieved_rows: Optional[list[dict[str, Any]]] = None,
    ) -> dict[str, Any]:
        return {
            "answer": "요청 처리 중 시스템 오류가 발생했습니다. 메뉴 정보 부족과는 다른 오류입니다.",
            "related_menu_ids": [],
            "evidence_document_ids": [],
            "retrieved_documents": retrieved_rows or [],
            "status": status,
            "follow_up_question": None,
            "choices": [],
            "applied_conditions": plan.model_dump(exclude_none=True) if plan else {},
            "query_plan": plan.model_dump() if plan else None,
            "claims": [],
            "grounding_validation": {"status": "error", "issues": []},
            "next_action": None,
            "resume_prompt": plan.pending_question if plan else None,
            "error": f"{type(error).__name__}: {error}",
        }
