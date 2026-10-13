"""프론트엔드 ↔ RAG 연결 API (Vercel Python 함수).

POST /api/chat   채팅 한 턴 처리
GET  /api/health 서버·인덱스 상태 확인

요청 종류 구분·복합 요청 분해·결과 병합은 통합 담당 영역이다.
지금은 RAG 단일 질문 호출만 하고, 장바구니 변경은 cart_actions(빈 목록)로 비워 둔다.
통합 라우터가 생기면 route_message()만 바꾸면 된다. 응답 형식은 docs/API.md 참고.
"""

from __future__ import annotations

import json
import os
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Optional

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 로컬 실행 시 .env의 키를 읽는다. Vercel에서는 환경변수 설정값이 우선한다.
load_dotenv(ROOT / ".env", override=False)

# 실행 위치와 관계없이 저장소 안의 데이터·인덱스를 쓰도록 기본 경로를 지정한다.
os.environ.setdefault("MEGACOFFEE_DATA_DIR", str(ROOT / "data" / "megacoffee_rag_enriched"))
os.environ.setdefault("RAG_INDEX_DIR", str(ROOT / "rag" / "rag_index" / "faiss_openai_v1"))
os.environ.setdefault("RAG_EMBEDDING_JSON_PATH", str(ROOT / "rag" / "rag_embeddings.json"))
os.environ.setdefault("RAG_REBUILD_INDEX", "0")


app = FastAPI(title="Cafe Kiosk API", docs_url="/api/docs", openapi_url="/api/openapi.json")

allowed_origins = [
    origin.strip()
    for origin in os.getenv("CORS_ALLOW_ORIGINS", "http://localhost:3000").split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


class HistoryTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class CartContext(BaseModel):
    stage: str = "greeting"
    dine_option: Optional[str] = None
    payment_method: Optional[str] = None
    total_krw: int = 0
    item_count: int = 0
    lines: list[dict[str, Any]] = Field(default_factory=list)
    pending_question: Optional[str] = None


class ChatContext(BaseModel):
    cart: CartContext = Field(default_factory=CartContext)
    last_recommended_menu_ids: list[str] = Field(default_factory=list)
    prior_conditions: Optional[dict[str, Any]] = None


class ChatRequest(BaseModel):
    message: str = Field(max_length=500)
    history: list[HistoryTurn] = Field(default_factory=list, max_length=20)
    context: ChatContext = Field(default_factory=ChatContext)


# 추천 조건 중 다음 턴으로 넘길 항목 (3.8 다른 추천, 3.9 결정하지 못하기)
CARRY_OVER_FIELDS = {
    "temperature", "max_price_krw", "max_caffeine_mg", "decaf_required",
    "required_type", "required_category", "required_sweetness_level",
    "required_coffee_strength", "required_situations", "preferred_terms",
    "clarification_count",
}


@lru_cache(maxsize=1)
def get_service():
    from rag import CafeRAGService, load_config

    return CafeRAGService.from_config(load_config())


def to_jsonable(value: Any) -> Any:
    """numpy 실수 등 JSON으로 바로 못 바꾸는 값을 정리한다."""
    return json.loads(json.dumps(value, ensure_ascii=False, default=lambda item: float(item)))


def build_prior_conditions(request: ChatRequest) -> dict[str, Any]:
    prior = dict(request.context.prior_conditions or {})
    if request.context.cart.pending_question:
        prior["pending_question"] = request.context.cart.pending_question
    if request.context.last_recommended_menu_ids:
        prior["previous_recommended_menu_ids"] = request.context.last_recommended_menu_ids
    return prior


def route_message(request: ChatRequest) -> dict[str, Any]:
    """통합 담당 연결 지점.

    TODO(통합): LLM 라우터로 요청 종류를 나누고, 주문 요청은 cart_actions로,
    정보·추천 질문은 service.ask()로 보내고, 복합 요청은 결과를 하나로 합친다.
    cart_actions 형식은 lib/order/types.ts의 CartAction과 같다.
    """
    service = get_service()
    result = service.ask(request.message, prior_conditions=build_prior_conditions(request))
    applied = result.get("applied_conditions") or {}
    plan = result.get("query_plan") or {}
    carry = None
    if plan.get("intent") in {"recommend", "ambiguous"}:
        carry = {key: value for key, value in applied.items() if key in CARRY_OVER_FIELDS}
    result["cart_actions"] = []
    result["prior_conditions"] = carry
    return result


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "openai_key": bool(os.getenv("OPENAI_API_KEY", "").strip()),
        "index_dir_exists": Path(os.environ["RAG_INDEX_DIR"]).is_dir(),
    }


@app.post("/api/chat")
def chat(request: ChatRequest) -> dict[str, Any]:
    if not os.getenv("OPENAI_API_KEY", "").strip():
        return {
            "answer": "지금은 대화 서버 설정이 끝나지 않아 답변할 수 없어요. 메뉴판에서 직접 골라 주세요.",
            "status": "config_error",
            "choices": [],
            "related_menu_ids": [],
            "evidence_document_ids": [],
            "next_action": None,
            "resume_prompt": None,
            "cart_actions": [],
            "prior_conditions": None,
            "error": "OPENAI_API_KEY is not set",
        }
    try:
        result = route_message(request)
    except Exception as error:  # 서버가 죽지 않도록 오류를 응답으로 돌려준다
        return {
            "answer": "요청을 처리하는 중 오류가 발생했어요. 잠시 후 다시 말씀해 주세요.",
            "status": "server_error",
            "choices": [],
            "related_menu_ids": [],
            "evidence_document_ids": [],
            "next_action": None,
            "resume_prompt": None,
            "cart_actions": [],
            "prior_conditions": None,
            "error": f"{type(error).__name__}: {error}",
        }
    # 화면에 필요 없는 큰 필드는 빼서 응답 크기를 줄인다
    for heavy in ("retrieved_documents", "exact_facts"):
        result.pop(heavy, None)
    return to_jsonable(result)
