/**
 * 화면 상태: 대화 기록 + 주문 상태. 서버 응답과 버튼 액션을 한곳에서 합친다.
 * React와 분리된 순수 함수라 테스트할 수 있다.
 */

import type { ChatRequest, ChatResponse, RagChoice } from "../api";
import { getMenu } from "../order/catalog";
import { cartSnapshot, greetingReply, initialOrderState, runActions, stagePrompt } from "../order/engine";
import type { Choice, EngineAction, KioskReply, OrderState } from "../order/types";

export interface ChatMessage {
  id: string;
  role: "user" | "kiosk";
  text: string;
  choices: Choice[];
  menuIds: string[];
  tone?: "error";
  evidence?: string[];
}

export interface KioskState {
  order: OrderState;
  messages: ChatMessage[];
  lastRecommended: string[];
  priorConditions: Record<string, unknown> | null;
  pendingMenuConfirmation: string | null;
  busy: boolean;
  seq: number;
}

export type KioskEvent =
  | { type: "user_text"; text: string }
  | { type: "engine"; actions: EngineAction[]; echo?: string; silent?: boolean }
  | { type: "server"; response: ChatResponse }
  | { type: "server_error" }
  | { type: "busy"; value: boolean }
  | { type: "reset" };

const MAX_CARDS = 4;
const VISIBLE_STAGES = new Set(["dine_option", "final_confirm", "payment", "pay_confirm", "complete"]);

export function initialKioskState(): KioskState {
  const greeting = greetingReply();
  return {
    order: initialOrderState(),
    messages: [{ id: "m0", role: "kiosk", ...greeting }],
    lastRecommended: [],
    priorConditions: null,
    pendingMenuConfirmation: null,
    busy: false,
    seq: 1,
  };
}

function push(state: KioskState, message: Omit<ChatMessage, "id">): KioskState {
  return {
    ...state,
    seq: state.seq + 1,
    messages: [...state.messages, { ...message, id: `m${state.seq}` }],
  };
}

function pushReply(state: KioskState, reply: KioskReply): KioskState {
  if (!reply.text && reply.choices.length === 0 && reply.menuIds.length === 0) return state;
  return push(state, { role: "kiosk", text: reply.text, choices: reply.choices, menuIds: reply.menuIds });
}

/** RAG choices → 화면 버튼. 메뉴 선택지는 바로 담기(온도는 엔진이 물음, HTI 3.6) */
export function mapRagChoices(choices: RagChoice[], order: OrderState): Choice[] {
  return choices.flatMap((choice): Choice[] => {
    const menuId = choice.field === "menu_group_id" ? choice.value : choice.source_id;
    if (choice.value === "free_text" || choice.field === "free_text") {
      return [{ label: choice.label, action: { type: "focus_input" } }];
    }
    if (menuId && getMenu(menuId) && menuId.startsWith("MENU-")) {
      return [{ label: choice.label, action: { type: "add_item", menu_id: menuId } }];
    }
    if (choice.field === "temperature" && (choice.value === "HOT" || choice.value === "ICE")) {
      if (order.pending?.kind === "temperature") {
        return [{ label: choice.label, action: { ...order.pending.action, temperature: choice.value, pending_resolution: true } }];
      }
    }
    return [{ label: choice.label, action: { type: "send", text: choice.label } }];
  });
}

function applyEngine(state: KioskState, actions: EngineAction[], silent = false, echo?: string): KioskState {
  const prev = state.order;
  const { state: order, reply } = runActions(prev, actions);
  // silent: 장바구니 패널의 잦은 조작은 질문이 생기거나 단계가 바뀔 때만 대화창에 남긴다
  const show = !silent || !!order.pending || VISIBLE_STAGES.has(order.stage) || order.stage !== prev.stage;
  if (!show) return { ...state, order, pendingMenuConfirmation: null };
  const withEcho = echo ? push(state, { role: "user", text: echo, choices: [], menuIds: [] }) : state;
  return pushReply({ ...withEcho, order, pendingMenuConfirmation: null }, reply);
}

