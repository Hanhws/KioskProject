"""메뉴·가격·조건·옵션·페어링·대안의 정확 조회."""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any, Iterable, Optional

from .build_index import option_relation, resolve_exact_menu_terms
from .data_loader import RagData, normalize_text, pipe_values
from .prompts import QueryPlan


def find_unavailable_rule(question: str, data: RagData) -> Optional[dict[str, Any]]:
    normalized_question = normalize_text(question)
    matches = [
        row
        for row in data.tables["unavailable_menu_alternatives"]
        if normalize_text(row.get("requested_menu"))
        and normalize_text(row["requested_menu"]) in normalized_question
    ]
    return max(matches, key=lambda row: len(normalize_text(row["requested_menu"])), default=None)


def detect_menu_ids_in_question(question: str, data: RagData) -> list[str]:
    normalized_question = normalize_text(question)
    found: set[str] = set()
    for alias, menu_ids in data.alias_to_menu_ids.items():
        if len(alias) >= 2 and alias in normalized_question:
            found.update(menu_ids)
    return sorted(found)


def resolve_menu_term(term: str, data: RagData) -> tuple[list[str], list[str]]:
    normalized = normalize_text(term)
    exact = data.alias_to_menu_ids.get(normalized, [])
    if exact:
        return exact, []
    candidates = []
    if normalized:
        for menu_id, menu in data.catalog_by_id.items():
            names = [menu["menu_name"], *pipe_values(menu.get("aliases"))]
            if any(
                normalized in normalize_text(name) or normalize_text(name) in normalized
                for name in names
                if len(normalize_text(name)) >= 2
            ):
                candidates.append(menu_id)
    if not candidates and len(normalized) >= 3:
        fuzzy_scores: list[tuple[float, str]] = []
        for menu_id, menu in data.catalog_by_id.items():
            names = [menu["menu_name"], *pipe_values(menu.get("aliases"))]
            score = max(
                (
                    SequenceMatcher(None, normalized, normalize_text(name)).ratio()
                    for name in names
                    if normalize_text(name)
                ),
                default=0.0,
            )
            if score >= 0.76:
                fuzzy_scores.append((score, menu_id))
        if fuzzy_scores:
            best = max(score for score, _ in fuzzy_scores)
            candidates.extend(
                menu_id for score, menu_id in fuzzy_scores if score >= best - 0.05
            )
    return [], sorted(set(candidates))


def resolve_plan_menus(
    question: str, plan: QueryPlan, data: RagData
) -> dict[str, Any]:
    exact_ids = set(detect_menu_ids_in_question(question, data))
    candidate_ids: set[str] = set()
    unresolved_terms = []
    for term in plan.menu_terms:
        exact, candidates = resolve_menu_term(term, data)
        exact_ids.update(exact)
        candidate_ids.update(candidates)
        if not exact and not candidates:
            unresolved_terms.append(term)
    candidate_ids -= exact_ids
    return {
        "exact_menu_ids": sorted(exact_ids),
        "candidate_menu_ids": sorted(candidate_ids),
        "unresolved_menu_terms": unresolved_terms,
    }


def variants_for_menu_id(data: RagData, menu_id: str) -> list[dict[str, Any]]:
    menu = data.catalog_by_id[menu_id]
    return data.variants_by_menu_name.get(menu["menu_name"], [])


def meets_allergen_constraint(variant: dict[str, Any], plan: QueryPlan) -> bool:
    if not plan.excluded_allergens:
        return True
    # 명시된 성분만 제외한다. 빈 칸의 불확실성은 추천 응답에서 별도로 안내한다.
    allergens = variant.get("allergens")
    return not any(
        normalize_text(term) in normalize_text(allergens) for term in plan.excluded_allergens
    )


def filter_recommendation_candidates(data: RagData, plan: QueryPlan) -> list[str]:
    candidates = []
    preferred_terms = [
        normalize_text(term) for term in plan.preferred_terms if normalize_text(term)
    ]
    for menu_id, menu in data.catalog_by_id.items():
        if menu_id in plan.excluded_menu_ids:
            continue
        if plan.required_type and menu["type"] != plan.required_type:
            continue
        if plan.required_category and menu["category"] != plan.required_category:
            continue
        if (
            plan.required_sweetness_level
            and menu.get("sweetness_level") != plan.required_sweetness_level
        ):
            continue
        if (
            plan.required_coffee_strength
            and menu.get("coffee_strength") != plan.required_coffee_strength
        ):
            continue
        if preferred_terms:
            searchable = normalize_text(" ".join(
                str(menu.get(field) or "")
                for field in [
                    "category", "menu_name", "aliases", "flavor_tags",
                    "texture", "recommended_for", "recommended_situations",
                    "rich_description",
                ]
            ))
            if not any(term in searchable for term in preferred_terms):
                continue
        if plan.required_situations:
            situations = {
                normalize_text(value)
                for value in pipe_values(menu.get("recommended_situations"))
            }
            if not any(
                normalize_text(required) in situation
                or situation in normalize_text(required)
                for required in plan.required_situations
                for situation in situations
            ):
                continue
        variants = variants_for_menu_id(data, menu_id)
        # 한 온도라도 제외 성분이 명시돼 있으면 해당 메뉴 전체를 추천에서 제외한다.
        if plan.excluded_allergens and any(not meets_allergen_constraint(row, plan) for row in variants):
            continue
        eligible_variants = []
        for variant in variants:
            if plan.temperature and variant["temperature"] != plan.temperature:
                continue
            if not meets_allergen_constraint(variant, plan):
                continue
            if plan.max_price_krw is not None:
                price = variant.get("price_krw")
                if price is None or price > plan.max_price_krw:
                    continue
            if plan.max_caffeine_mg is not None:
                caffeine = variant.get("caffeine_mg")
                if caffeine is None or caffeine > plan.max_caffeine_mg:
                    continue
            if plan.decaf_required is True and variant.get("decaf_available") != "Y":
                continue
            eligible_variants.append(variant)
        if eligible_variants:
            candidates.append(menu_id)
    return candidates


