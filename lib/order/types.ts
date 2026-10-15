export type Temperature = "HOT" | "ICE";
export type DineOption = "dine_in" | "takeout";
export type PaymentMethod = "card" | "easy_pay";

/** option_id → 횟수. 예: { "OPT-001": 2 } = 샷 2개 추가 */
export type OptionSelection = Record<string, number>;

export interface MenuVariant {
  id: string;
  displayName: string;
  temperature: Temperature | null;
  price: number;
  volumeMl: number | null;
  caffeineMg: number | null;
  allergens: string | null;
  decafAvailable: string | null;
  imageUrl: string | null;
}

export interface Menu {
  id: string;
  type: "음료" | "디저트" | string;
  category: string;
  name: string;
  aliases: string[];
  temperatures: Temperature[];
  description: string;
  flavorTags: string[];
  sweetness: string | null;
  coffeeStrength: string | null;
  variants: MenuVariant[];
}

export interface MenuOption {
  id: string;
  group: string;
  name: string;
  appliesTo: string;
  excludedMenus: string | null;
  maxQuantity: number;
  priceDelta: number;
  rule: string;
  menuIds: string[];
}

export interface Pairing {
  id: string;
  beverageMenuIds: string[];
  dessertMenuIds: string[];
  reason: string;
}

export interface CartLine {
  lineId: string;
  menuId: string;
  variantId: string;
  temperature: Temperature | null;
  options: OptionSelection;
  quantity: number;
}

export interface PricedLine extends CartLine {
  menuName: string;
  basePrice: number;
  optionsPrice: number;
  unitPrice: number;
  lineTotal: number;
  optionLabels: string[];
}

/** 메뉴를 담거나 바꿀 때의 입력. 온도·옵션·수량은 빠질 수 있다. */
export interface ItemDraft {
  menuId: string;
  temperature?: Temperature | null;
  options?: OptionSelection;
  quantity?: number;
}

/** 대상 항목 지정. line_id가 가장 정확하고, 없으면 메뉴·온도로 찾는다. */
export interface LineTarget {
  line_id?: string;
  menu_id?: string;
  menu_ids?: string[];
  temperature?: Temperature;
  /** 온도를 기다리는 아직 담기 전 주문 초안 */
  pending?: boolean;
}

/**
 * 장바구니·주문 흐름 액션. 버튼과 서버(통합 라우터)가 같은 형식을 사용한다.
 * 서버 응답의 cart_actions 필드도 이 형식이다.
 */
export type CartAction =
  | {
      type: "add_item";
      menu_id: string;
      temperature?: Temperature | null;
      options?: OptionSelection;
      quantity?: number;
      /** 현재 보류 중인 온도 질문에 대한 답일 때만 true */
      pending_resolution?: boolean;
      /** 10잔 이상 수량 확인을 이미 받았으면 true */
      confirmed?: boolean;
    }
  | {
      type: "update_item";
      target: LineTarget;
      quantity?: number;
      temperature?: Temperature;
      /** 지정한 옵션만 절대값으로 바꾼다. 0이면 제거 */
      set_options?: OptionSelection;
      /** true면 set_options로 옵션 전체를 교체한다 */
      replace_options?: boolean;
      confirmed?: boolean;
    }
  | { type: "remove_item"; target: LineTarget }
  | { type: "replace_menu"; target: LineTarget; menu_id: string }
  | { type: "clear_cart"; confirmed?: boolean }
  | { type: "set_dine_option"; value: DineOption }
  | { type: "finish_ordering" }
  | { type: "show_cart" }
  | { type: "confirm_order" }
  | { type: "select_payment"; method: PaymentMethod | "cash" }
  | { type: "pay_now" }
  | { type: "new_order" };

/** 엔진이 처리하는 액션 = 서버·버튼 공용 CartAction + 보류 질문 취소 */
export type EngineAction = CartAction | { type: "cancel_pending" };

export type Stage =
  | "greeting"
  | "ordering"
  | "more"
  | "dine_option"
  | "final_confirm"
  | "payment"
  | "pay_confirm"
  | "complete";

export type Pending =
  | { kind: "temperature"; action: Extract<CartAction, { type: "add_item" }>; replaceLineId?: string; question: string }
  | { kind: "quantity_confirm"; action: CartAction; question: string }
  | { kind: "clear_confirm"; question: string }
  | { kind: "target"; action: CartAction; lineIds: string[]; question: string }
  | null;

export interface OrderState {
  lines: CartLine[];
  dineOption: DineOption | null;
  stage: Stage;
  pending: Pending;
  /** 보류 질문 때문에 멈춘 나머지 액션. 질문이 풀리면 이어서 처리한다 */
  queued: CartAction[];
  paymentMethod: PaymentMethod | null;
  orderNumber: string | null;
  /** 디저트 제안은 주문 한 번에 한 번만 (공통 규칙 4) */
  dessertSuggested: boolean;
  /** '샷·시럽 추가도 가능해요'는 커피를 처음 담을 때만 */
  coffeeHintShown: boolean;
  nextLineSeq: number;
}

export type IssueCode =
  | "unknown_menu"
  | "temperature_required"
  | "temperature_unavailable"
  | "unknown_option"
  | "option_not_applicable"
  | "option_quantity_exceeded"
  | "option_conflict"
  | "invalid_quantity"
  | "quantity_exceeds_limit";

export interface ValidationIssue {
  code: IssueCode;
  message: string;
  optionId?: string;
  /** 대안: 가능한 온도, 옵션 최대치로 고친 초안, 옵션이 가능한 다른 메뉴 등 */
  availableTemperatures?: Temperature[];
  fixedDraft?: ItemDraft;
  alternativeMenuIds?: string[];
}

/** 화면에 보여 줄 선택 버튼. action을 그대로 실행한다. */
export type ClientAction =
  | CartAction
  | { type: "send"; text: string }
  | { type: "open_menu"; menu_id: string }
  | { type: "edit_line"; line_id: string }
  | { type: "focus_input" }
  | { type: "cancel_pending" };

export interface Choice {
  label: string;
  action: ClientAction;
  variant?: "primary" | "default";
}

export interface KioskReply {
  text: string;
  choices: Choice[];
  /** 함께 보여 줄 메뉴 카드 (추천·디저트 제안·대안) */
  menuIds: string[];
  /** 최종 확인·결제 화면에서 주문 요약을 카드로 보여줄지 */
  showSummary?: boolean;
}
