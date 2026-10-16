"""POST /api/chat adapter. The browser and order engine own the cart."""

from __future__ import annotations

import os
import re
from difflib import SequenceMatcher, get_close_matches
from typing import Any, Literal, Mapping, Protocol

from fastapi import FastAPI
from pydantic import BaseModel, Field, TypeAdapter

from .dialogue import CATALOG, DialoguePlan, LLMDialoguePlanner, MockPlanner, Planner, TurnStep, _category_term, _menu_mentions, _option_terms, _ordinal, is_payment_request
from .fake_rag import FakeRAG


class HistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class PendingContext(BaseModel):
    kind: Literal["temperature", "quantity_confirm", "clear_confirm", "target"]
    action: dict[str, Any] | None = None
    line_ids: list[str] = Field(default_factory=list)


class ChatContext(BaseModel):
    cart: dict[str, Any]
    pending: PendingContext | None = None
    last_recommended_menu_ids: list[str]
    prior_conditions: dict[str, Any] | None


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    history: list[HistoryMessage]
    context: ChatContext


class RagChoice(BaseModel):
    field: str | None
    value: str
    label: str
    source_id: str | None


class AddItemAction(BaseModel):
    type: Literal["add_item"]
    menu_id: str
    temperature: Literal["HOT", "ICE"] | None = None
    options: dict[str, int] | None = None
    quantity: int | None = None
    pending_resolution: bool | None = None


class UpdateItemAction(BaseModel):
    type: Literal["update_item"]
    target: dict[str, Any]
    set_options: dict[str, int] | None = None
    quantity: int | None = None
    temperature: Literal["HOT", "ICE"] | None = None


class RemoveItemAction(BaseModel):
    type: Literal["remove_item"]
    target: dict[str, Any]


class ReplaceMenuAction(BaseModel):
    type: Literal["replace_menu"]
    target: dict[str, Any]
    menu_id: str


class ClearCartAction(BaseModel):
    type: Literal["clear_cart"]


class ShowCartAction(BaseModel):
    type: Literal["show_cart"]


class FinishOrderingAction(BaseModel):
    type: Literal["finish_ordering"]


class ConfirmOrderAction(BaseModel):
    type: Literal["confirm_order"]


class PayNowAction(BaseModel):
    type: Literal["pay_now"]


class SelectPaymentAction(BaseModel):
    type: Literal["select_payment"]
    method: Literal["card", "easy_pay", "cash"]


class NewOrderAction(BaseModel):
    type: Literal["new_order"]


class SetDineOptionAction(BaseModel):
    type: Literal["set_dine_option"]
    value: Literal["dine_in", "takeout"]


CartAction = (
    AddItemAction | UpdateItemAction | RemoveItemAction | ReplaceMenuAction |
    ClearCartAction | ShowCartAction | FinishOrderingAction |
    ConfirmOrderAction | SetDineOptionAction | PayNowAction | SelectPaymentAction | NewOrderAction
)
ACTION_ADAPTER = TypeAdapter(CartAction)


class ChatResponse(BaseModel):
    answer: str
    status: str
    choices: list[RagChoice]
    related_menu_ids: list[str]
    evidence_document_ids: list[str]
    next_action: str | None
    resume_prompt: str | None
    error: str | None
    cart_actions: list[CartAction] | None = None
    prior_conditions: dict[str, Any] | None = None


class RagService(Protocol):
    def ask(
        self,
        question: str,
        *,
        prior_conditions: dict | None = None,
        recent_history: list[dict[str, str]] | None = None,
    ) -> Mapping[str, Any]: ...


def _menu_id(term: str | None) -> str | None:
    if not term:
        return None
    key = "".join(term.lower().split())
    matches = [menu["id"] for menu in CATALOG["menus"]
               if key in {"".join(alias.lower().split()) for alias in [menu["name"], *menu["aliases"]]}]
    return matches[0] if len(matches) == 1 else None