def resolve_option_ids(option_terms: Iterable[str], data: RagData) -> list[str]:
    found = set()
    for term in option_terms:
        target = normalize_text(term)
        for option_id, option in data.option_by_id.items():
            expressions = [
                option["option_name"],
                *pipe_values(option.get("customer_phrase_examples")),
            ]
            if any(
                target == normalize_text(expression)
                or target in normalize_text(expression)
                or normalize_text(expression) in target
                for expression in expressions
                if normalize_text(expression)
            ):
                found.add(option_id)
    return sorted(found)


def evaluate_option_for_menu(
    data: RagData,
    menu_id: str,
    option_id: str,
    requested_quantity: Optional[int],
) -> dict[str, Any]:
    option = data.option_by_id[option_id]
    related_menu_ids, unresolved_terms = option_relation(option, data)
    if menu_id in related_menu_ids:
        if (
            requested_quantity is not None
            and option.get("max_quantity") is not None
            and requested_quantity > option["max_quantity"]
        ):
            status = "quantity_exceeded"
        else:
            status = "applicable"
    elif unresolved_terms and not related_menu_ids:
        status = "unknown"
    else:
        status = "not_applicable"
    return {
        "menu_group_id": menu_id,
        "menu_name": data.catalog_by_id[menu_id]["menu_name"],
        "option_id": option_id,
        "option_name": option["option_name"],
        "status": status,
        "requested_quantity": requested_quantity,
        "max_quantity": option.get("max_quantity"),
        "price_delta_krw_per_unit": option.get("price_delta_krw"),
        "applies_to": option.get("applies_to"),
        "excluded_menus": option.get("excluded_menus"),
        "unresolved_rule_terms": unresolved_terms,
    }


def build_menu_listing(data: RagData, plan: QueryPlan) -> Optional[dict[str, Any]]:
    if not plan.list_menu:
        return None
    rows = [
        menu for menu in data.tables["menu_catalog"]
        if (not plan.list_menu_type or menu["type"] == plan.list_menu_type)
        and (not plan.list_category or menu["category"] == plan.list_category)
    ]
    grouped: dict[str, list[dict[str, str]]] = {}
    for menu in rows:
        grouped.setdefault(menu["category"], []).append({
            "menu_group_id": menu["menu_group_id"],
            "menu_name": menu["menu_name"],
        })
    categories_only = plan.list_menu_type == "음료" and not plan.list_category
    return {
        "mode": "categories" if categories_only else "grouped_menus",
        "menu_type": plan.list_menu_type,
        "selected_category": plan.list_category,
        "categories": sorted(grouped),
        "groups": {} if categories_only else grouped,
    }


def pairing_rules_for_menu_ids(
    data: RagData, menu_ids: Iterable[str]
) -> list[dict[str, Any]]:
    menu_names = {
        data.catalog_by_id[menu_id]["menu_name"]
        for menu_id in menu_ids
        if menu_id in data.catalog_by_id
    }
    return [
        row for row in data.tables["pairings"]
        if menu_names & (
            set(pipe_values(row.get("beverage_menu_names")))
            | set(pipe_values(row.get("dessert_menu_names")))
        )
    ]


def applicable_options_for_menu_ids(
    data: RagData, menu_ids: Iterable[str]
) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for menu_id in menu_ids:
        for option_id, option in data.option_by_id.items():
            evaluation = evaluate_option_for_menu(data, menu_id, option_id, None)
            if evaluation["status"] == "applicable":
                found.append({"option": option, "evaluation": evaluation})
    return found


