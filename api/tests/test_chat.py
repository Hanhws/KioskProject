import unittest

from fastapi.testclient import TestClient

from api.index import create_app
from api.live_rag import LiveRAG


def chat_request(message: str) -> dict:
    return {
        "message": message,
        "history": [{"role": "assistant", "content": "안녕하세요."}],
        "context": {
            "cart": {
                "stage": "more",
                "dine_option": None,
                "payment_method": None,
                "total_krw": 3900,
                "item_count": 1,
                "lines": [{
                    "line_id": "L1",
                    "menu_id": "MENU-002",
                    "variant_id": "MEGA-004",
                    "menu_name": "카페라떼",
                    "temperature": "ICE",
                    "options": {"OPT-001": 1},
                    "option_labels": ["샷 추가"],
                    "quantity": 1,
                    "unit_price_krw": 3900,
                    "line_total_krw": 3900,
                }],
                "pending_question": "더 필요하신 건 없으세요?",
            },
            "last_recommended_menu_ids": [],
            "prior_conditions": None,
        },
    }


class RecordingRAG:
    def __init__(self):
        self.questions = []
        self.histories = []

    def ask(
        self,
        question: str,
        *,
        prior_conditions: dict | None = None,
        recent_history: list[dict[str, str]] | None = None,
    ) -> dict:
        self.questions.append(question)
        self.histories.append(recent_history)
        return {
            "answer": "주입된 RAG의 시험 답변",
            "status": "ok",
            "choices": [],
            "related_menu_ids": [],
            "evidence_document_ids": ["TEST-EVIDENCE"],
            "cart_actions": [{"type": "clear_cart"}],
        }


