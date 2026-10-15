"use client";

import { getMenu } from "@/lib/order/catalog";
import { won } from "@/lib/order/format";
import { lineTitle, priceCart, unitWord } from "@/lib/order/pricing";
import type { CartAction, CartLine, OrderState } from "@/lib/order/types";

export default function CartPanel({
  order,
  open,
  onClose,
  onAction,
  onEdit,
  disabled,
}: {
  order: OrderState;
  open: boolean;
  onClose: () => void;
  /** silent: 수량 +/- 처럼 잦은 조작은 대화창에 매번 남기지 않는다 */
  onAction: (action: CartAction, options?: { echo?: string; silent?: boolean }) => void;
  onEdit: (line: CartLine) => void;
  disabled: boolean;
}) {
  const cart = priceCart(order.lines);
  const locked = order.stage === "complete";

  return (
    <aside className={`cart${open ? " open" : ""}`} aria-label="주문 내역">
      <div className="cart-head">
        <h2>
          주문 내역 <span className="num" style={{ color: "var(--ink-3)", fontWeight: 600 }}>{cart.itemCount}</span>
        </h2>
        <div style={{ display: "flex", gap: 14 }}>
          {order.lines.length > 0 && !locked && (
            <button type="button" className="link-btn" onClick={() => onAction({ type: "clear_cart" }, { echo: "다 취소할게요" })}>
              전체 취소
            </button>
          )}
          <button type="button" className="link-btn mobile-only" onClick={onClose}>
            닫기
          </button>
        </div>
      </div>

      <div className="cart-list">
        {cart.lines.length === 0 && (
          <p className="cart-empty">
            아직 담긴 메뉴가 없어요.
            <br />
            대화로 주문하거나 메뉴판에서 골라 주세요.
          </p>
        )}
        {cart.lines.map((line) => {
          const menu = getMenu(line.menuId);
          const unit = unitWord(line.menuId);
          return (
            <div className="line" key={line.lineId}>
              <div className="line-top">
                <div>
                  <div className="line-name">
                    {line.temperature && <span className={`temp-badge ${line.temperature}`}>{line.temperature}</span>}
                    {menu?.name}
                  </div>
                  {line.optionLabels.length > 0 && <div className="line-opts">{line.optionLabels.join(" · ")}</div>}
                  <div className="line-unit num">
                    {won(line.basePrice)}
                    {line.optionsPrice ? ` + 옵션 ${won(line.optionsPrice)}` : ""} / {unit}
                  </div>
                </div>
                <strong className="num">{won(line.lineTotal)}</strong>
              </div>
              <div className="line-bottom">
                <div className="stepper" aria-label="수량">
                  <button
                    type="button"
                    aria-label="수량 빼기"
                    disabled={disabled || locked || line.quantity <= 1}
                    onClick={() =>
                      onAction({ type: "update_item", target: { line_id: line.lineId }, quantity: line.quantity - 1 }, { silent: true })
                    }
                  >
                    −
                  </button>
                  <output className="num">{line.quantity}</output>
                  <button
                    type="button"
                    aria-label="수량 더하기"
                    disabled={disabled || locked}
                    onClick={() =>
                      onAction(
                        { type: "update_item", target: { line_id: line.lineId }, quantity: line.quantity + 1, confirmed: true },
                        { silent: true },
                      )
                    }
                  >
                    +
                  </button>
                </div>
                <div className="line-actions">
                  <button type="button" className="small-btn" disabled={disabled || locked} onClick={() => onEdit(line)}>
                    변경
                  </button>
                  <button
                    type="button"
                    className="small-btn danger"
                    disabled={disabled || locked}
                    onClick={() =>
                      onAction({ type: "remove_item", target: { line_id: line.lineId } }, { echo: `${lineTitle(line)} 빼 주세요` })
                    }
                  >
                    삭제
                  </button>
                </div>
              </div>
            </div>
          );
        })}
      </div>

      <div className="cart-foot">
        <div className="seg" role="group" aria-label="매장 또는 포장">
          <button
            type="button"
            aria-pressed={order.dineOption === "dine_in"}
            disabled={disabled || locked}
            onClick={() => onAction({ type: "set_dine_option", value: "dine_in" }, { echo: "매장에서 먹을게요", silent: true })}
          >
            매장
          </button>
          <button
            type="button"
            aria-pressed={order.dineOption === "takeout"}
            disabled={disabled || locked}
            onClick={() => onAction({ type: "set_dine_option", value: "takeout" }, { echo: "포장할게요", silent: true })}
          >
            포장
          </button>
        </div>
        <div className="total-row">
          <span className="label">총 결제금액</span>
          <span className="value num">{won(cart.total)}</span>
        </div>
        <button
          type="button"
          className="cta"
          disabled={disabled || locked || cart.lines.length === 0}
          onClick={() => {
            onAction({ type: "finish_ordering" }, { echo: "주문할게요" });
            onClose();
          }}
        >
          주문하기
        </button>
      </div>
    </aside>
  );
}