def temperature_alternative_ids(
    data: RagData,
    menu_ids: Iterable[str],
    requested_temperature: Optional[str],
    limit: int = 3,
) -> list[str]:
    if not requested_temperature:
        return []
    source_ids = [menu_id for menu_id in menu_ids if menu_id in data.catalog_by_id]
    if not source_ids:
        return []
    if all(
        any(v["temperature"] == requested_temperature for v in variants_for_menu_id(data, menu_id))
        for menu_id in source_ids
    ):
        return []
    source_categories = {data.catalog_by_id[menu_id]["category"] for menu_id in source_ids}
    source_types = {data.catalog_by_id[menu_id]["type"] for menu_id in source_ids}
    source_tags = {
        normalize_text(tag)
        for menu_id in source_ids
        for tag in pipe_values(data.catalog_by_id[menu_id].get("flavor_tags"))
    }
    ranked: list[tuple[int, str]] = []
    for menu_id, menu in data.catalog_by_id.items():
        if menu_id in source_ids:
            continue
        if not any(
            variant["temperature"] == requested_temperature
            for variant in variants_for_menu_id(data, menu_id)
        ):
            continue
        tags = {normalize_text(tag) for tag in pipe_values(menu.get("flavor_tags"))}
        score = 0
        score += 5 if menu["category"] in source_categories else 0
        score += 2 if menu["type"] in source_types else 0
        score += len(source_tags & tags)
        ranked.append((score, menu_id))
    return [menu_id for _, menu_id in sorted(ranked, key=lambda item: (-item[0], item[1]))[:limit]]


def rank_unavailable_fallback_candidates(
    data: RagData, plan: QueryPlan, limit: int = 3
) -> list[str]:
    candidates = filter_recommendation_candidates(data, plan)
    terms = [*plan.unavailable_profile_terms, *plan.preferred_terms]
    ranked: list[tuple[int, str]] = []
    for menu_id in candidates:
        menu = data.catalog_by_id[menu_id]
        searchable = normalize_text(" ".join(str(menu.get(field) or "") for field in [
            "category", "menu_name", "flavor_tags", "texture",
            "recommended_for", "recommended_situations", "rich_description",
        ]))
        score = sum(1 for term in terms if normalize_text(term) in searchable)
        ranked.append((score, menu_id))
    positive = [item for item in ranked if item[0] > 0]
    return [
        menu_id
        for _, menu_id in sorted(positive, key=lambda item: (-item[0], item[1]))[:limit]
    ]


def exact_fact_bundle(
    data: RagData,
    menu_ids: list[str],
    option_ids: list[str],
    plan: QueryPlan,
    candidate_menu_ids: list[str],
    ambiguous_menu_ids: list[str],
    unavailable_fallback_ids: Optional[list[str]] = None,
    temperature_alternative_menu_ids: Optional[list[str]] = None,
) -> dict[str, Any]:
    unavailable = None
    if plan.unavailable_term:
        unavailable = next(
            (
                row for row in data.tables["unavailable_menu_alternatives"]
                if normalize_text(row["requested_menu"]) == normalize_text(plan.unavailable_term)
            ),
            None,
        )
    return {
        "resolved_menus": [
            {
                "catalog": data.catalog_by_id[menu_id],
                "variants": variants_for_menu_id(data, menu_id),
            }
            for menu_id in menu_ids
        ],
        "recommendation_candidates_after_required_filters": [
            {
                "catalog": data.catalog_by_id[menu_id],
                "eligible_variants": [
                    variant for variant in variants_for_menu_id(data, menu_id)
                    if (not plan.temperature or variant["temperature"] == plan.temperature)
                    and meets_allergen_constraint(variant, plan)
                    and (
                        plan.max_price_krw is None
                        or (variant.get("price_krw") is not None and variant["price_krw"] <= plan.max_price_krw)
                    )
                    and (
                        plan.max_caffeine_mg is None
                        or (
                            variant.get("caffeine_mg") is not None
                            and variant["caffeine_mg"] <= plan.max_caffeine_mg
                        )
                    )
                    and (plan.decaf_required is not True or variant.get("decaf_available") == "Y")
                ],
            }
            for menu_id in candidate_menu_ids
        ],
        "options": [data.option_by_id[option_id] for option_id in option_ids],
        "option_evaluations": [
            evaluate_option_for_menu(data, menu_id, option_id, plan.option_quantity)
            for menu_id in menu_ids
            for option_id in option_ids
        ],
        "menu_candidates_for_confirmation": [
            {
                "menu_group_id": menu_id,
                "menu_name": data.catalog_by_id[menu_id]["menu_name"],
                "category": data.catalog_by_id[menu_id]["category"],
            }
            for menu_id in ambiguous_menu_ids
        ],
        "unavailable_menu_rule": unavailable,
        "unavailable_fallback_candidates": [
            data.catalog_by_id[menu_id]
            for menu_id in (unavailable_fallback_ids or [])
            if menu_id in data.catalog_by_id
        ],
        "temperature_alternatives": [
            {
                "catalog": data.catalog_by_id[menu_id],
                "eligible_variants": [
                    variant for variant in variants_for_menu_id(data, menu_id)
                    if variant["temperature"] == plan.temperature
                ],
            }
            for menu_id in (temperature_alternative_menu_ids or [])
            if menu_id in data.catalog_by_id
        ],
        "menu_listing": build_menu_listing(data, plan),
        "pairing_rules": pairing_rules_for_menu_ids(data, menu_ids),
        "applicable_options": (
            applicable_options_for_menu_ids(data, menu_ids)
            if plan.list_options else []
        ),
    }
