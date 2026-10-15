/**
 * 키오스크가 닫힌 질문(온도·예/아니요·매장/포장·결제 수단)을 하고 있을 때
 * 짧은 타이핑 답을 서버 호출 없이 바로 처리한다.
 *
 * 열린 질문(메뉴 탐색·추천·정보 질문)은 null을 돌려 서버(RAG/라우터)로 보낸다.
 */

import type { CartAction, EngineAction, OrderState } from "./types";
import { getMenu, getOption } from "./catalog";

// 문장 일부에만 맞는 표현은 추가 요청을 삼킬 수 있으므로 단답 전체에만 맞춘다.
const YES = /^(?:네|예|응|어|ㅇㅇ|맞아|맞아요|맞습니다|좋아|좋아요|그래|그래요|그렇게|넵|넹|yes|ok|오케이)$/i;
const NO = /^(?:아니|아뇨|아니요|아니오|취소|됐어|no)$/i;
const HOT = /^(?:따뜻하게|따뜻한\s*(?:걸로|것으로|거요)|뜨겁게|핫(?:으로|요)?|hot(?:으로|요)?|뜨아)$/i;
const ICE = /^(?:아이스(?:요|로)?|아이스로\s*해\s*주세요|차갑게|시원하게|ice(?:요|로)?|아아)$/i;
const DINE_IN = /^(?:매장(?:이요|에서\s*먹을게요)?|먹고\s*갈게요|여기서\s*먹을게요)$/;
const TAKEOUT = /^(?:포장(?:이요|할게요|해\s*주세요)?|테이크아웃(?:이요)?|가져갈게요)$/;
const CARD = /^(?:카드(?:로\s*할게요|요)?|신용카드|체크카드)$/;
const EASY_PAY = /^(?:간편결제(?:로\s*할게요|요)?|(?:삼성|카카오|네이버|애플|토스)페이(?:로\s*할게요|요)?)$/i;
const CASH = /^현금(?:\s*돼요\?)?$/;
const DONE = /^(?:그게\s*다예요|이제\s*됐어요|다\s*됐어요|더\s*없어요|없어요|없습니다|괜찮아요|주문할게요|결제할게요|계산할게요)$/;

export function interpretStageReply(state: OrderState, rawText: string): EngineAction[] | null {
  const text = rawText.trim().replace(/[.!~〜]+$/, "").trim();
  if (!text || text.length > 20) return null;
  const pending = state.pending;

  if (pending?.kind === "temperature") {
    const hot = HOT.test(text);
    const ice = ICE.test(text.replace(/^ice\s+요$/i, "ice요"));
    if (hot === ice) return null; // 둘 다 있거나 둘 다 없으면 서버가 판단 (HTI 8.2)
    return [{ ...pending.action, temperature: hot ? "HOT" : "ICE", pending_resolution: true }];
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
  if (pending?.kind === "target") {
    const candidates = state.lines.filter((line) => pending.lineIds.includes(line.lineId));
    const targetText = text.replace(/요$/, "").trim();
    const ranked = candidates.map((line) => {
      const menu = getMenu(line.menuId);
      const menuTerms = [menu?.name, ...(menu?.aliases ?? [])].filter((term): term is string => !!term);
      const menuScore = Math.max(0, ...menuTerms.filter((term) => targetText === term).map((term) => term.length));
      const optionScore = Object.keys(line.options).some((id) => {
        const name = getOption(id)?.name ?? "";
        return name.length > 0 && targetText === name;
      }) ? 100 : 0;
      return { line, score: menuScore + optionScore };
    }).filter((row) => row.score > 0).sort((a, b) => b.score - a.score);
    if (ranked.length && (ranked.length === 1 || ranked[0].score > ranked[1].score)) {
      return [{ ...pending.action, target: { line_id: ranked[0].line.lineId } } as CartAction];
    }
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
