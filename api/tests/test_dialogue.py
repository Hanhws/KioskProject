import unittest

from fastapi.testclient import TestClient

from api.dialogue import DialoguePlan, LLMDialoguePlanner, TurnStep
from api.index import create_app


class RecordingRAG:
    def __init__(self):
        self.questions = []

    def ask(self, question, *, prior_conditions=None, recent_history=None):
        self.questions.append(question)
        return {
            "answer": f"[시험 RAG] {question}", "status": "ok", "choices": [],
            "related_menu_ids": [], "evidence_document_ids": ["TEST-EVIDENCE"],
        }


class RecommendationRAG:
    def __init__(self):
        self.conditions = []

    def ask(self, question, *, prior_conditions=None, recent_history=None):
        self.conditions.append(prior_conditions)
        first = not prior_conditions or not prior_conditions.get("excluded_menu_ids")
        ids = ["MENU-008", "MENU-006"] if first else ["MENU-011", "MENU-004"]
        names = ["초코라떼", "카페모카"] if first else ["딸기라떼", "바닐라라떼"]
        return {
            "answer": f"{names[0]}, {names[1]}를 추천해요.",
            "status": "ok", "related_menu_ids": ids, "evidence_document_ids": [],
            "choices": [{"field": "menu_group_id", "value": menu_id, "label": name, "source_id": menu_id}
                        for menu_id, name in zip(ids, names)],
            "applied_conditions": {"required_sweetness_level": "높음", **(prior_conditions or {})},
        }


class PartialRecommendationRAG:
    def ask(self, question, *, prior_conditions=None, recent_history=None):
        return {
            "answer": "추천 정보를 확인하기 어려워요.", "status": "partial",
            "related_menu_ids": [], "evidence_document_ids": [],
            "choices": [{"field": None, "value": "초코라떼", "label": "초코라떼", "source_id": "MENU-008"}],
            "next_action": "choose_menu",
        }


class NoCandidateRecommendationRAG:
    def ask(self, question, *, prior_conditions=None, recent_history=None):
        return {
            "answer": "조건에 맞는 메뉴를 찾지 못했어요.", "status": "no_candidates",
            "related_menu_ids": [], "evidence_document_ids": [],
            "choices": [{"field": "menu_group_id", "value": "MENU-008", "label": "초코라떼", "source_id": None}],
            "next_action": "choose_menu",
        }


class FallbackAlternativeRAG:
    def __init__(self, ids):
        self.ids = ids
        self.questions = []

    def ask(self, question, *, prior_conditions=None, recent_history=None):
        self.questions.append(question)
        return {
            "answer": "[시험 RAG] 비슷한 판매 메뉴를 찾았어요.", "status": "ok",
            "choices": [{"field": "menu_group_id", "value": "NOT-SOLD", "label": "없는 메뉴", "source_id": "NOT-SOLD"}],
            "related_menu_ids": self.ids, "evidence_document_ids": ["TEST-ALT"],
            "next_action": "choose_menu",
        }


def request(message, lines=None):
    return {
        "message": message, "history": [],
        "context": {
            "cart": {"stage": "more", "dine_option": None, "payment_method": None,
                     "total_krw": 0, "item_count": len(lines or []), "lines": lines or [], "pending_question": None},
            "last_recommended_menu_ids": [], "prior_conditions": None,
        },
    }


LATTE_LINE = {"line_id": "L1", "menu_id": "MENU-002", "variant_id": "MEGA-004",
              "menu_name": "카페라떼", "temperature": "ICE", "options": {},
              "option_labels": [], "quantity": 1, "unit_price_krw": 3400, "line_total_krw": 3400}


class FixedPlanner:
    def plan(self, message, context):
        return DialoguePlan(steps=[
            TurnStep(kind="add", text=message, menu_term="아이스 아메리카노", temperature="ICE"),
            TurnStep(kind="add", text=message, menu_term="우주커피"),
        ])


class QualifiedConfirmationPlanner:
    def plan(self, message, context):
        return DialoguePlan(steps=[
            TurnStep(kind="confirm_order", text="네"),
            TurnStep(kind="add", text="쿠키도 추가요", menu_term="쿠키"),
        ])


def live_planner_without_model():
    class ForbiddenModel:
        def invoke(self, prompt):
            raise AssertionError("명확한 주문은 LLM 호출 없이 해석해야 합니다")

    planner = LLMDialoguePlanner.__new__(LLMDialoguePlanner)
    planner.model = ForbiddenModel()
    return planner


def pending_latte(message):
    body = request(message)
    body["context"]["cart"]["pending_question"] = "카페라떼는 따뜻한 것과 아이스 중 어떤 걸로 드릴까요?"
    body["context"]["pending"] = {
        "kind": "temperature", "action": {"type": "add_item", "menu_id": "MENU-002"},
    }
    return body