class ChatApiTests(unittest.TestCase):
    def test_health_endpoint_reports_server_liveness(self):
        with TestClient(create_app()) as client:
            response = client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_mock_mode_answers_presentation_information_without_inventing_values(self):
        cases = [
            ("아메리카노 얼마예요?", ["HOT 2,200원", "ICE 2,500원"]),
            ("아메리카노 중 사이즈 얼마예요?", ["사이즈 선택이 없어요", "HOT 355ml", "ICE 473ml"]),
            ("카페모카 칼로리 얼마예요?", ["자료에는", "확인할 수 없어요"]),
            ("오늘 날씨 어때요?", ["날씨 정보는 확인할 수 없어요", "카페 메뉴"]),
        ]
        with TestClient(create_app()) as client:
            for message, expected in cases:
                with self.subTest(message=message):
                    response = client.post("/api/chat", json=chat_request(message))
                    self.assertEqual(response.status_code, 200)
                    answer = response.json()["answer"]
                    for fragment in expected:
                        self.assertIn(fragment, answer)

    def test_mock_mode_keeps_both_parts_of_comparison_question(self):
        message = "아메리카노랑 카푸치노 중 뭐가 더 진해요? 가격도 알려 주세요."
        with TestClient(create_app()) as client:
            response = client.post("/api/chat", json=chat_request(message))
        answer = response.json()["answer"]
        self.assertIn("아메리카노가 더 진해요", answer)
        self.assertIn("아메리카노 HOT 2,200원", answer)
        self.assertIn("카푸치노 HOT·ICE 모두 3,400원", answer)

    def test_mock_mode_recommendations_only_use_confirmed_zero_caffeine(self):
        with TestClient(create_app()) as client:
            response = client.post("/api/chat", json=chat_request("카페인 없고 달달한 음료 추천해 주세요."))
        body = response.json()
        self.assertEqual(body["related_menu_ids"], ["MENU-011", "MENU-019", "MENU-020"])
        self.assertIn("카페인 0mg이 확인", body["answer"])

    def test_mock_mode_greeting_recommendation_buttons_return_real_menu_choices(self):
        cases = [
            ("커피 말고 다른 거 추천해 주세요", ["MENU-008", "MENU-019", "MENU-022"]),
            ("달콤한 음료 추천해 주세요", ["MENU-008", "MENU-010", "MENU-019"]),
        ]
        with TestClient(create_app()) as client:
            for message, expected_ids in cases:
                with self.subTest(message=message):
                    body = client.post("/api/chat", json=chat_request(message)).json()
                    self.assertEqual(body["status"], "ok")
                    self.assertEqual(body["related_menu_ids"], expected_ids)
                    self.assertEqual([choice["value"] for choice in body["choices"]], expected_ids)

    def test_mock_mode_unavailable_menu_uses_matching_evidence(self):
        with TestClient(create_app()) as client:
            body = client.post("/api/chat", json=chat_request("브라우니 있어요?")).json()
        self.assertEqual(body["evidence_document_ids"], ["ALT-006"])

    def test_mock_mode_refines_previous_recommendation_from_natural_preferences(self):
        body = chat_request("뜨거운 거 싫어 너무 단 것도 싫어")
        body["context"]["last_recommended_menu_ids"] = ["MENU-008", "MENU-010", "MENU-019"]
        with TestClient(create_app()) as client:
            result = client.post("/api/chat", json=body).json()
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["related_menu_ids"])
        self.assertTrue(set(result["related_menu_ids"]).isdisjoint(body["context"]["last_recommended_menu_ids"]))
        self.assertIn("아이스로 마실 수 있고 너무 달지 않은", result["answer"])
        self.assertEqual(result["prior_conditions"]["temperature"], "ICE")
        self.assertEqual(result["prior_conditions"]["max_sweetness_level"], "보통")

    def test_live_adapter_forwards_history_without_initializing_real_rag(self):
        class StubService:
            def __init__(self):
                self.calls = []

            def ask(self, question, *, prior_conditions=None, recent_history=None):
                self.calls.append((question, prior_conditions, recent_history))
                return {
                    "answer": "시험 답변", "status": "ok", "choices": [],
                    "related_menu_ids": [], "evidence_document_ids": [],
                }

        backend = StubService()
        rag = LiveRAG()
        rag.service = backend
        body = chat_request("아메리카노 가격 알려 주세요")
        body["context"]["prior_conditions"] = {"requested_fields": ["가격"]}
        with TestClient(create_app(rag)) as client:
            response = client.post("/api/chat", json=body)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(backend.calls, [(
            "아메리카노 가격 알려 주세요",
            {"requested_fields": ["가격"], "_dialogue_intent": "information"},
            [{"role": "assistant", "content": "안녕하세요."}],
        )])
        self.assertNotIn("cart_actions", response.json())
        self.assertNotIn("_dialogue_intent", response.json().get("prior_conditions", {}))

    def test_default_fake_accepts_frontend_request_without_key(self):
        with TestClient(create_app()) as client:
            response = client.post("/api/chat", json=chat_request("아메리카노 가격 알려 주세요"))
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "ok")
        self.assertIn("HOT 2,200원", body["answer"])
        self.assertIn("ICE 2,500원", body["answer"])
        self.assertEqual(body["evidence_document_ids"], ["DETAIL-MEGA-001", "DETAIL-MEGA-002"])
        self.assertNotIn("cart_actions", body)

    def test_injected_rag_receives_question_and_adapter_omits_actions(self):
        rag = RecordingRAG()
        with TestClient(create_app(rag)) as client:
            response = client.post("/api/chat", json=chat_request("정보 질문"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(rag.questions, ["정보 질문"])
        self.assertEqual(rag.histories, [[{"role": "assistant", "content": "안녕하세요."}]])
        self.assertEqual(response.json()["evidence_document_ids"], ["TEST-EVIDENCE"])
        self.assertNotIn("cart_actions", response.json())

    def test_missing_frontend_context_is_rejected(self):
        with TestClient(create_app()) as client:
            response = client.post("/api/chat", json={"message": "아메리카노 가격 알려 주세요"})
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
