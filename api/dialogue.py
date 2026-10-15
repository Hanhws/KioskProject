"""Turn planning. Clear order phrases use the existing rule planner in live mode.

Other live requests use one structured LLM call to separate information and order work.
Neither planner is allowed to supply menu IDs, option IDs, or prices.
"""

from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, Field


ROOT = Path(__file__).resolve().parents[1]
CATALOG = json.loads((ROOT / "lib/generated/catalog.json").read_text(encoding="utf-8"))
MENUS = CATALOG["menus"]
OPTIONS = CATALOG["options"]


class TurnStep(BaseModel):
    kind: Literal[
        "information", "recommend", "add", "update_option", "update_quantity",
        "remove", "replace", "show_cart", "clear_cart", "finish_ordering",
        "confirm_order", "set_dine_option", "select_payment", "new_order", "set_order_mode", "answer_pending", "update_temperature", "temperature_conflict", "unknown",
    ]
    text: str
    menu_term: str | None = None
    replacement_term: str | None = None
    temperature: Literal["HOT", "ICE"] | None = None
    quantity: int | None = Field(default=None, ge=1)
    option_quantity: int | None = Field(default=None, ge=1)
    remove_option: bool = False
    dine_option: Literal["dine_in", "takeout"] | None = None
    payment_method: Literal["card", "easy_pay", "cash"] | None = None
    order_mode: Literal["quick", "dialogue"] | None = None
    ordinal: int | None = Field(default=None, ge=1)
    option_terms: list[str] = Field(default_factory=list)


class DialoguePlan(BaseModel):
    steps: list[TurnStep] = Field(default_factory=list)


class Planner(Protocol):
    def plan(self, message: str, context: dict) -> DialoguePlan: ...


def is_payment_request(message: str) -> bool:
    text = re.sub(r"[.!~〜]+$", "", message.strip()).strip()
    return bool(re.fullmatch(r"(?:결제|계산)\s*(?:하기|해\s*줘(?:요)?|해\s*주세요|할게요|진행해\s*주세요)", text))


def control_step(message: str, context: dict) -> TurnStep | None:
    """메뉴 주문이 섞이지 않은 명확한 화면 명령만 모델 없이 연결한다."""
    text = re.sub(r"[.!~〜]+$", "", message.strip()).strip()
    key = re.sub(r"\s+", "", text)
    if key in {"장바구니", "장바구니보기", "주문내역", "주문내역보기"}:
        return TurnStep(kind="show_cart", text=message)
    if key == "주문마치기":
        return TurnStep(kind="finish_ordering", text=message)
    if key in {"전체취소", "다취소할게요", "전부취소할게요"}:
        return TurnStep(kind="clear_cart", text=message)
    if key in {"새주문시작", "새로주문할게요"}:
        return TurnStep(kind="new_order", text=message)
    if key in {"결제수단바꾸기", "결제방법바꾸기", "결제수단바꿔줘", "결제수단바꿔주세요"} and context.get("cart", {}).get("stage") in {"payment", "pay_confirm"}:
        return TurnStep(kind="confirm_order", text=message)
    if key == "네,주문할게요" and context.get("cart", {}).get("stage") == "final_confirm":
        return TurnStep(kind="confirm_order", text=message)
    method = re.fullmatch(r"(카드|신용카드|체크카드|간편결제|카카오페이|네이버페이|삼성페이|애플페이|토스페이|현금)(?:로)?(?:결제)?(?:할게요|해주세요|해줘요|해줘|요)?", key)
    if method:
        name = method.group(1)
        return TurnStep(kind="select_payment", text=message, payment_method=(
            "cash" if name == "현금" else "card" if name in {"카드", "신용카드", "체크카드"} else "easy_pay"
        ))
    return None


def _menu_mentions(text: str) -> list[tuple[int, int, str]]:
    """Match catalog aliases, longest first, without accepting overlapping names."""
    candidates = []
    for menu in MENUS:
        for alias in set([menu["name"], *menu["aliases"]]):
            for match in re.finditer(re.escape(alias), text, re.IGNORECASE):
                candidates.append((match.start(), match.end(), alias))
    chosen: list[tuple[int, int, str]] = []
    for candidate in sorted(candidates, key=lambda row: (-(row[1] - row[0]), row[0])):
        if not any(candidate[0] < end and start < candidate[1] for start, end, _ in chosen):
            chosen.append(candidate)
    return sorted(chosen)