function handleServer(state: KioskState, response: ChatResponse): KioskState {
  const menuIds = (response.related_menu_ids ?? []).filter((id) => getMenu(id)).slice(0, MAX_CARDS);
  const isRecommendation = response.next_action === "choose_menu";
  const confirmationId = response.next_action === "confirm_menu"
    ? response.choices.find((choice) => choice.field === "menu_group_id" && getMenu(choice.value))?.value ?? null
    : null;
  let next: KioskState = {
    ...state,
    busy: false,
    lastRecommended: isRecommendation && menuIds.length ? menuIds : state.lastRecommended,
    priorConditions: response.prior_conditions !== undefined ? response.prior_conditions : state.priorConditions,
    pendingMenuConfirmation: confirmationId,
  };
  const failed = !!response.error;
  const hasActions = !!response.cart_actions?.length;
  const midOrder = !!state.order.pending || (state.order.stage !== "greeting" && state.order.stage !== "complete");
  const resumes = !hasActions && midOrder && (
    response.status === "out_of_scope" || response.next_action === "resume_pending_question" || failed
  );
  const prompt = resumes ? stagePrompt(state.order) : null;
  const resume = prompt ? response.resume_prompt || (state.order.pending ? `아까 ${prompt.text}` : prompt.text) : "";
  const answer = [response.answer, resume && !response.answer.includes(resume) ? resume : ""].filter(Boolean).join("\n");
  if (answer) {
    next = push(next, {
      role: "kiosk",
      text: answer,
      choices: [...mapRagChoices(response.choices ?? [], state.order), ...(prompt?.choices ?? [])],
      menuIds,
      tone: failed ? "error" : undefined,
      evidence: response.evidence_document_ids,
    });
  }

  if (response.cart_actions?.length) {
    // HTI 공통 규칙 1: 보류 질문 중 장바구니를 확인해도 진행 중인 주문은 유지한다.
    if (state.order.pending && response.cart_actions.every((action) => action.type === "show_cart")) {
      const { reply } = runActions({ ...state.order, pending: null, queued: [] }, response.cart_actions);
      return pushReply(pushReply(next, reply), stagePrompt(state.order));
    }
    return applyEngine(next, response.cart_actions);
  }

  return next;
}

export function kioskReducer(state: KioskState, event: KioskEvent): KioskState {
  switch (event.type) {
    case "user_text":
      return push({ ...state, pendingMenuConfirmation: null }, { role: "user", text: event.text, choices: [], menuIds: [] });
    case "engine":
      return applyEngine(state, event.actions, event.silent, event.echo);
    case "server":
      return handleServer(state, event.response);
    case "server_error": {
      const next = push(
        { ...state, busy: false },
        {
          role: "kiosk",
          text: "지금은 대화 서버에 연결할 수 없어요. 메뉴판에서 직접 골라 담으실 수 있어요.",
          choices: [],
          menuIds: [],
          tone: "error",
        },
      );
      const prompt = stagePrompt(state.order);
      return state.order.pending ? pushReply(next, prompt) : next;
    }
    case "busy":
      return { ...state, busy: event.value };
    case "reset":
      return initialKioskState();
  }
}

/** HTI 8.5: 오타 후보를 확인한 뒤 '네'라고 답해도 같은 메뉴를 담는다. */
export function confirmSuggestedMenu(state: KioskState, text: string): EngineAction[] | null {
  const menuId = state.pendingMenuConfirmation;
  if (!menuId || !getMenu(menuId)) return null;
  return /^(?:네|예|맞아요|맞습니다|그거요|그걸로|응|네,\s*맞아요)(?:[.!])?$/.test(text.trim())
    ? [{ type: "add_item", menu_id: menuId }]
    : null;
}

export function buildChatRequest(state: KioskState, message: string): ChatRequest {
  const pending = state.order.pending;
  const history = state.messages
    .filter((m) => m.text)
    .slice(-10)
    .map((m) => ({ role: m.role === "user" ? ("user" as const) : ("assistant" as const), content: m.text }));
  return {
    message,
    history,
    context: {
      cart: { ...cartSnapshot(state.order), pending_question: stagePromptText(state.order) },
      pending: pending ? {
        kind: pending.kind,
        ...("action" in pending ? { action: pending.action } : {}),
        ...(pending.kind === "target" ? { line_ids: pending.lineIds } : {}),
      } : null,
      last_recommended_menu_ids: state.lastRecommended,
      prior_conditions: state.priorConditions,
    },
  };
}

/** 서버가 딴 질문 뒤 되물을 수 있게 현재 멈춘 질문을 함께 보낸다 */
function stagePromptText(order: OrderState): string | null {
  if (order.stage === "greeting" && !order.pending) return null;
  if (order.stage === "complete") return null;
  return stagePrompt(order).text.split("\n").pop() ?? null;
}
