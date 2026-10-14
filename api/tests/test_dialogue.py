import unittest

from fastapi.testclient import TestClient

from api.dialogue import DialoguePlan, TurnStep
from api.index import create_app


class RecordingRAG:
    def __init__(self):
        self.questions = []

    def ask(self, question, *, prior_conditions=None):
        self.questions.append(question)
        return {
            "answer": f"[시험 RAG] {question}", "status": "ok", "choices": [],
            "related_menu_ids": [], "evidence_document_ids": ["TEST-EVIDENCE"],
        }


class RecommendationRAG:
    def __init__(self):
        self.conditions = []

    def ask(self, question, *, prior_conditions=None):
        self.conditions.append(prior_conditions)
        first = not prior_conditions or not prior_conditions.get("excluded_menu_ids")
        ids = ["MENU-008", "MENU-006"] if first else ["MENU-011", "MENU-004"]
        names = ["초코라떼", "카페모카"] if first else ["딸기라떼", "바닐라라떼"]
        return {
            "answer": f"{names[0]}, {names[1]}를 추천해요.",
            "status": "ok", "related_menu_ids": sorted(ids), "evidence_document_ids": [],
            "choices": [{"field": "menu_group_id", "value": menu_id, "label": name, "source_id": menu_id}
                        for menu_id, name in zip(ids, names)],
            "applied_conditions": prior_conditions or {"required_sweetness_level": "높음"},
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

    def test_temperature_conflict_does_not_create_order_action(self):
        result = self.chat("따뜻한 아이스 아메리카노 주세요")
        self.assertEqual(result["status"], "needs_clarification")
        self.assertNotIn("cart_actions", result)
        self.assertIn("따뜻한 것과 아이스", result["answer"])
        body = request("따뜻한 아이스")
        body["context"]["cart"]["pending_question"] = "카페라떼는 따뜻한 것과 아이스 중 어떤 걸로 드릴까요?"
        pending = self.client.post("/api/chat", json=body).json()
        self.assertNotIn("cart_actions", pending)
        self.assertEqual(pending["status"], "needs_clarification")

    def test_typo_offers_confirmation_without_adding(self):
        result = self.chat("바닐라라테 주세요")
        self.assertNotIn("cart_actions", result)
        self.assertEqual(result["choices"][0]["value"], "MENU-004")
        self.assertEqual(result["next_action"], "confirm_menu")

    def test_information_during_pending_order_requests_resume_without_actions(self):
        body = request("너 AI야?")
        body["context"]["cart"]["pending_question"] = "카페라떼는 따뜻한 것과 아이스 중 어떤 걸로 드릴까요?"
        result = self.client.post("/api/chat", json=body).json()
        self.assertNotIn("cart_actions", result)
        self.assertEqual(result["next_action"], "resume_pending_question")
        self.assertEqual(result["resume_prompt"], body["context"]["cart"]["pending_question"])

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

    def test_indecision_count_passed_to_rag_for_hti_3_9(self):
        rag = RecommendationRAG()
        body = request("잘 모르겠어요")
        body["context"]["prior_conditions"] = {"clarification_count": 1}
        with TestClient(create_app(rag)) as client:
            client.post("/api/chat", json=body)
        self.assertEqual(rag.conditions[-1]["clarification_count"], 2)


if __name__ == "__main__":
    unittest.main()