def _quantity(text: str) -> int | None:
    match = re.search(r"(?<!\d)(\d{1,3})\s*(잔|개|병|컵|인분)", text)
    if match:
        return int(match.group(1))
    match = re.search(r"(한|하나|두|둘|세|셋|네|넷|다섯)\s*(잔|개|병|컵|인분)", text)
    return {"한": 1, "하나": 1, "두": 2, "둘": 2, "세": 3, "셋": 3, "네": 4, "넷": 4, "다섯": 5}.get(match.group(1)) if match else None


def _temperature(text: str) -> Literal["HOT", "ICE"] | None:
    hot = bool(re.search(r"따뜻|뜨거|핫|HOT|뜨아", text, re.IGNORECASE))
    ice = bool(re.search(r"아이스|차가|시원|ICE|아아", text, re.IGNORECASE))
    return "HOT" if hot and not ice else "ICE" if ice and not hot else None


def _option_terms(text: str) -> list[str]:
    terms = []
    for label, term in [
        ("바닐라 시럽", "바닐라 시럽"), ("카라멜 시럽", "카라멜 시럽"),
        ("샷", "샷 추가"), ("두유", "두유 변경"),
        ("저지방", "저지방 우유 변경"), ("디카페인", "디카페인 변경"),
        ("연하게", "연하게"), ("휘핑 추가", "휘핑 추가"),
        ("휘핑 제외", "휘핑 제외"),
    ]:
        if label in text:
            terms.append(term)
    if "시럽" in text and not any("시럽" in term for term in terms):
        terms.append("시럽")
    if "휘핑" in text and not any("휘핑" in term for term in terms):
        terms.append("휘핑 제외" if re.search(r"빼|제외|없이", text) else "휘핑 추가")
    return terms


def _option_quantity(text: str) -> int | None:
    match = re.search(r"(?:샷|시럽|휘핑)\s*(\d+|한|두|세)\s*(?:개|회|번)", text)
    if not match:
        return None
    return {"한": 1, "두": 2, "세": 3}.get(match.group(1)) or int(match.group(1))


def _ordinal(text: str | None) -> int | None:
    if not text:
        return None
    for ordinal, pattern in [
        (1, r"첫\s*번째|첫째|1\s*번"),
        (2, r"두\s*번째|둘째|2\s*번"),
        (3, r"세\s*번째|셋째|3\s*번"),
        (4, r"네\s*번째|넷째|4\s*번"),
    ]:
        if re.search(pattern, text):
            return ordinal
    return None


def _category_term(text: str) -> str | None:
    """Only recognize a close, unambiguous catalog category; never pick a menu."""
    key = re.sub(r"(?:도|요)$", "", text.strip())
    categories = {menu["category"] for menu in MENUS}
    scores = sorted(((SequenceMatcher(None, key, category).ratio(), category)
                     for category in categories if abs(len(key) - len(category)) <= 1), reverse=True)
    return scores[0][1] if scores and scores[0][0] >= 0.65 and (len(scores) == 1 or scores[0][0] - scores[1][0] >= 0.15) else None


