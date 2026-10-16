import type { DineOption, PaymentMethod, Temperature } from "./types";

export function won(amount: number): string {
  return `${amount.toLocaleString("ko-KR")}원`;
}

export function temperatureLabel(temperature: Temperature | null): string {
  if (temperature === "HOT") return "따뜻한";
  if (temperature === "ICE") return "아이스";
  return "";
}

export function dineLabel(value: DineOption | null): string {
  if (value === "dine_in") return "매장";
  if (value === "takeout") return "포장";
  return "미선택";
}

export function paymentLabel(value: PaymentMethod | null): string {
  if (value === "card") return "카드";
  if (value === "easy_pay") return "간편결제";
  if (value === "cash") return "현금";
  return "";
}

/** 마지막 글자 받침 유무로 조사를 고른다. 괄호·숫자로 끝나면 앞 글자를 본다. */
function hasBatchim(word: string): boolean {
  const cleaned = word.replace(/[\s)）\]]+$/u, "");
  const last = cleaned.charCodeAt(cleaned.length - 1);
  if (last >= 0xac00 && last <= 0xd7a3) return (last - 0xac00) % 28 !== 0;
  // 숫자·영문: 1,3,6,7,8,0 / l,m,n,r 등은 받침처럼 읽힌다
  return /[013678lmnr]$/i.test(cleaned);
}

export function josa(word: string, pair: "은/는" | "이/가" | "을/를" | "으로/로" | "과/와"): string {
  const [withBatchim, without] = pair.split("/");
  if (pair === "으로/로") {
    const cleaned = word.replace(/[\s)）\]]+$/u, "");
    const code = cleaned.charCodeAt(cleaned.length - 1);
    // 받침이 ㄹ이면 '로'
    const isRieul = code >= 0xac00 && code <= 0xd7a3 && (code - 0xac00) % 28 === 8;
    return `${word}${hasBatchim(word) && !isRieul ? withBatchim : without}`;
  }
  return `${word}${hasBatchim(word) ? withBatchim : without}`;
}