def _option_id(term: str) -> str | None:
    key = "".join(term.lower().split())
    matches = [option["id"] for option in CATALOG["options"]
               if key == "".join(option["name"].lower().split())]
    return matches[0] if len(matches) == 1 else None


def _option_selection(terms: list[str], count: int | None = None, remove: bool = False) -> tuple[dict[str, int], str | None]:
    selections: dict[str, int] = {}
    for term in terms:
        option_id = _option_id(term)
        if not option_id:
            return {}, f"'{term}' 옵션은 종류를 정확히 골라 주세요."
        # '휘핑 제외'는 카페모카의 선택 옵션이다. 기존 옵션 제거와 구별한다.
        selections[option_id] = 1 if term == "휘핑 제외" else 0 if remove else count or 1
    return selections, None


def _suggest_menu(term: str | None) -> str | None:
    if not term:
        return None
    aliases = {alias: menu["id"] for menu in CATALOG["menus"] for alias in [menu["name"], *menu["aliases"]]}
    near = get_close_matches(term, aliases, n=1, cutoff=0.78)
    return aliases[near[0]] if near else None


def _menu_candidates(term: str | None) -> list[dict[str, Any]]:
    """Show catalog matches for a broad name without guessing a specific item."""
    if not term:
        return []
    key = "".join(term.lower().split())
    direct = [menu for menu in CATALOG["menus"]
            if key and any(key in "".join(alias.lower().split())
                           for alias in [menu["name"], *menu["aliases"]])]
    if direct:
        return direct
    category = _category_term(term)
    return [menu for menu in CATALOG["menus"] if menu["category"] == category] if category else []


def _requested_information(text: str) -> str:
    """Normalize a clear typo in an information field, without guessing a menu."""
    fields = ("카페인", "칼로리", "당류", "알레르기", "가격", "용량")
    if any(field in text for field in fields):
        return text
    words = re.findall(r"[가-힣ㄱ-ㅎ]+", text)
    for word in words:
        matches = sorted(((max(
            SequenceMatcher(None, word[:length], field).ratio()
            for length in range(len(field), min(len(word), len(field) + 2) + 1)
        ) if len(word) >= len(field) else SequenceMatcher(None, word, field).ratio(), field)
            for field in fields), reverse=True)
        if matches and matches[0][0] >= 0.6 and (len(matches) == 1 or matches[0][0] - matches[1][0] >= 0.15):
            return matches[0][1]
    return text


def _target(cart: dict[str, Any], term: str | None, menu_id: str | None) -> tuple[dict | None, str | None]:
    lines = cart.get("lines", [])
    if term:
        key = "".join(term.lower().split())
        broad = list(dict.fromkeys(line["menu_id"] for line in lines
                                  if key and key in "".join(line.get("menu_name", "").lower().split())))
        if len(broad) > 1:
            return {"menu_ids": broad}, None
    candidates = [line for line in lines if not menu_id or line.get("menu_id") == menu_id]
    if not candidates:
        return None, "바꿀 주문 항목을 찾지 못했어요. 먼저 메뉴를 담거나 대상을 알려 주세요."
    if not menu_id and len(candidates) > 1:
        return None, "어느 주문 항목을 말씀하시는 건가요? 메뉴명을 알려 주세요."
    return ({"menu_id": menu_id} if menu_id else {"line_id": candidates[0]["line_id"]}), None