class MockPlanner:
    """Catalog based rule planner for local tests and clear live order phrases."""

    def plan(self, message: str, context: dict) -> DialoguePlan:
        if is_payment_request(message):
            return DialoguePlan(steps=[TurnStep(kind="finish_ordering", text=message)])
        if (len(_menu_mentions(message)) >= 2 and re.search(r"진|비교", message)
                and re.search(r"가격|얼마", message)):
            # 비교와 가격처럼 같은 두 메뉴를 대상으로 한 복합 정보 질문은
            # 문장부호로 나누지 않아 RAG가 두 요구를 함께 답하게 한다.
            return DialoguePlan(steps=[TurnStep(kind="information", text=message)])
        if (context.get("last_recommended_menu_ids") and re.search(r"싫어|말고|제외", message)
                and re.search(r"뜨거|따뜻|차가|아이스|달|단|쓴|카페인", message)):
            # 직전 추천에 대한 취향 수정은 메뉴명을 다시 말하지 않아도 추천 후속 턴이다.
            return DialoguePlan(steps=[TurnStep(kind="recommend", text=message)])
        control = control_step(message, context)
        if control:
            return DialoguePlan(steps=[control])
        if context.get("cart", {}).get("stage") == "final_confirm":
            # '네, 그런데 ... 추가'는 최종 동의가 아니라 주문 변경이다.
            message = re.sub(r"^\s*(?:네|예|응)\s*[,，]\s*(?:그런데|근데|하지만)\s*", "", message)
        parts = [part.strip() for part in re.split(r"[?.!]", message) if part.strip()]
        steps: list[TurnStep] = []
        last_menu: str | None = None
        for part in parts:
            pending = context.get("pending") or {}
            if pending.get("kind") == "temperature":
                leading = re.match(r"^\s*(아이스(?:요|로)?|차갑게|따뜻하게|핫(?:으로|요)?)\s*[,，]\s*(.+)$", part)
                if leading:
                    steps.append(TurnStep(kind="answer_pending", text=leading.group(1),
                                          temperature=_temperature(leading.group(1))))
                    part = leading.group(2).strip()
                elif re.fullmatch(r"\s*(?:아이스(?:요|로)?|차갑게|따뜻하게|핫(?:으로|요)?)\s*", part):
                    steps.append(TurnStep(kind="answer_pending", text=part, temperature=_temperature(part)))
                    continue
            bare_temperature = re.sub(r"[~〜\s]+", "", part)
            if not pending and re.fullmatch(r"(?:ice|아이스|hot|핫)(?:요|로)?", bare_temperature, re.I):
                steps.append(TurnStep(kind="update_temperature", text=part, temperature=_temperature(part)))
                continue
            mentions = _menu_mentions(part)
            if mentions:
                last_menu = mentions[-1][2]
            if re.search(r"빠른\s*주문|대화(?:로|\s*주문)", part) and re.search(r"할게|할래|바꿔|전환", part):
                mode = "quick" if "빠른" in part else "dialogue"
                steps.append(TurnStep(kind="set_order_mode", text=part, order_mode=mode))
                continue
            if len(mentions) <= 1 and re.search(r"따뜻|뜨거|핫|HOT|뜨아", part, re.I) and re.search(r"아이스|차가|시원|ICE|아아", part, re.I) and (re.search(r"주세요|주문|담아", part) or context.get("cart", {}).get("pending_question")):
                steps.append(TurnStep(kind="temperature_conflict", text=part,
                                      menu_term=mentions[0][2] if mentions else None,
                                      quantity=_quantity(part), option_terms=_option_terms(part),
                                      option_quantity=_option_quantity(part)))
                continue
            if re.search(r"(?:다\s*취소|전부\s*취소|처음부터\s*다시|전체\s*취소)", part):
                steps.append(TurnStep(kind="clear_cart", text=part))
                continue
            if re.search(r"총\s*얼마|지금까지.*얼마|(?:뭐|무엇|얼마|총액|내역).*(?:담|장바구니|총|결제)|(?:담|장바구니).*(?:뭐|무엇|얼마|내역)", part):
                if not steps or steps[-1].kind != "show_cart":
                    steps.append(TurnStep(kind="show_cart", text=part))
                continue
            if re.search(r"^(?:그게\s*다|이제\s*됐|다\s*됐|더\s*없|주문\s*마치|결제할게|계산할게)", part):
                steps.append(TurnStep(kind="finish_ordering", text=part))
                continue
            if re.search(r"이대로\s*주문할게|이대로\s*할게", part):
                steps.append(TurnStep(kind="confirm_order", text=part))
                continue
            dine = "takeout" if re.search(r"포장|테이크아웃", part) else "dine_in" if re.search(r"먹고\s*갈|매장", part) else None
            dine_request = bool(re.search(r"먹고\s*갈|포장이요|포장할|포장해|매장이요|매장에서\s*먹|매장으로", part)) or part in {"포장", "매장"}
            if dine and dine_request and not mentions and not re.search(r"얼마|가격|추천|가능", part):
                steps.append(TurnStep(kind="set_dine_option", text=part, dine_option=dine))
                continue
            if re.search(r"추천", part) or re.search(r"다른\s*거\s*없", part) or re.search(r"잘\s*모르겠|아무거나", part):
                steps.append(TurnStep(kind="recommend", text=part))
                continue
            question = bool(re.search(r"얼마|가격|가능|할 수|되나요|돼요|뭐|어떤|추천|알려|있어요", part))
            request = bool(re.search(r"주세요|주문|담아|추가|빼|제거|변경|바꿔|하나요|(?:\d+|한|하나|두|둘|세|셋|네|넷)\s*(?:잔|개)", part))
            if question and (not request or re.search(r"얼마|가격|가능|할 수|추천|알려", part)):
                steps.append(TurnStep(kind="information", text=part))
                continue
            if request:
                if len(mentions) >= 2 and re.search(r"말고|대신", part) and re.search(r"바꿔|변경", part):
                    steps.append(TurnStep(kind="replace", text=part, menu_term=mentions[0][2], replacement_term=mentions[-1][2]))
                    continue
                option_update = bool(_option_terms(part)) and not re.search(r"(메뉴|음료).*추가", part) and (
                    bool(re.search(r"추가|변경|바꿔|빼|제거|제외|올려", part)) or
                    (bool(re.search(r"해\s*주세요", part)) and bool(context.get("cart", {}).get("lines"))) or
                    ("연하게" in part and bool(context.get("cart", {}).get("lines")))
                )
                if option_update and mentions:
                    term = "".join(mentions[0][2].lower().split())
                    mentioned_ids = {menu["id"] for menu in MENUS
                                     if term in {"".join(alias.lower().split()) for alias in [menu["name"], *menu["aliases"]]}}
                    cart_lines = context.get("cart", {}).get("lines", [])
                    option_update = any(line.get("menu_id") in mentioned_ids or
                                        term in "".join(line.get("menu_name", "").lower().split())
                                        for line in cart_lines)
                if option_update and (not mentions or not re.search(r"(잔|개).*(주세요|주문|담아)", part)):
                    steps.append(TurnStep(kind="update_option", text=part, menu_term=mentions[0][2] if mentions else None,
                                          option_terms=_option_terms(part), option_quantity=_option_quantity(part),
                                          remove_option=bool(re.search(r"빼|제거|제외|없이", part))))
                    continue
                if "빼" in part and not _option_terms(part):
                    steps.append(TurnStep(kind="remove", text=part, menu_term=mentions[0][2] if mentions else None))
                    continue
                if mentions and context.get("cart", {}).get("lines") and re.search(
                    r"(?:잔|개)\s*(?:만)?\s*(?:할게|할래|주세요)|수량.*(?:바꿔|변경)", part
                ):
                    steps.append(TurnStep(kind="update_quantity", text=part, menu_term=mentions[0][2], quantity=_quantity(part)))
                    continue
                if re.search(r"(?:잔|개)\s*(?:으로|로)\s*(?:바꿔|변경)|수량.*(?:바꿔|변경)", part):
                    steps.append(TurnStep(kind="update_quantity", text=part, menu_term=mentions[0][2] if mentions else None, quantity=_quantity(part)))
                    continue
                # HTI 6.1.2: 두 잔 중 하나에만 옵션을 지정한 경우는 별도 항목이다.
                if len(mentions) == 1 and _quantity(part) == 2 and re.search(r"(?:하나는|한\s*잔은)", part) and _option_terms(part):
                    common = dict(kind="add", text=part, menu_term=mentions[0][2], temperature=_temperature(part), quantity=1)
                    steps.append(TurnStep(**common))
                    steps.append(TurnStep(**common, option_terms=_option_terms(part)))
                    if dine:
                        steps.append(TurnStep(kind="set_dine_option", text=part, dine_option=dine))
                    continue
                if mentions:
                    for index, (_, end, term) in enumerate(mentions):
                        previous_end = mentions[index - 1][1] if index else 0
                        next_start = mentions[index + 1][0] if index + 1 < len(mentions) else len(part)
                        item_text = part[end:next_start]
                        item_segment = part[previous_end:next_start]
                        if index + 1 < len(mentions):
                            item_segment = re.split(r"(?:랑|하고|과|와|,)\s*", item_segment, maxsplit=1)[0]
                        steps.append(TurnStep(kind="add", text=part, menu_term=term,
                                              temperature=_temperature(item_segment), quantity=_quantity(item_text),
                                              option_terms=_option_terms(item_text), option_quantity=_option_quantity(item_text)))
                    tail = part[mentions[-1][1]:]
                    unknown = re.search(r"(?:랑|하고|과|와|이랑)\s*([^, ]+?)(?:\s*(?:주세요|주문|담아))?$", tail)
                    if unknown and not _menu_mentions(unknown.group(1)):
                        steps.append(TurnStep(kind="add", text=part, menu_term=unknown.group(1)))
                else:
                    ordinal = _ordinal(part)
                    unknown = re.match(r"^([가-힣A-Za-z]+?)(?:도)?\s*(?:하나|한\s*잔|두\s*잔)?\s*(?:주세요|추가요|추가해\s*주세요|주문할게요|주문해\s*주세요|하나요)$", part)
                    steps.append(TurnStep(kind="add", text=part,
                                          menu_term=last_menu if "그거" in part else unknown.group(1) if unknown and not ordinal else None,
                                          ordinal=ordinal,
                                          temperature=_temperature(part), quantity=_quantity(part), option_terms=_option_terms(part)))
                if dine:
                    steps.append(TurnStep(kind="set_dine_option", text=part, dine_option=dine))
                continue
            steps.append(TurnStep(kind="information", text=part))
        return DialoguePlan(steps=steps or [TurnStep(kind="unknown", text=message)])


