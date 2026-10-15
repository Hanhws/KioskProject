"""Fixed development responses. This module never imports or initializes the real RAG."""

import re

from typing import Any

from .dialogue import CATALOG


class FakeRAG:
    def ask(
        self,
        question: str,
        *,
        prior_conditions: dict | None = None,
        recent_history: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        def response(answer: str, *, status: str = "ok", menu_ids: list[str] | None = None,
                     evidence_ids: list[str] | None = None) -> dict[str, Any]:
            ids = menu_ids or []
            names = {menu["id"]: menu["name"] for menu in CATALOG["menus"]}
            return {
                "answer": answer, "status": status,
                "choices": [
                    {"field": "menu_group_id", "value": menu_id, "label": names[menu_id], "source_id": menu_id}
                    for menu_id in ids if menu_id in names
                ],
                "related_menu_ids": ids, "evidence_document_ids": evidence_ids or [],
                "next_action": None, "resume_prompt": None, "error": None,
            }

        if "날씨" in question:
            return response(
                "날씨 정보는 확인할 수 없어요. 카페 메뉴 주문과 가격·메뉴 추천을 도와드릴게요.",
                status="out_of_scope", evidence_ids=["FAQ-007"],
            )
        if "카페모카" in question and "칼로리" in question:
            return response(
                "제공된 자료에는 카페모카의 칼로리 정보가 없어 확인할 수 없어요. 임의의 수치는 안내하지 않을게요.",
                status="not_found", menu_ids=["MENU-006"],
                evidence_ids=["DETAIL-MEGA-011", "DETAIL-MEGA-012"],
            )
        if ("알레르기" in question or "알러지" in question) and "우유" in question:
            return response(
                "우유 알레르기가 있다고 기록된 메뉴는 제외할 수 있지만, 알레르기 정보가 빈 메뉴도 있어 안전을 보장할 수 없어요. 주문 전에 직원이나 매장에 원재료와 교차 접촉 여부를 확인해 주세요.",
                status="needs_clarification", evidence_ids=["FAQ-006"],
            )
        if "아메리카노" in question and ("사이즈" in question or "용량" in question):
            return response(
                "아메리카노는 사이즈 선택이 없어요. 단일 용량은 HOT 355ml, ICE 473ml이며, 별도의 중 사이즈 가격은 없습니다.",
                menu_ids=["MENU-001"], evidence_ids=["DETAIL-MEGA-001", "DETAIL-MEGA-002"],
            )
        if "아메리카노" in question and "카푸치노" in question and ("진" in question or "가격" in question or "얼마" in question):
            return response(
                "팀 정의 맛 특성에서는 아메리카노가 '진함', 카푸치노가 '보통'이라 아메리카노가 더 진해요. 가격은 아메리카노 HOT 2,200원·ICE 2,500원, 카푸치노 HOT·ICE 모두 3,400원이에요.",
                menu_ids=["MENU-001", "MENU-003"], evidence_ids=["MENU-001", "MENU-003"],
            )
        if "아메리카노" in question and ("가격" in question or "얼마" in question):
            return response(
                "아메리카노는 HOT 2,200원, ICE 2,500원이에요.",
                menu_ids=["MENU-001"], evidence_ids=["DETAIL-MEGA-001", "DETAIL-MEGA-002"],
            )
        if "추천" in question and "카페인" in question and re.search(r"달달|달콤", question):
            ids = [
                menu["id"] for menu in CATALOG["menus"]
                if menu["type"] == "음료" and menu.get("sweetness") == "높음"
                and menu.get("variants") and all(variant.get("caffeineMg") == 0 for variant in menu["variants"])
            ][:3]
            names = [menu["name"] for menu in CATALOG["menus"] if menu["id"] in ids]
            return response(
                f"카페인 0mg이 확인되고 당도가 높은 메뉴로 {', '.join(names)}를 추천해요.",
                menu_ids=ids, evidence_ids=ids,
            )
        if "추천" in question and re.search(r"커피\s*말고|비커피|논커피", question):
            ids = ["MENU-008", "MENU-019", "MENU-022"]
            return response(
                "커피가 아닌 음료로 초코라떼, 레몬에이드, 딸기 주스를 추천해요. 부드러운 음료, 탄산, 과일 주스 중 취향에 맞게 골라 주세요.",
                menu_ids=ids, evidence_ids=ids,
            )
        if "추천" in question and re.search(r"달달|달콤", question):
            ids = ["MENU-008", "MENU-010", "MENU-019"]
            return response(
                "당도가 높은 메뉴로 초코라떼, 고구마라떼, 레몬에이드를 추천해요.",
                menu_ids=ids, evidence_ids=ids,
            )
        if re.search(r"뜨거|따뜻", question) and "싫" in question and re.search(r"(?:달|단).*싫|안\s*(?:달|단)", question):
            excluded = set((prior_conditions or {}).get("excluded_menu_ids", []))
            ids = [
                menu["id"] for menu in CATALOG["menus"]
                if menu["id"] not in excluded and menu["type"] == "음료"
                and "ICE" in menu.get("temperatures", []) and menu.get("sweetness") in {"낮음", "보통"}
            ][:3]
            names = [menu["name"] for menu in CATALOG["menus"] if menu["id"] in ids]
            result = response(
                f"아이스로 마실 수 있고 너무 달지 않은 메뉴로 {', '.join(names)}를 추천해요.",
                menu_ids=ids, evidence_ids=ids,
            )
            result["applied_conditions"] = {
                **(prior_conditions or {}), "temperature": "ICE", "max_sweetness_level": "보통",
            }
            return result
        rule_index = next((index for index, row in enumerate(CATALOG["unavailable"])
                           if row["requested"] in question), None)
        rule = CATALOG["unavailable"][rule_index] if rule_index is not None else None
        if rule:
            valid_ids = {menu["id"] for menu in CATALOG["menus"]}
            alternatives = [menu_id for menu_id in rule["alternativeMenuIds"] if menu_id in valid_ids][:3]
            names = [menu["name"] for menu in CATALOG["menus"] if menu["id"] in alternatives]
            return response(
                f"{rule['requested']}은(는) 현재 판매하지 않아요. 대신 {', '.join(names)}를 추천해요. 팀 정의 대체 기준은 {rule['reason']}예요.",
                menu_ids=alternatives, evidence_ids=[f"ALT-{rule_index + 1:03d}"],
            )
        return response(
            "제공된 메뉴 자료에서 답을 확인하지 못했어요. 메뉴명이나 조건을 조금 더 구체적으로 알려 주세요.",
            status="not_found",
        )