def _action(step: TurnStep, cart: dict[str, Any], pending: PendingContext | None, last_menu: str | None,
            recommended: list[str]) -> tuple[dict | None, str | None, str | None]:
    if step.kind == "new_order":
        if pending or (cart.get("lines") and cart.get("stage") != "complete"):
            return None, "진행 중인 주문이 있어요. 모두 취소하려면 '전체 취소'라고 말씀해 주세요.", None
        return {"type": "new_order"}, None, None
    if step.kind == "select_payment":
        if not step.payment_method:
            return None, "카드, 간편결제, 현금 중 선택해 주세요.", None
        return {"type": "select_payment", "method": step.payment_method}, None, None
    referent = not step.menu_term or bool(re.search(r"그거|그걸", step.menu_term)) or _ordinal(step.menu_term) is not None
    menu_id = _menu_id(step.menu_term) or (last_menu if referent else None)
    ordinal = step.ordinal or _ordinal(step.text)
    if ordinal is not None:
        menu_id = recommended[ordinal - 1] if ordinal <= len(recommended) else None
        if not menu_id:
            return None, "직전 추천 목록에서 해당 순서를 확인할 수 없어요. 메뉴명을 알려 주세요.", None
    elif not menu_id and re.search(r"그거|그걸", step.text) and len(recommended) == 1:
        menu_id = recommended[0]

    if step.kind == "finish_ordering" and is_payment_request(step.text):
        if pending:
            return None, cart.get("pending_question") or "진행 중인 주문의 확인부터 마쳐 주세요.", None
        stage = cart.get("stage")
        if stage in {"pay_confirm", "complete"}:
            return {"type": "pay_now"}, None, None
        if stage == "final_confirm":
            return {"type": "confirm_order"}, None, None
        if stage == "payment":
            return None, "카드, 간편결제, 현금 중 선택해 주세요.", None
        if stage == "dine_option":
            return None, "매장에서 드시나요, 포장하시나요?", None
    if step.kind in {"show_cart", "clear_cart", "finish_ordering", "confirm_order"}:
        return {"type": step.kind}, None, None
    if step.kind == "set_dine_option":
        if not step.dine_option:
            return None, "매장 또는 포장 중 골라 주세요.", None
        return {"type": "set_dine_option", "value": step.dine_option}, None, None
    if step.kind == "add":
        if not menu_id:
            near = _suggest_menu(step.menu_term)
            if near:
                name = next(menu["name"] for menu in CATALOG["menus"] if menu["id"] == near)
                return None, f"'{step.menu_term}'은(는) {name}을 말씀하셨나요? 확인 후 담을게요.", near
            return None, f"'{step.menu_term or step.text}' 메뉴를 확인할 수 없어 담지 않았어요. 정확한 메뉴명을 알려 주세요.", None
        options, problem = _option_selection(step.option_terms, step.option_quantity)
        if problem:
            return None, problem, None
        action: dict[str, Any] = {"type": "add_item", "menu_id": menu_id}
        if step.temperature:
            action["temperature"] = step.temperature
        if step.quantity is not None:
            action["quantity"] = step.quantity
        if options:
            action["options"] = options
        return action, None, None

    if step.kind == "update_temperature":
        if not step.temperature:
            return None, "따뜻하게 또는 아이스로 말씀해 주세요.", None
        drink_ids = {menu["id"] for menu in CATALOG["menus"] if menu["type"] == "음료"}
        drinks = [line for line in cart.get("lines", []) if line.get("menu_id") in drink_ids]
        if not drinks:
            return None, "온도를 바꿀 음료가 없어요. 메뉴를 알려 주세요.", None
        target = {"line_id": drinks[0]["line_id"]} if len(drinks) == 1 else {"menu_ids": list(dict.fromkeys(line["menu_id"] for line in drinks))}
        return {"type": "update_item", "target": target, "temperature": step.temperature}, None, None

    draft = pending.action if pending and pending.kind == "temperature" else None
    draft_id = draft.get("menu_id") if draft else None
    matches_draft = draft_id and (not step.menu_term or menu_id == draft_id)
    matching_lines = [line for line in cart.get("lines", []) if line.get("menu_id") == draft_id]
    if step.kind in {"update_option", "update_quantity"} and matches_draft and matching_lines:
        if not re.search(r"장바구니|이미\s*담", step.text) and not re.search(r"초안|보류|담기\s*전", step.text):
            return None, "이미 담긴 메뉴와 보류 중인 같은 메뉴 중 어느 쪽을 바꿀까요?", None
        if re.search(r"초안|보류|담기\s*전", step.text):
            matching_lines = []
    if step.kind in {"update_option", "update_quantity"} and matches_draft and not matching_lines:
        if step.kind == "update_quantity":
            return ({"type": "update_item", "target": {"pending": True}, "quantity": step.quantity}, None, None) if step.quantity else (None, "몇 잔으로 바꿀지 알려 주세요.", None)
        options, problem = _option_selection(step.option_terms, step.option_quantity, step.remove_option)
        if problem or not options:
            return None, problem or "어떤 옵션을 바꿀지 알려 주세요.", None
        return {"type": "update_item", "target": {"pending": True}, "set_options": options}, None, None

    target, problem = _target(cart, step.menu_term, menu_id)
    if problem:
        return None, problem, None
    if step.kind == "remove":
        return {"type": "remove_item", "target": target}, None, None
    if step.kind == "replace":
        replacement = _menu_id(step.replacement_term)
        return ({"type": "replace_menu", "target": target, "menu_id": replacement}, None, None) if replacement else (None, "바꿀 메뉴명을 확인해 주세요.", None)
    if step.kind == "update_quantity":
        return ({"type": "update_item", "target": target, "quantity": step.quantity}, None, None) if step.quantity else (None, "몇 잔으로 바꿀지 알려 주세요.", None)
    options, problem = _option_selection(step.option_terms, step.option_quantity, step.remove_option)
    if problem or not options:
        return None, problem or "어떤 옵션을 바꿀지 알려 주세요.", None
    return {"type": "update_item", "target": target, "set_options": options}, None, None


