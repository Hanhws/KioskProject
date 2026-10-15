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
        self.assertIn("개발·시험용 가짜 RAG", body["answer"])
        self.assertEqual(body["evidence_document_ids"], ["FAKE-RAG-001"])
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
