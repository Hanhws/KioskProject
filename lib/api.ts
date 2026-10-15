import type { cartSnapshot } from "./order/engine";
import type { CartAction } from "./order/types";

export interface RagChoice {
  field: string | null;
  value: string;
  label: string;
  source_id: string | null;
}

/** POST /api/chat 응답. RAG ask() 반환값 + 통합 영역 필드(cart_actions, prior_conditions) */
export interface ChatResponse {
  answer: string;
  status: string;
  choices: RagChoice[];
  related_menu_ids: string[];
  evidence_document_ids?: string[];
  next_action: string | null;
  resume_prompt: string | null;
  follow_up_question?: string | null;
  cart_actions?: CartAction[];
  prior_conditions?: Record<string, unknown> | null;
  error: string | null;
}

export interface ChatRequest {
  message: string;
  history: { role: "user" | "assistant"; content: string }[];
  context: {
    cart: ReturnType<typeof cartSnapshot>;
    pending: {
      kind: "temperature" | "quantity_confirm" | "clear_confirm" | "target";
      action?: CartAction;
      line_ids?: string[];
    } | null;
    last_recommended_menu_ids: string[];
    prior_conditions: Record<string, unknown> | null;
  };
}

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";

export async function postChat(body: ChatRequest, signal?: AbortSignal): Promise<ChatResponse> {
  const response = await fetch(`${API_BASE}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!response.ok) throw new Error(`API ${response.status}`);
  return (await response.json()) as ChatResponse;
}
