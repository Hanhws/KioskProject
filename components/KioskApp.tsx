"use client";

import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { postChat } from "@/lib/api";
import { buildChatRequest, confirmSuggestedMenu, initialKioskState, kioskReducer } from "@/lib/kiosk/store";
import { getMenu } from "@/lib/order/catalog";
import { won } from "@/lib/order/format";
import { priceCart } from "@/lib/order/pricing";
import { interpretStageReply } from "@/lib/order/stageReply";
import type { CartAction, CartLine, Choice, EngineAction } from "@/lib/order/types";
import CartPanel from "./CartPanel";
import ChatView from "./ChatView";
import MenuBoard from "./MenuBoard";
import OptionSheet, { type SheetTarget } from "./OptionSheet";

type View = "chat" | "menu";

export default function KioskApp() {
  const [state, dispatch] = useReducer(kioskReducer, undefined, initialKioskState);
  const [view, setView] = useState<View>("chat");
  const [draft, setDraft] = useState("");
  const [sheet, setSheet] = useState<SheetTarget | null>(null);
  const [cartOpen, setCartOpen] = useState(false);
  // 큰 글씨 모드: 글자·버튼·터치 영역을 키우고 흐린 글자의 대비를 높인다 (디지털 소외계층 배려)
  const [largeText, setLargeText] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const stateRef = useRef(state);
  const abortRef = useRef<AbortController | null>(null);
  stateRef.current = state;

  useEffect(() => () => abortRef.current?.abort(), []);

  const runEngine = useCallback((actions: EngineAction[], echo?: string, silent?: boolean) => {
    dispatch({ type: "engine", actions, echo, silent });
  }, []);

  const send = useCallback(async (raw: string) => {
    const text = raw.trim();
    const current = stateRef.current;
    if (!text || current.busy) return;
    setView("chat");

    const confirmedMenu = confirmSuggestedMenu(current, text);
    if (confirmedMenu) {
      runEngine(confirmedMenu, text);
      return;
    }

    // 닫힌 질문(온도·예/아니요·매장/포장·결제 수단)은 바로 처리
    const local = interpretStageReply(current.order, text);
    if (local) {
      runEngine(local, text);
      return;
    }

    dispatch({ type: "user_text", text });
    dispatch({ type: "busy", value: true });
    const request = buildChatRequest(current, text);
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const response = await postChat(request, controller.signal);
      dispatch({ type: "server", response });
      if (response.next_action === "open_menu") setView("menu");
      if (response.next_action === "open_chat") setView("chat");
    } catch (error) {
      if ((error as Error).name === "AbortError") return;
      dispatch({ type: "server_error" });
    }
  }, [runEngine]);

  const addMenu = useCallback(
    (menuId: string) => {
      const menu = getMenu(menuId);
      setView("chat");
      runEngine([{ type: "add_item", menu_id: menuId }], menu ? `${menu.name} 주세요` : undefined);
    },
    [runEngine],
  );

  const onChoice = useCallback(
    (choice: Choice) => {
      const action = choice.action;
      switch (action.type) {
        case "send":
          void send(action.text);
          return;
        case "open_menu":
          setSheet({ mode: "add", menuId: action.menu_id });
          return;
        case "edit_line": {
          const line = stateRef.current.order.lines.find((row) => row.lineId === action.line_id);
          if (line) setSheet({ mode: "edit", line });
          return;
        }
        case "focus_input":
          setView("chat");
          inputRef.current?.focus();
          return;
        default:
          runEngine([action], choice.label);
      }
    },
    [runEngine, send],
  );

  const onCartAction = useCallback(
    (action: CartAction, options?: { echo?: string; silent?: boolean }) => {
      runEngine([action], options?.echo, options?.silent);
      if (!options?.silent) setView("chat");
    },
    [runEngine],
  );

  const order = state.order;
  const cart = priceCart(order.lines);

  return (
    <div className={`app${largeText ? " large" : ""}`}>
      <header className="header">
        <div className="brand">
          <span className="brand-mark" aria-hidden>
            K
          </span>
          <span>카페 대화 키오스크</span>
        </div>
        <div className="tabs" role="tablist" aria-label="주문 방식">
          <button type="button" role="tab" className="tab" aria-selected={view === "chat"} onClick={() => setView("chat")}>
            대화 주문
          </button>
          <button type="button" role="tab" className="tab" aria-selected={view === "menu"} onClick={() => setView("menu")}>
            메뉴판
          </button>
        </div>
        <div className="header-spacer" />
        <button
          type="button"
          className="large-toggle"
          aria-pressed={largeText}
          aria-label="큰 글씨로 보기"
          onClick={() => setLargeText((value) => !value)}
        >
          <span className="large-toggle-mark" aria-hidden>
            가
          </span>
          <span className="large-toggle-label">{largeText ? "기본 글씨" : "큰 글씨"}</span>
        </button>
        <button
          type="button"
          className="ghost-btn"
          onClick={() => {
            abortRef.current?.abort();
            dispatch({ type: "reset" });
            setView("chat");
            setDraft("");
          }}
        >
          처음으로
        </button>
      </header>

      <div className="body">
        <main className="main">
          {view === "chat" ? (
            <ChatView
              ref={inputRef}
              messages={state.messages}
              busy={state.busy}
              draft={draft}
              onDraft={setDraft}
              onSend={() => {
                const text = draft;
                setDraft("");
                void send(text);
              }}
              onChoice={onChoice}
              onOpenMenu={(menuId) => setSheet({ mode: "add", menuId })}
              onAddMenu={addMenu}
            />
          ) : (
            <MenuBoard onOpen={(menuId) => setSheet({ mode: "add", menuId })} onAdd={addMenu} />
          )}
          <div className="cart-bar">
            <span className="count num">담은 메뉴 {cart.itemCount}</span>
            <span className="sum num">{won(cart.total)}</span>
            <button type="button" onClick={() => setCartOpen(true)}>
              주문 내역
            </button>
          </div>
        </main>

        <CartPanel
          order={order}
          open={cartOpen}
          onClose={() => setCartOpen(false)}
          onAction={onCartAction}
          onEdit={(line: CartLine) => setSheet({ mode: "edit", line })}
          disabled={state.busy}
        />
      </div>

      {sheet && (
        <OptionSheet
          key={sheet.mode === "add" ? sheet.menuId : sheet.line.lineId}
          target={sheet}
          onClose={() => setSheet(null)}
          onSubmit={(action, echo) => {
            setSheet(null);
            setView("chat");
            runEngine([action], echo);
          }}
        />
      )}

      {order.stage === "complete" && order.orderNumber && (
        <div className="scrim" role="dialog" aria-modal="true" aria-label="주문 완료">
          <div className="done-card">
            <p>{order.paymentMethod === "cash" ? "주문이 접수됐어요" : "주문이 완료됐어요"}</p>
            <div className="no num">{order.orderNumber}</div>
            {order.paymentMethod === "cash" ? (
              <p className="done-cash">
                카운터에서 주문번호를 말씀하시고
                <br />
                현금 <span className="num">{won(cart.total)}</span>을 결제해 주세요.
              </p>
            ) : (
              <p>메뉴가 준비되면 번호로 불러 드릴게요.</p>
            )}
            <p className="done-note">시연용 모의 결제로 실제 결제는 이뤄지지 않았어요.</p>
            <button
              type="button"
              className="cta"
              style={{ width: "100%", marginTop: 16 }}
              onClick={() => {
                dispatch({ type: "reset" });
                setView("chat");
              }}
            >
              새 주문 시작
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