def _ordered_recommendations(result: Mapping[str, Any]) -> list[str]:
    """RAG가 확정한 ID 목록을 추천 순서의 단일 기준으로 사용한다."""
    valid_ids = {menu["id"] for menu in CATALOG["menus"]}
    return list(dict.fromkeys(
        menu_id
        for menu_id in result.get("related_menu_ids", [])
        if menu_id in valid_ids
    ))


def route_message(request: ChatRequest, rag: RagService, planner: Planner | None = None) -> ChatResponse:
    """Interpret one turn, then return validated IDs and actions for the frontend engine."""
    planner = planner or MockPlanner()
    planner_context = {**request.context.model_dump(), "history": [row.model_dump() for row in request.history]}
    plan: DialoguePlan = planner.plan(request.message, planner_context)
    mutating = {"add", "update_option", "update_quantity", "update_temperature", "remove", "replace", "clear_cart"}
    has_change = any(step.kind in mutating for step in plan.steps)
    answers: list[str] = []
    actions: list[dict[str, Any]] = []
    choices: list[RagChoice] = []
    menu_ids: list[str] = []
    evidence_ids: list[str] = []
    status = "ok"
    next_action = None
    resume_prompt = None
    prior_conditions = None
    last_menu: str | None = None
    unavailable_handled = False
    recommendation_needs_answer = False

    valid_ids = {menu["id"] for menu in CATALOG["menus"]}
    menu_names = {menu["id"]: menu["name"] for menu in CATALOG["menus"]}
    recommended = [menu_id for menu_id in request.context.last_recommended_menu_ids if menu_id in valid_ids]
    for step in plan.steps:
        if step.kind == "confirm_order" and has_change and request.context.cart.get("stage") == "final_confirm":
            continue
        if step.kind in {"information", "recommend"}:
            question = step.text
            cart_lines = request.context.cart.get("lines", [])
            field = _requested_information(step.text)
            if step.kind == "information" and not _menu_mentions(question) and (
                _option_terms(question) or field != step.text or re.search(
                    r"가격|얼마|카페인|칼로리|당류|영양|알레르기|용량|맛|설명|성분|옵션|가능",
                    question,
                )
            ):
                pending_mentions = _menu_mentions(request.context.cart.get("pending_question") or "")
                if pending_mentions:
                    question = f"{pending_mentions[-1][2]} {question}"
                else:
                    drink_ids = {menu["id"] for menu in CATALOG["menus"] if menu["type"] == "음료"}
                    drinks = [line for line in cart_lines if line.get("menu_id") in drink_ids]
                    if len(drinks) == 1:
                        question = f"{drinks[0]['menu_name']}에 {question}"
                    elif len(cart_lines) == 1:
                        question = f"{cart_lines[0]['menu_name']}에 {question}"
            if field != step.text and field in {"카페인", "칼로리", "당류", "알레르기", "가격", "용량"}:
                menu_mentions = _menu_mentions(question)
                question = f"{menu_mentions[-1][2]} {field}만 간결히 알려 주세요" if menu_mentions else f"{field}만 간결히 알려 주세요"
            conditions = request.context.prior_conditions
            if step.kind == "recommend" and re.search(r"잘\s*모르겠|아무거나", question):
                conditions = {**(conditions or {})}
                conditions["clarification_count"] = min(2, int(conditions.get("clarification_count", 0)) + 1)
            if step.kind == "recommend" and re.search(r"다른\s*거|다른\s*메뉴", question) and recommended:
                conditions = {**(conditions or {})}
                conditions["excluded_menu_ids"] = list(dict.fromkeys([
                    *conditions.get("excluded_menu_ids", []), *recommended,
                ]))
            if step.kind == "recommend" and re.search(r"싫어|말고|제외", question) and recommended:
                conditions = {**(conditions or {})}
                conditions["excluded_menu_ids"] = list(dict.fromkeys([
                    *conditions.get("excluded_menu_ids", []), *recommended,
                ]))
            conditions = {**(conditions or {}), "_dialogue_intent": step.kind}
            result = rag.ask(
                question,
                prior_conditions=conditions,
                recent_history=[row.model_dump() for row in request.history[-6:]],
            )
            if step.kind == "recommend" and result.get("status") in {"no_candidates", "needs_clarification"}:
                recommendation_needs_answer = True
            if result.get("answer"):
                answers.append(str(result["answer"]))
            result_choices = [RagChoice.model_validate(row) for row in result.get("choices", [])]
            if step.kind == "recommend":
                ordered = _ordered_recommendations(result) if result.get("status") == "ok" and not result.get("error") else []
                menu_ids.extend(ordered)
                if ordered:
                    next_action = "choose_menu"
                    choice_by_id = {
                        choice.value if choice.field == "menu_group_id" else choice.source_id: choice
                        for choice in result_choices
                        if (choice.value if choice.field == "menu_group_id" else choice.source_id) in ordered
                    }
                    non_menu_choices = [
                        choice for choice in result_choices
                        if choice.source_id not in valid_ids and choice.field != "menu_group_id"
                    ]
                    result_choices = [
                        choice_by_id[menu_id]
                        for menu_id in ordered
                        if menu_id in choice_by_id
                    ] + non_menu_choices
                else:
                    result_choices = [
                        choice
                        for choice in result_choices
                        if choice.field != "menu_group_id" and choice.source_id not in valid_ids
                    ]
            else:
                menu_ids.extend(result.get("related_menu_ids", []))
            choices.extend(result_choices)
            evidence_ids.extend(result.get("evidence_document_ids", []))
            status = result.get("status", status)
            result_next_action = result.get("next_action")
            if step.kind == "recommend" and not ordered and result_next_action == "choose_menu":
                result_next_action = None
            next_action = next_action or result_next_action
            resume_prompt = result.get("resume_prompt")
            if result.get("applied_conditions") is not None:
                prior_conditions = result["applied_conditions"]
            elif conditions is not None:
                prior_conditions = conditions
            if prior_conditions is not None:
                prior_conditions = {key: value for key, value in prior_conditions.items() if key != "_dialogue_intent"}
            last_menu = _menu_id(step.menu_term)
            if not last_menu:
                mentions = _menu_mentions(step.text)
                last_menu = _menu_id(mentions[-1][2]) if mentions else None
        elif step.kind == "answer_pending":
            pending = request.context.pending
            draft = pending.action if pending and pending.kind == "temperature" else None
            if (draft and draft.get("type") == "add_item" and draft.get("menu_id") in valid_ids
                    and step.temperature):
                actions.append({**draft, "temperature": step.temperature, "pending_resolution": True})
            elif any(item.kind in {"information", "recommend"} for item in plan.steps) and not step.temperature:
                # 정보 질문에 모델이 빈 온도 답변 단계를 덧붙여도 원래 주문을 재질문한다.
                continue
            else:
                answers.append("진행 중인 온도 질문을 확인하기 어려워요. 메뉴와 온도를 다시 알려 주세요.")
                status = "partial" if actions else "needs_clarification"
        elif step.kind in {"add", "update_option", "update_quantity", "update_temperature", "remove", "replace",
                           "show_cart", "clear_cart", "finish_ordering", "confirm_order", "set_dine_option", "select_payment", "new_order"}:
            if step.kind == "add" and not _menu_id(step.menu_term) and step.ordinal is None:
                candidates = _menu_candidates(step.menu_term)
                if len(candidates) > 1:
                    category = _category_term(step.menu_term or "")
                    if category and step.quantity is None:
                        names = ", ".join(menu["name"] for menu in candidates)
                        answers.append(f"{category} 종류는 {names}가 있어요. 원하는 종류와 수량을 알려 주세요.")
                        status = "partial" if actions else "needs_clarification"
                        continue
                    answers.append(f"어떤 {step.menu_term} 메뉴를 말씀하시는 건가요? 아래에서 골라 주세요.")
                    choices.extend(RagChoice(field="menu_group_id", value=menu["id"],
                                             label=menu["name"], source_id=menu["id"])
                                   for menu in candidates)
                    menu_ids.extend(menu["id"] for menu in candidates)
                    status = "partial" if actions else "needs_clarification"
                    continue
                if step.menu_term and not _suggest_menu(step.menu_term):
                    # 판매하지 않는 메뉴의 대안 검색은 기존 RAG가 맡는다. 여기서는
                    # 검증된 판매 메뉴 ID만 선택지로 바꾸고 주문 액션은 만들지 않는다.
                    result = rag.ask(
                        f"{step.menu_term} 메뉴를 주문하려고 해요. 판매하지 않는다면 대안 메뉴를 알려 주세요.",
                        prior_conditions=request.context.prior_conditions,
                        recent_history=[row.model_dump() for row in request.history[-6:]],
                    )
                    rag_alternative_ids = list(dict.fromkeys(
                        menu_id for menu_id in (result.get("related_menu_ids") or [])
                        if menu_id in valid_ids
                    ))[:3]
                    rule = next((row for row in CATALOG["unavailable"]
                                 if "".join(row["requested"].lower().split()) == "".join(step.menu_term.lower().split())), None)
                    alternative_ids = rag_alternative_ids or list(dict.fromkeys(
                        menu_id for menu_id in (rule or {}).get("alternativeMenuIds", [])
                        if menu_id in valid_ids
                    ))[:3]
                    prefix = f"'{step.menu_term}'은(는) 판매 메뉴에 없어 담지 않았어요."
                    if alternative_ids:
                        names = ", ".join(menu_names[menu_id] for menu_id in alternative_ids)
                        answers.append(f"{prefix} 대신 {names} 중에서 골라 주세요.")
                        if rag_alternative_ids and result.get("answer"):
                            answers.append(str(result["answer"]))
                            evidence_ids.extend(result.get("evidence_document_ids", []))
                        choices.extend(RagChoice(field="menu_group_id", value=menu_id,
                                                 label=menu_names[menu_id], source_id=menu_id)
                                       for menu_id in alternative_ids)
                        menu_ids.extend(alternative_ids)
                    else:
                        answers.append(f"{prefix} 제안할 대안도 찾지 못했어요. 다른 메뉴를 알려 주세요.")
                    status = "partial" if actions else "needs_clarification" if alternative_ids else "not_found"
                    unavailable_handled = True
                    continue
            action, problem, suggestion = _action(step, request.context.cart, request.context.pending, last_menu, recommended)
            if action:
                actions.append(action)
                last_menu = action.get("menu_id", last_menu)
            if problem:
                answers.append(problem)
                status = "partial" if actions else "needs_clarification"
            if suggestion:
                name = next(menu["name"] for menu in CATALOG["menus"] if menu["id"] == suggestion)
                choices.append(RagChoice(field="menu_group_id", value=suggestion, label=name, source_id=suggestion))
                menu_ids.append(suggestion)
                next_action = "confirm_menu"
        elif step.kind == "temperature_conflict":
            # 기존 주문 엔진의 온도 보류 초안을 사용해 다음 답변에 메뉴·수량을 잇는다.
            menu_id = _menu_id(step.menu_term)
            menu = next((row for row in CATALOG["menus"] if row["id"] == menu_id), None)
            if not request.context.pending and menu and set(menu["temperatures"]) == {"HOT", "ICE"}:
                draft = step.model_copy(update={"kind": "add", "temperature": None})
                action, problem, _ = _action(draft, request.context.cart, None, last_menu, recommended)
                if action:
                    actions.append(action)
                    last_menu = menu_id
                elif problem:
                    answers.append(problem)
            else:
                answers.append("따뜻한 것과 아이스 중 어떤 것으로 드릴까요?")
            status = "partial" if actions else "needs_clarification"
        elif step.kind == "set_order_mode":
            if step.order_mode:
                next_action = "open_menu" if step.order_mode == "quick" else "open_chat"
                answers.append("빠른 주문과 대화 주문 모두 가능해요. 주문 중에도 바꿀 수 있어요.")
            else:
                answers.append("빠른 주문과 대화 주문 중 골라 주세요.")
                status = "needs_clarification"
        else:
            answers.append(f"'{step.text}' 요청을 확인하기 어려워 처리하지 않았어요. 메뉴나 요청을 구체적으로 말씀해 주세요.")
            status = "partial" if actions else "needs_clarification"

    if not plan.steps:
        answers.append("원하시는 메뉴나 질문을 조금 더 구체적으로 말씀해 주세요.")
        status = "needs_clarification"

    pending_question = request.context.cart.get("pending_question")
    if request.context.pending and pending_question and not actions and not recommendation_needs_answer and (
        unavailable_handled or any(step.kind in {"information", "recommend"} for step in plan.steps)
    ):
        next_action = "resume_pending_question"
        resume_prompt = pending_question

    response = ChatResponse(
        answer="\n".join(answers), status=status, choices=choices,
        related_menu_ids=list(dict.fromkeys(menu_id for menu_id in menu_ids if menu_id in valid_ids)),
        evidence_document_ids=list(dict.fromkeys(evidence_ids)), next_action=next_action,
        resume_prompt=resume_prompt, error=None,
    )
    if actions:
        response.cart_actions = [ACTION_ADAPTER.validate_python(action) for action in actions]
    if prior_conditions is not None:
        response.prior_conditions = prior_conditions
    return response


def create_app(rag: RagService | None = None, planner: Planner | None = None) -> FastAPI:
    app = FastAPI(title="Kiosk dialogue integration")
    live = os.getenv("KIOSK_MODE", "mock").lower() == "live"
    if live:
        from pathlib import Path
        from dotenv import load_dotenv
        from .live_rag import LiveRAG

        load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
        active_rag = rag if rag is not None else LiveRAG()
        active_planner = planner if planner is not None else LLMDialoguePlanner()
    else:
        active_rag = rag if rag is not None else FakeRAG()
        active_planner = planner if planner is not None else MockPlanner()

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/chat", response_model=ChatResponse, response_model_exclude_unset=True)
    def chat(request: ChatRequest) -> ChatResponse:
        return route_message(request, active_rag, active_planner)

    return app


app = create_app()