class LLMDialoguePlanner:
    """One structured call for intent, order fields, and subrequest order."""

    def __init__(self, model: str = "gpt-4.1-mini"):
        from langchain_openai import ChatOpenAI

        self.model = ChatOpenAI(model=model, temperature=0).with_structured_output(DialoguePlan, method="json_schema")

    def plan(self, message: str, context: dict) -> DialoguePlan:
        # The existing rule planner is more reliable for explicit HTI order forms.
        # Keep mixed questions and open-ended language with the LLM.
        rule_plan = MockPlanner().plan(message, context)
        known_terms = {
            "".join(term.lower().split())
            for menu in MENUS for term in [menu["name"], *menu["aliases"]]
        } | {"".join(row["requested"].lower().split()) for row in CATALOG["unavailable"]}
        def known_add(step: TurnStep) -> bool:
            return step.kind == "add" and bool(step.menu_term) and "".join(step.menu_term.lower().split()) in known_terms

        explicit_order = (
            "?" not in message and bool(re.search(r"(?:주세요|하나요|추가요)\s*[.!]?\s*$", message))
            and all(known_add(step) for step in rule_plan.steps)
        )
        pending_add = (
            (context.get("pending") or {}).get("kind") == "temperature"
            and len(rule_plan.steps) > 1 and rule_plan.steps[0].kind == "answer_pending"
            and all(known_add(step) for step in rule_plan.steps[1:])
        )
        quantity_change = (
            len(rule_plan.steps) == 1 and rule_plan.steps[0].kind == "update_quantity"
            and rule_plan.steps[0].quantity is not None
            and bool(re.search(r"(?:잔|개)\s*(?:으로|로)\s*(?:바꿔|변경)", message))
        )
        clear_edit = len(rule_plan.steps) == 1 and rule_plan.steps[0].kind in {"update_option", "update_temperature"}
        category_order = any(step.kind == "add" and _category_term(step.menu_term or "") for step in rule_plan.steps) and all(step.kind == "add" for step in rule_plan.steps)
        conflict_order = bool(rule_plan.steps) and any(step.kind == "temperature_conflict" for step in rule_plan.steps) and all(
            known_add(step) or step.kind == "temperature_conflict" for step in rule_plan.steps
        )
        if explicit_order or pending_add or quantity_change or clear_edit or category_order or conflict_order or is_payment_request(message) or control_step(message, context):
            return rule_plan

        menu_names = [m["name"] for m in MENUS]
        prompt = (
            "카페 키오스크 요청을 순서 있는 단계로 나누세요. 정보 질문은 주문보다 먼저 두세요. "
            "정보 질문과 주문을 모두 보존하세요. '할 수 있어요?'는 정보 질문이고 '해 주세요'는 변경 요청입니다. "
            "메뉴명은 사용자 표현을 기록하고, 옵션은 제공한 실제 옵션 이름으로 정규화하세요. "
            "옵션 종류가 불분명하면 사용자 표현 그대로 두세요. ID와 가격을 만들지 마세요. "
            "모르는 메뉴는 원문 menu_term에 두세요. 온도가 없으면 null로 두세요. "
            "각 메뉴 주문은 별도 add 단계로 만드세요. '그거'는 같은 메시지의 직전 메뉴로 풀되 "
            "불명확하면 null로 두세요. 수량을 추측하지 마세요. "
            "추천 요청과 '다른 거', '잘 모르겠어요'는 recommend로 두세요. "
            "메뉴를 고르는 카페 대화에서는 메뉴명이나 '추천'이 없어도 상황·감정·취향을 해석해 recommend로 두세요. "
            "피로, 기분 전환, 입맛 없음, 끼니를 거름, 색다른 경험 등 다양한 표현을 문맥으로 이해하세요. "
            "단순 감정 공유, 추천 거절, 메뉴와 무관한 대화는 information으로 보내 짧게 반응하며 주문 액션을 만들지 마세요. "
            "recommend의 text에는 사용자의 원문과 조건을 보존하세요. 감정에서 메뉴나 카페인 수치를 임의 확정하지 마세요. "
            "'첫 번째'는 ordinal=1, '두 번째'는 ordinal=2, '3번'은 ordinal=3으로 두세요. "
            "수량 변경은 update_quantity, 옵션 추가·제거는 update_option(remove_option을 표시), 메뉴 교체는 replace(replacement_term 포함), "
            "메뉴 삭제는 remove, 장바구니 확인은 show_cart, 전체 취소는 clear_cart, 주문 마무리는 finish_ordering, "
            "최종 확인은 confirm_order, 매장·포장은 set_dine_option으로 두세요. "
            "결제 수단 선택은 select_payment(payment_method=card/easy_pay/cash), 새 주문 시작은 new_order로 두세요. "
            "한 주문의 HOT/ICE가 충돌하면 temperature_conflict로 두고 메뉴명·수량·옵션은 보존하세요. "
            "빠른 주문/대화 주문 방식 전환은 set_order_mode(order_mode 포함)로 두세요. "
            "같은 메뉴 두 잔 중 한 잔만 옵션이 다르면 두 개의 add로 나누세요. "
            "옵션 수량은 option_quantity에 두고 메뉴 수량과 구분하세요. "
            "이전 추천의 순서·메뉴 ID는 문맥에서 보되 추측하지 마세요. "
            "옵션만 바꾸는 요청은 update_option으로 두세요.\n"
            "보류 온도 질문에 대한 답은 answer_pending(temperature 포함)으로 두고 기존 메뉴의 add를 반복하지 마세요. "
            "같은 발화의 추가 주문은 그 뒤에 별도 add로 보존하세요. "
            "최종 확인에서 '네, 그런데 ... 추가'는 confirm_order가 아니라 추가·변경 요청입니다.\n"
            f"실제 메뉴 이름: {json.dumps(menu_names, ensure_ascii=False)}\n"
            f"옵션 이름: {json.dumps([o['name'] for o in OPTIONS], ensure_ascii=False)}\n"
            f"장바구니/이전 문맥: {json.dumps(context, ensure_ascii=False)}\n"
            f"사용자 입력: {message}"
        )
        return self.model.invoke(prompt)
