import unittest

from fastapi.testclient import TestClient

from api.index import create_app


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

    def ask(self, question: str, *, prior_conditions: dict | None = None) -> dict:
        self.questions.append(question)
        return {
            "answer": "주입된 RAG의 시험 답변",
            "status": "ok",
            "choices": [],
            "related_menu_ids": [],
            "evidence_document_ids": ["TEST-EVIDENCE"],
            "cart_actions": [{"type": "clear_cart"}],
        }


class ChatApiTests(unittest.TestCase):
    def test_default_fake_accepts_frontend_request_without_key(self):
        with TestClient(create_app()) as client:
            response = client.post("/api/chat", json=chat_request("아메리카노 가격 알려 주세요"))
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "ok")
        self.assertIn("개발·시험용 가짜 RAG", body["answer"])
        self.assertEqual(body["evidence_document_ids"], ["FAKE-RAG-001"])
        self.assertNotIn("cart_actions", body)

    def test_injected_rag_receives_question_and_adapter_omits_actions(self):
        rag = RecordingRAG()
        with TestClient(create_app(rag)) as client:
            response = client.post("/api/chat", json=chat_request("정보 질문"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(rag.questions, ["정보 질문"])
        self.assertEqual(response.json()["evidence_document_ids"], ["TEST-EVIDENCE"])
        self.assertNotIn("cart_actions", response.json())

    def test_missing_frontend_context_is_rejected(self):
        with TestClient(create_app()) as client:
            response = client.post("/api/chat", json={"message": "아메리카노 가격 알려 주세요"})
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