class DialogueTests(unittest.TestCase):
    def setUp(self):
        self.rag = RecordingRAG()
        self.client = TestClient(create_app(self.rag))

    def tearDown(self):
        self.client.close()

    def chat(self, message, lines=None):
        response = self.client.post("/api/chat", json=request(message, lines))
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_information_question_never_changes_cart(self):
        result = self.chat("아메리카노 얼마예요?", [LATTE_LINE])
        self.assertNotIn("cart_actions", result)
        self.assertEqual(result["evidence_document_ids"], ["TEST-EVIDENCE"])

    def test_two_iced_americanos_use_catalog_id(self):
        result = self.chat("아이스 아메리카노 두 잔 주세요")
        self.assertEqual(result["cart_actions"], [{"type": "add_item", "menu_id": "MENU-001", "temperature": "ICE", "quantity": 2}])
        self.assertEqual(self.rag.questions, [])

    def test_latte_without_temperature_leaves_engine_to_ask(self):
        result = self.chat("카페라떼 하나 주세요")
        self.assertEqual(result["cart_actions"], [{"type": "add_item", "menu_id": "MENU-002"}])

    def test_multiple_menus_keep_action_order(self):
        result = self.chat("아메리카노랑 치즈케이크 주세요")
        self.assertEqual([item["menu_id"] for item in result["cart_actions"]], ["MENU-001", "MENU-028"])

    def test_hti_4_4_keeps_each_quantity(self):
        result = self.chat("아아 두 잔이랑 치즈케이크 하나요")
        self.assertEqual(result["cart_actions"], [
            {"type": "add_item", "menu_id": "MENU-001", "temperature": "ICE", "quantity": 2},
            {"type": "add_item", "menu_id": "MENU-028"},
        ])

    def test_two_drinks_keep_separate_temperatures(self):
        result = self.chat("아이스 아메리카노랑 따뜻한 카페라떼 주세요")
        self.assertEqual([row.get("temperature") for row in result["cart_actions"]], ["ICE", "HOT"])

    def test_information_before_order_in_same_turn(self):
        result = self.chat("라떼 얼마예요? 그거 아이스로 두 잔 주세요")
        self.assertEqual(self.rag.questions, ["라떼 얼마예요"])
        self.assertTrue(result["answer"].startswith("[시험 RAG]"))
        self.assertEqual(result["cart_actions"], [{"type": "add_item", "menu_id": "MENU-002", "temperature": "ICE", "quantity": 2}])

    def test_option_inquiry_and_action_are_distinct(self):
        self.assertNotIn("cart_actions", self.chat("시럽 추가할 수 있어요?", [LATTE_LINE]))
        self.assertEqual(self.rag.questions[-1], "카페라떼에 시럽 추가할 수 있어요")
        self.assertNotIn("cart_actions", self.chat("시럽 추가해 주세요", [LATTE_LINE]))
        result = self.chat("라떼에 바닐라 시럽 추가해 주세요", [LATTE_LINE])
        self.assertEqual(result["cart_actions"], [{"type": "update_item", "target": {"menu_id": "MENU-002"}, "set_options": {"OPT-003": 1}}])

    def test_unknown_menu_only_blocks_its_own_action(self):
        result = self.chat("아이스 아메리카노랑 우주커피 주세요")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["cart_actions"], [{"type": "add_item", "menu_id": "MENU-001", "temperature": "ICE"}])
        self.assertIn("우주커피", result["answer"])

    def test_registered_unavailable_menu_uses_mock_rag_rule_without_ordering(self):
        with TestClient(create_app()) as client:
            result = client.post("/api/chat", json=request("브라우니 주세요")).json()
        self.assertNotIn("cart_actions", result)
        self.assertEqual(result["related_menu_ids"], ["MENU-030", "MENU-032"])
        self.assertEqual([choice["value"] for choice in result["choices"]], result["related_menu_ids"])
        self.assertIn("현재 판매하지 않아요", result["answer"])
        self.assertIn("초코무스케이크", result["answer"])
        self.assertIsNone(result["next_action"])

    def test_live_adapter_uses_team_alternative_rule_when_rag_has_no_candidates(self):
        with TestClient(create_app(RecordingRAG(), live_planner_without_model())) as client:
            result = client.post("/api/chat", json=request("브라우니 주세요")).json()
        self.assertNotIn("cart_actions", result)
        self.assertEqual(result["related_menu_ids"], ["MENU-030", "MENU-032"])
        self.assertEqual([choice["value"] for choice in result["choices"]], result["related_menu_ids"])
        self.assertEqual(result["evidence_document_ids"], [])

    def test_live_planner_reuses_clear_order_and_pending_rules(self):
        planner = live_planner_without_model()
        with TestClient(create_app(RecordingRAG(), planner)) as client:
            latte = client.post("/api/chat", json=request("라떼 하나요")).json()
            iced = client.post("/api/chat", json=request("아이스 아메리카노 하나 주세요")).json()
            mixed = client.post("/api/chat", json=pending_latte("아이스요, 치즈케이크도 주세요")).json()
            quantity = client.post("/api/chat", json=request("카페라떼 세 잔으로 바꿔 주세요", [LATTE_LINE])).json()
        self.assertEqual(latte["cart_actions"], [{"type": "add_item", "menu_id": "MENU-002"}])
        self.assertEqual(iced["cart_actions"], [{"type": "add_item", "menu_id": "MENU-001", "temperature": "ICE"}])
        self.assertEqual(mixed["cart_actions"], [
            {"type": "add_item", "menu_id": "MENU-002", "temperature": "ICE", "pending_resolution": True},
            {"type": "add_item", "menu_id": "MENU-028"},
        ])
        self.assertEqual(quantity["cart_actions"], [
            {"type": "update_item", "target": {"menu_id": "MENU-002"}, "quantity": 3},
        ])

    def test_ruleless_unavailable_menu_uses_rag_candidates_but_only_sold_three(self):
        rag = FallbackAlternativeRAG(["NOT-SOLD", "MENU-030", "MENU-032", "MENU-031", "MENU-028", "MENU-030"])
        with TestClient(create_app(rag)) as client:
            result = client.post("/api/chat", json=request("피자 주세요")).json()
        self.assertEqual(rag.questions, ["피자 메뉴를 주문하려고 해요. 판매하지 않는다면 대안 메뉴를 알려 주세요."])
        self.assertNotIn("cart_actions", result)
        self.assertEqual(result["related_menu_ids"], ["MENU-030", "MENU-032", "MENU-031"])
        self.assertEqual([choice["value"] for choice in result["choices"]], result["related_menu_ids"])
        self.assertIsNone(result["next_action"])

    def test_unavailable_menu_without_alternatives_is_not_ordered(self):
        with TestClient(create_app()) as client:
            result = client.post("/api/chat", json=request("피자 주세요")).json()
        self.assertEqual(result["status"], "not_found")
        self.assertEqual(result["related_menu_ids"], [])
        self.assertEqual(result["choices"], [])
        self.assertNotIn("cart_actions", result)

    def test_unavailable_during_pending_temperature_preserves_resume(self):
        with TestClient(create_app()) as client:
            result = client.post("/api/chat", json=pending_latte("브라우니 주세요")).json()
        self.assertNotIn("cart_actions", result)
        self.assertEqual(result["related_menu_ids"], ["MENU-030", "MENU-032"])
        self.assertEqual(result["next_action"], "resume_pending_question")
        self.assertEqual(result["resume_prompt"], "카페라떼는 따뜻한 것과 아이스 중 어떤 걸로 드릴까요?")

    def test_unavailable_menu_only_blocks_its_own_part_of_multi_order(self):
        with TestClient(create_app()) as client:
            result = client.post("/api/chat", json=request("아이스 아메리카노랑 브라우니 주세요")).json()
        self.assertEqual(result["cart_actions"], [{"type": "add_item", "menu_id": "MENU-001", "temperature": "ICE"}])
        self.assertEqual(result["related_menu_ids"], ["MENU-030", "MENU-032"])
        self.assertEqual(result["status"], "partial")

    def test_typo_confirmation_still_avoids_rag_alternatives(self):
        result = self.chat("바닐라라테 주세요")
        self.assertEqual(result["next_action"], "confirm_menu")
        self.assertEqual(self.rag.questions, [])

    def test_injected_planner_uses_same_validation(self):
        with TestClient(create_app(self.rag, FixedPlanner())) as client:
            result = client.post("/api/chat", json=request("two requests")).json()
        self.assertEqual(len(result["cart_actions"]), 1)
        self.assertIn("우주커피", result["answer"])

    def test_quantity_option_removal_and_menu_replacement_use_engine_actions(self):
        lines = [LATTE_LINE, {**LATTE_LINE, "line_id": "L2", "menu_id": "MENU-001", "menu_name": "아메리카노"}]
        self.assertEqual(self.chat("라떼 두 잔으로 바꿔 주세요", lines)["cart_actions"],
                         [{"type": "update_item", "target": {"menu_id": "MENU-002"}, "quantity": 2}])
        self.assertEqual(self.chat("아까 라떼 샷 추가는 빼 주세요", lines)["cart_actions"],
                         [{"type": "update_item", "target": {"menu_id": "MENU-002"}, "set_options": {"OPT-001": 0}}])
        self.assertEqual(self.chat("아메리카노 말고 카페라떼로 바꿔 주세요", lines)["cart_actions"],
                         [{"type": "replace_menu", "target": {"menu_id": "MENU-001"}, "menu_id": "MENU-002"}])

    def test_other_order_management_actions(self):
        self.assertEqual(self.chat("치즈케이크 빼 주세요", [{**LATTE_LINE, "menu_id": "MENU-028", "menu_name": "치즈케이크"}])["cart_actions"],
                         [{"type": "remove_item", "target": {"menu_id": "MENU-028"}}])
        for utterance, expected in [
            ("지금 뭐 담겼어요? 총 얼마예요?", "show_cart"),
            ("다 취소할게요", "clear_cart"), ("그게 다예요", "finish_ordering"),
            ("이대로 주문할게요", "confirm_order"),
        ]:
            self.assertEqual(self.chat(utterance, [LATTE_LINE])["cart_actions"][0]["type"], expected)
        self.assertEqual(self.chat("포장이요")["cart_actions"], [{"type": "set_dine_option", "value": "takeout"}])
        self.assertEqual([row["type"] for row in self.chat("아아 두 잔 포장이요")["cart_actions"]],
                         ["add_item", "set_dine_option"])
        self.assertNotIn("cart_actions", self.chat("매장에 화장실 있어요?"))

    def test_order_mode_uses_existing_screen_tabs(self):
        self.assertEqual(self.chat("빠른 주문 할게요")["next_action"], "open_menu")
        self.assertEqual(self.chat("대화로 주문할래요")["next_action"], "open_chat")

    def test_different_options_make_two_order_actions(self):
        actions = self.chat("아이스 라떼 두 잔인데 하나는 두유로 해 주세요")["cart_actions"]
        self.assertEqual(actions, [
            {"type": "add_item", "menu_id": "MENU-002", "temperature": "ICE", "quantity": 1},
            {"type": "add_item", "menu_id": "MENU-002", "temperature": "ICE", "options": {"OPT-005": 1}, "quantity": 1},
        ])

    def test_option_quantity_and_whipping_are_distinct_from_drink_quantity(self):
        self.assertEqual(self.chat("샷 3개 추가해 주세요", [LATTE_LINE])["cart_actions"],
                         [{"type": "update_item", "target": {"line_id": "L1"}, "set_options": {"OPT-001": 3}}])
        self.assertEqual(self.chat("카페모카 휘핑 빼 주세요", [{**LATTE_LINE, "menu_id": "MENU-006", "menu_name": "카페모카"}])["cart_actions"],
                         [{"type": "update_item", "target": {"menu_id": "MENU-006"}, "set_options": {"OPT-008": 1}}])
        self.assertEqual(self.chat("라떼 연하게 해 주세요", [LATTE_LINE])["cart_actions"],
                         [{"type": "update_item", "target": {"menu_id": "MENU-002"}, "set_options": {"OPT-002": 1}}])
        self.assertEqual(self.chat("카페라떼 두유로 해 주세요", [LATTE_LINE])["cart_actions"],
                         [{"type": "update_item", "target": {"menu_id": "MENU-002"}, "set_options": {"OPT-005": 1}}])
        chocolate = {**LATTE_LINE, "menu_id": "MENU-008", "menu_name": "초코라떼"}
        self.assertEqual(self.chat("초코라떼 휘핑 올려 주세요", [chocolate])["cart_actions"],
                         [{"type": "update_item", "target": {"menu_id": "MENU-008"}, "set_options": {"OPT-007": 1}}])
        self.assertEqual(self.chat("초코라떼 휘핑 올려 주세요")["cart_actions"],
                         [{"type": "add_item", "menu_id": "MENU-008", "options": {"OPT-007": 1}}])

    def test_ambiguous_latte_removal_is_left_to_engine_target_confirmation(self):
        vanilla = {**LATTE_LINE, "line_id": "L2", "menu_id": "MENU-004", "menu_name": "바닐라라떼"}
        result = self.chat("라떼 빼 주세요", [LATTE_LINE, vanilla])
        self.assertEqual(result["cart_actions"], [{"type": "remove_item", "target": {"menu_ids": ["MENU-002", "MENU-004"]}}])

    def test_temperature_conflict_preserves_draft_until_answer(self):
        result = self.chat("따뜻한 아이스 아메리카노 2잔 샷 추가해 주세요")
        self.assertEqual(result["cart_actions"], [{"type": "add_item", "menu_id": "MENU-001",
                                                  "quantity": 2, "options": {"OPT-001": 1}}])
        body = request("아이스요")
        body["context"]["pending"] = {"kind": "temperature", "action": result["cart_actions"][0]}
        body["context"]["cart"]["pending_question"] = "아메리카노는 따뜻한 것과 아이스 중 어떤 걸로 드릴까요?"
        resolved = self.client.post("/api/chat", json=body).json()
        self.assertEqual(resolved["cart_actions"], [{**result["cart_actions"][0], "temperature": "ICE", "pending_resolution": True}])

    def test_repeated_temperature_conflict_keeps_existing_pending(self):
        body = request("따뜻한 아이스")
        body["context"]["pending"] = {"kind": "temperature", "action": {"type": "add_item", "menu_id": "MENU-002"}}
        body["context"]["cart"]["pending_question"] = "카페라떼는 따뜻한 것과 아이스 중 어떤 걸로 드릴까요?"
        pending = self.client.post("/api/chat", json=body).json()
        self.assertNotIn("cart_actions", pending)
        self.assertEqual(pending["status"], "needs_clarification")

    def test_live_conflict_order_reuses_rules_without_model_call(self):
        planner = LLMDialoguePlanner.__new__(LLMDialoguePlanner)
        result = planner.plan("따뜻한 아이스 아메리카노 2잔 주세요", request("")["context"])
        self.assertEqual(result.steps[0].kind, "temperature_conflict")
        self.assertIn("아메리카노", result.steps[0].menu_term)
        self.assertEqual(result.steps[0].quantity, 2)

    def test_typo_offers_confirmation_without_adding(self):
        result = self.chat("바닐라라테 주세요")
        self.assertNotIn("cart_actions", result)
        self.assertEqual(result["choices"][0]["value"], "MENU-004")
        self.assertEqual(result["next_action"], "confirm_menu")

    def test_information_during_pending_order_requests_resume_without_actions(self):
        body = request("너 AI야?")
        body["context"]["cart"]["pending_question"] = "카페라떼는 따뜻한 것과 아이스 중 어떤 걸로 드릴까요?"
        body["context"]["pending"] = {"kind": "temperature", "action": {"type": "add_item", "menu_id": "MENU-002"}}
        result = self.client.post("/api/chat", json=body).json()
        self.assertNotIn("cart_actions", result)
        self.assertEqual(result["next_action"], "resume_pending_question")
        self.assertEqual(result["resume_prompt"], body["context"]["cart"]["pending_question"])

    def test_pending_temperature_and_new_menu_are_separate_actions(self):
        result = self.client.post("/api/chat", json=pending_latte("아이스요, 치즈케이크도 주세요")).json()
        self.assertEqual(result["cart_actions"], [
            {"type": "add_item", "menu_id": "MENU-002", "temperature": "ICE", "pending_resolution": True},
            {"type": "add_item", "menu_id": "MENU-028"},
        ])

    def test_new_menu_without_temperature_answer_does_not_resolve_pending(self):
        result = self.client.post("/api/chat", json=pending_latte("치즈케이크도 주세요")).json()
        self.assertEqual(result["cart_actions"], [{"type": "add_item", "menu_id": "MENU-028"}])

    def test_qualified_final_confirmation_asks_which_cookie(self):
        body = request("네, 그런데 쿠키도 추가요", [LATTE_LINE])
        body["context"]["cart"]["stage"] = "final_confirm"
        result = self.client.post("/api/chat", json=body).json()
        self.assertNotIn("cart_actions", result)
        self.assertEqual(result["status"], "needs_clarification")
        self.assertEqual([choice["value"] for choice in result["choices"]], ["MENU-026", "MENU-031", "MENU-032"])
        self.assertIsNone(result["next_action"])

    def test_qualified_confirmation_ignores_incorrect_confirm_step(self):
        body = request("네, 그런데 쿠키도 추가요", [LATTE_LINE])
        body["context"]["cart"]["stage"] = "final_confirm"
        with TestClient(create_app(self.rag, QualifiedConfirmationPlanner())) as client:
            result = client.post("/api/chat", json=body).json()
        self.assertNotIn("cart_actions", result)
        self.assertEqual(result["status"], "needs_clarification")

    def test_pending_menu_information_uses_menu_name_and_resumes(self):
        body = request("카페인은요?")
        body["context"]["cart"]["pending_question"] = "카페라떼는 따뜻한 것과 아이스 중 어떤 걸로 드릴까요?"
        body["context"]["pending"] = {"kind": "temperature", "action": {"type": "add_item", "menu_id": "MENU-002"}}
        result = self.client.post("/api/chat", json=body).json()
        self.assertEqual(self.rag.questions[-1], "카페라떼 카페인은요")
        self.assertNotIn("cart_actions", result)
        self.assertEqual(result["next_action"], "resume_pending_question")

    def test_completed_drink_information_and_colloquial_temperature_change(self):
        cake = {**LATTE_LINE, "line_id": "L2", "menu_id": "MENU-028", "menu_name": "치즈케이크", "temperature": None}
        lines = [LATTE_LINE, cake]
        info = self.chat("카페이능ㄴ요?", lines)
        self.assertEqual(self.rag.questions[-1], "카페라떼 카페인만 간결히 알려 주세요")
        self.assertNotIn("cart_actions", info)
        self.assertIsNone(info["next_action"])
        changed = self.chat("ice 요~", lines)
        self.assertEqual(changed["cart_actions"], [
            {"type": "update_item", "target": {"line_id": "L1"}, "temperature": "ICE"},
        ])
        self.assertEqual(len(self.rag.questions), 1)

    def test_temperature_change_only_targets_drinks_and_asks_if_ambiguous(self):
        another = {**LATTE_LINE, "line_id": "L3", "menu_id": "MENU-001", "menu_name": "아메리카노"}
        result = self.chat("ice 요~", [LATTE_LINE, another])
        self.assertEqual(result["cart_actions"], [
            {"type": "update_item", "target": {"menu_ids": ["MENU-002", "MENU-001"]}, "temperature": "ICE"},
        ])
        self.assertEqual(self.rag.questions, [])

    def test_colloquial_cake_category_keeps_explicit_drink_quantity(self):
        result = self.chat("음 아메리카노 2잔이랑 케이꾸")
        self.assertEqual(result["cart_actions"], [{"type": "add_item", "menu_id": "MENU-001", "quantity": 2}])
        self.assertEqual(result["choices"], [])
        self.assertTrue(all(name in result["answer"] for name in ("치즈케이크", "티라미수케이크", "초코무스케이크")))
        self.assertIn("종류와 수량", result["answer"])
        self.assertEqual(self.rag.questions, [])
        self.assertNotIn("MENU-002", result["related_menu_ids"])

    def test_pending_draft_edits_use_structured_pending_target_without_rag(self):
        body = pending_latte("샷 추가")
        body["context"]["pending"]["action"]["menu_id"] = "MENU-001"
        self.assertEqual(self.client.post("/api/chat", json=body).json()["cart_actions"], [
            {"type": "update_item", "target": {"pending": True}, "set_options": {"OPT-001": 1}},
        ])
        body["message"] = "3잔으로 변경해도"
        body["context"]["pending"]["action"]["options"] = {"OPT-001": 1}
        self.assertEqual(self.client.post("/api/chat", json=body).json()["cart_actions"], [
            {"type": "update_item", "target": {"pending": True}, "quantity": 3},
        ])
        body["message"] = "샷 제거 해줘 걍"
        body["context"]["pending"]["action"]["quantity"] = 3
        self.assertEqual(self.client.post("/api/chat", json=body).json()["cart_actions"], [
            {"type": "update_item", "target": {"pending": True}, "set_options": {"OPT-001": 0}},
        ])
        self.assertEqual(self.rag.questions, [])

    def test_live_rule_planner_keeps_clear_draft_edits_and_category_local(self):
        rag = RecordingRAG()
        with TestClient(create_app(rag, live_planner_without_model())) as client:
            draft = client.post("/api/chat", json=pending_latte("샷 추가")).json()
            category = client.post("/api/chat", json=request("음 아메리카노 2잔이랑 케이꾸")).json()
        self.assertEqual(draft["cart_actions"][0]["target"], {"pending": True})
        self.assertEqual(category["cart_actions"][0]["quantity"], 2)
        self.assertEqual(rag.questions, [])

    def test_same_menu_in_cart_and_pending_draft_requires_target(self):
        body = pending_latte("샷 추가")
        body["context"]["cart"]["lines"] = [LATTE_LINE]
        result = self.client.post("/api/chat", json=body).json()
        self.assertNotIn("cart_actions", result)
        self.assertIn("어느 쪽", result["answer"])
        self.assertEqual(self.rag.questions, [])

    def test_recommendation_order_first_item_and_exclusion_across_turns(self):
        rag = RecommendationRAG()
        with TestClient(create_app(rag)) as client:
            first = client.post("/api/chat", json=request("달콤한 음료 추천해 주세요")).json()
            self.assertEqual(first["related_menu_ids"][:2], ["MENU-008", "MENU-006"])
            self.assertEqual(first["next_action"], "choose_menu")
            choose = request("첫 번째 걸로 주세요")
            choose["context"]["last_recommended_menu_ids"] = first["related_menu_ids"]
            self.assertEqual(client.post("/api/chat", json=choose).json()["cart_actions"],
                             [{"type": "add_item", "menu_id": "MENU-008"}])
            other = request("다른 거 추천해 줘")
            other["context"]["last_recommended_menu_ids"] = first["related_menu_ids"]
            other["context"]["prior_conditions"] = first["prior_conditions"]
            second = client.post("/api/chat", json=other).json()
            self.assertEqual(second["related_menu_ids"][:2], ["MENU-011", "MENU-004"])
            self.assertEqual(rag.conditions[-1]["excluded_menu_ids"], ["MENU-008", "MENU-006"])
            self.assertEqual(rag.conditions[-1]["required_sweetness_level"], "높음")

    def test_second_recommendation_uses_related_id_order(self):
        rag = RecommendationRAG()
        with TestClient(create_app(rag)) as client:
            first = client.post("/api/chat", json=request("달콤한 음료 추천해 주세요")).json()
            choose = request("두 번째 걸로 주세요")
            choose["context"]["last_recommended_menu_ids"] = first["related_menu_ids"]
            result = client.post("/api/chat", json=choose).json()
        self.assertEqual(result["cart_actions"], [{"type": "add_item", "menu_id": "MENU-006"}])

    def test_partial_recommendation_does_not_offer_unverified_menu_card(self):
        with TestClient(create_app(PartialRecommendationRAG())) as client:
            result = client.post("/api/chat", json=request("달달한 음료 추천해 줘")).json()
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["related_menu_ids"], [])
        self.assertEqual(result["choices"], [])
        self.assertIsNone(result["next_action"])

    def test_no_candidate_recommendation_does_not_offer_stale_menu_card(self):
        with TestClient(create_app(NoCandidateRecommendationRAG())) as client:
            result = client.post("/api/chat", json=request("달달한 음료 추천해 줘")).json()
        self.assertEqual(result["status"], "no_candidates")
        self.assertEqual(result["related_menu_ids"], [])
        self.assertEqual(result["choices"], [])
        self.assertIsNone(result["next_action"])

    def test_indecision_count_passed_to_rag_for_hti_3_9(self):
        rag = RecommendationRAG()
        body = request("잘 모르겠어요")
        body["context"]["prior_conditions"] = {"clarification_count": 1}
        with TestClient(create_app(rag)) as client:
            client.post("/api/chat", json=body)
        self.assertEqual(rag.conditions[-1]["clarification_count"], 2)


