/**
 * 키오스크가 닫힌 질문(온도·예/아니요·매장/포장·결제 수단)을 하고 있을 때
 * 짧은 타이핑 답을 서버 호출 없이 바로 처리한다.
 *
 * 열린 질문(메뉴 탐색·추천·정보 질문)은 null을 돌려 서버(RAG/라우터)로 보낸다.
 */

import type { CartAction, EngineAction, OrderState } from "./types";

const YES = /^(네|예|응|어|ㅇㅇ|맞아|맞아요|맞습니다|좋아|좋아요|그래|그래요|그렇게|넵|넹|yes|ok|오케이)/i;
const NO = /^(아니|아뇨|아니요|아니오|취소|됐어|no)/i;
const HOT = /(따뜻|따듯|뜨거|뜨겁|핫|hot|뜨아)/i;
const ICE = /(아이스|차가|차갑|시원|ice|아아|얼음)/i;
const DINE_IN = /(매장|먹고|여기서|홀)/;
const TAKEOUT = /(포장|테이크|가져|들고)/;
const CARD = /(카드|신용|체크)/;
const EASY_PAY = /(간편|페이|pay|삼성|카카오|네이버|애플|토스)/i;
const CASH = /(현금)/;
const DONE = /^(그게\s*다|이제\s*됐|다\s*됐|없어요|없습니다|없어|괜찮아요|주문할게|주문 할게|결제할게|결제 할게|계산할게|계산해)/;

export function interpretStageReply(state: OrderState, rawText: string): EngineAction[] | null {
  const text = rawText.trim();
  if (!text || text.length > 20) return null;
  const pending = state.pending;

  if (pending?.kind === "temperature") {
    const hot = HOT.test(text);
    const ice = ICE.test(text);
    if (hot === ice) return null; // 둘 다 있거나 둘 다 없으면 서버가 판단 (HTI 8.2)
    return [{ ...pending.action, temperature: hot ? "HOT" : "ICE" }];
  }
  if (pending?.kind === "quantity_confirm") {
    if (YES.test(text)) return [{ ...pending.action, confirmed: true } as CartAction];
    if (NO.test(text)) return [{ type: "cancel_pending" }];
    return null;
  }
  if (pending?.kind === "clear_confirm") {
    if (YES.test(text)) return [{ type: "clear_cart", confirmed: true }];
    if (NO.test(text)) return [{ type: "cancel_pending" }];
    return null;
  }
  if (pending) return null;

  switch (state.stage) {
    case "dine_option":
      if (DINE_IN.test(text) && !TAKEOUT.test(text)) return [{ type: "set_dine_option", value: "dine_in" }];
      if (TAKEOUT.test(text) && !DINE_IN.test(text)) return [{ type: "set_dine_option", value: "takeout" }];
      return null;
    case "final_confirm":
      if (YES.test(text)) return [{ type: "confirm_order" }];
      return null;
    case "payment":
    case "pay_confirm":
      if (CASH.test(text)) return [{ type: "select_payment", method: "cash" }];
      if (CARD.test(text)) return [{ type: "select_payment", method: "card" }];
      if (EASY_PAY.test(text)) return [{ type: "select_payment", method: "easy_pay" }];
      if (state.stage === "pay_confirm" && YES.test(text)) return [{ type: "pay_now" }];
      return null;
    case "more":
      if (DONE.test(text)) return [{ type: "finish_ordering" }];
      return null;
    default:
      return null;
  }
}