class PaymentRequestTests(unittest.TestCase):
    def test_recommendation_recovery_asks_one_question_without_resolving_pending_order(self):
        class RecoveryRAG:
            def ask(self, question, **kwargs):
                return {"answer": "조건을 하나 바꿔서 찾아볼까요?", "status": "no_candidates",
                        "choices": [{"field": "free_text", "value": "free_text", "label": "조건 바꾸기", "source_id": None}],
                        "related_menu_ids": [], "evidence_document_ids": []}

        body = pending_latte("천 원 이하로 추천해 주세요")
        with TestClient(create_app(RecoveryRAG())) as client:
            result = client.post("/api/chat", json=body).json()
        self.assertNotIn("cart_actions", result)
        self.assertIsNone(result["next_action"])
        self.assertIsNone(result["resume_prompt"])
        self.assertEqual(result["answer"].count("?"), 1)

    def test_situation_turn_uses_llm_plan_and_preserves_pending_order(self):
        class SituationModel:
            def invoke(self, prompt):
                self.prompt = prompt
                return DialoguePlan(steps=[TurnStep(kind="recommend", text="과제하느라 밤을 새웠어요")])

        model = SituationModel()
        planner = LLMDialoguePlanner.__new__(LLMDialoguePlanner)
        planner.model = model
        body = pending_latte("과제하느라 밤을 새웠어요")
        body["context"]["prior_conditions"] = {"max_price_krw": 3000, "excluded_allergens": ["우유"]}
        body["history"] = [{"role": "assistant", "content": "어떤 메뉴가 좋으세요?"}]
        rag = RecordingRAG()
        with TestClient(create_app(rag, planner=planner)) as client:
            result = client.post("/api/chat", json=body).json()
        self.assertIn("과제하느라 밤을 새웠어요", model.prompt)
        self.assertIn("어떤 메뉴가 좋으세요?", model.prompt)
        self.assertEqual(rag.questions, ["과제하느라 밤을 새웠어요"])
        self.assertNotIn("cart_actions", result)
        self.assertEqual(result["next_action"], "resume_pending_question")
        self.assertEqual(result["resume_prompt"], body["context"]["cart"]["pending_question"])
        self.assertEqual(result["prior_conditions"], body["context"]["prior_conditions"])

    def test_emotion_sharing_can_remain_information_without_forced_recommendation(self):
        class SharingModel:
            def invoke(self, prompt):
                return DialoguePlan(steps=[TurnStep(kind="information", text="그냥 기분 이야기만 하고 싶어요")])

        planner = LLMDialoguePlanner.__new__(LLMDialoguePlanner)
        planner.model = SharingModel()
        with TestClient(create_app(RecordingRAG(), planner=planner)) as client:
            result = client.post("/api/chat", json=request("그냥 기분 이야기만 하고 싶어요")).json()
        self.assertNotIn("cart_actions", result)
        self.assertEqual(result["related_menu_ids"], [])
        self.assertIsNone(result["next_action"])

    def test_information_with_spurious_pending_step_resumes_original_question(self):
        class SpuriousPendingPlanner:
            def plan(self, message, context):
                return DialoguePlan(steps=[TurnStep(kind="information", text=message),
                                           TurnStep(kind="answer_pending", text=message)])
        with TestClient(create_app(RecordingRAG(), planner=SpuriousPendingPlanner())) as client:
            result = client.post("/api/chat", json=pending_latte("카페이능ㄴ요?")).json()
        self.assertEqual(result["status"], "ok")
        self.assertIsNone(result.get("cart_actions"))
        self.assertEqual(result["next_action"], "resume_pending_question")
        self.assertIn("카페라떼는 따뜻한 것", result["resume_prompt"])
        self.assertNotIn("확인하기 어려워요", result["answer"])

    def test_screen_commands_reuse_order_actions_without_model_or_rag(self):
        rag = RecordingRAG()
        cases = [
            ("장바구니 보기", "more", {"type": "show_cart"}),
            ("주문 마치기", "more", {"type": "finish_ordering"}),
            ("네, 주문할게요", "final_confirm", {"type": "confirm_order"}),
            ("결제 수단 바꾸기", "pay_confirm", {"type": "confirm_order"}),
            ("카드로 결제할게요", "payment", {"type": "select_payment", "method": "card"}),
            ("간편결제로 할게요", "payment", {"type": "select_payment", "method": "easy_pay"}),
            ("현금", "payment", {"type": "select_payment", "method": "cash"}),
            ("새 주문 시작", "complete", {"type": "new_order"}),
            ("전체 취소", "more", {"type": "clear_cart"}),
        ]
        with TestClient(create_app(rag, planner=live_planner_without_model())) as client:
            for message, stage, action in cases:
                with self.subTest(message=message):
                    body = request(message, [LATTE_LINE])
                    body["context"]["cart"]["stage"] = stage
                    result = client.post("/api/chat", json=body)
                    self.assertEqual(result.status_code, 200)
                    self.assertEqual(result.json()["cart_actions"], [action])
        self.assertEqual(rag.questions, [])

    def test_new_order_does_not_erase_an_unfinished_order(self):
        with TestClient(create_app(RecordingRAG())) as client:
            for body in [request("새 주문 시작", [LATTE_LINE]), pending_latte("새 주문 시작")]:
                result = client.post("/api/chat", json=body).json()
                self.assertIsNone(result.get("cart_actions"))
                self.assertIn("전체 취소", result["answer"])

    def test_payment_phrase_with_menu_change_is_not_a_standalone_command(self):
        from api.dialogue import control_step
        context = {"cart": {"stage": "pay_confirm"}}
        for message in ["카드로 할게요, 케이크도 추가요", "새 주문 시작하면 기존 주문은 없어져요?", "전체 취소 가능한가요?"]:
            self.assertIsNone(control_step(message, context))

    def test_payment_request_uses_current_stage_without_model_or_rag(self):
        rag = RecordingRAG()
        with TestClient(create_app(rag, planner=live_planner_without_model())) as client:
            for message in ["결제하기", "결제해 줘", "결제해 주세요", "결제할게요", "계산하기"]:
                for stage, action in [("more", "finish_ordering"), ("final_confirm", "confirm_order"), ("pay_confirm", "pay_now")]:
                    with self.subTest(message=message, stage=stage):
                        body = request(message, [LATTE_LINE])
                        body["context"]["cart"].update(stage=stage, dine_option="takeout", payment_method="card")
                        response = client.post("/api/chat", json=body)
                        self.assertEqual(response.status_code, 200)
                        self.assertEqual(response.json()["cart_actions"], [{"type": action}])
        self.assertEqual(rag.questions, [])

    def test_payment_request_does_not_reset_unfinished_selection(self):
        rag = RecordingRAG()
        with TestClient(create_app(rag)) as client:
            for stage, question in [("payment", "카드와 간편결제"), ("dine_option", "매장에서 드시나요")]:
                body = request("결제하기", [LATTE_LINE])
                body["context"]["cart"]["stage"] = stage
                result = client.post("/api/chat", json=body).json()
                self.assertIsNone(result.get("cart_actions"))
                self.assertIn(question, result["answer"])
            body = request("결제하기")
            body["context"]["pending"] = {"kind": "temperature", "action": {"type": "add_item", "menu_id": "MENU-002"}}
            body["context"]["cart"]["pending_question"] = "카페라떼는 따뜻한 것과 아이스 중 어떤 걸로 드릴까요?"
            result = client.post("/api/chat", json=body).json()
            self.assertIsNone(result.get("cart_actions"))
            self.assertIn("카페라떼는 따뜻한 것", result["answer"])
        self.assertEqual(rag.questions, [])

    def test_presentation_cart_total_and_natural_quantity_change_are_actions(self):
        americano = {
            "line_id": "L1", "menu_id": "MENU-001", "variant_id": "MEGA-002",
            "menu_name": "아메리카노", "temperature": "ICE", "options": {},
            "option_labels": [], "quantity": 2, "unit_price_krw": 2500, "line_total_krw": 5000,
        }
        with TestClient(create_app(RecordingRAG())) as client:
            show = client.post("/api/chat", json=request("지금까지 얼마예요?", [americano])).json()
            update = client.post("/api/chat", json=request("아메리카노 한 잔만 할게요.", [americano])).json()
        self.assertEqual(show["cart_actions"], [{"type": "show_cart"}])
        self.assertEqual(update["cart_actions"], [{
            "type": "update_item", "target": {"menu_id": "MENU-001"}, "quantity": 1,
        }])


if __name__ == "__main__":
    unittest.main()
