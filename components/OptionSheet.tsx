"use client";

import { useEffect, useMemo, useState } from "react";
import { findVariant, getMenu, optionsForMenu, primaryImage } from "@/lib/order/catalog";
import { won } from "@/lib/order/format";
import { lineTitle, optionsPrice } from "@/lib/order/pricing";
import type { CartAction, CartLine, OptionSelection, Temperature } from "@/lib/order/types";
import { MAX_LINE_QUANTITY, validateItem } from "@/lib/order/validation";
import { MenuImage } from "./MenuCard";

export type SheetTarget = { mode: "add"; menuId: string } | { mode: "edit"; line: CartLine };

export default function OptionSheet({
  target,
  onClose,
  onSubmit,
}: {
  target: SheetTarget;
  onClose: () => void;
  onSubmit: (action: CartAction, echo: string) => void;
}) {
  const menuId = target.mode === "add" ? target.menuId : target.line.menuId;
  const menu = getMenu(menuId)!;
  const available = optionsForMenu(menuId);

  const [temperature, setTemperature] = useState<Temperature | null>(
    target.mode === "edit" ? target.line.temperature : menu.temperatures.length === 1 ? menu.temperatures[0] : null,
  );
  const [options, setOptions] = useState<OptionSelection>(target.mode === "edit" ? { ...target.line.options } : {});
  const [quantity, setQuantity] = useState(target.mode === "edit" ? target.line.quantity : 1);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const validation = useMemo(
    () => validateItem({ menuId, temperature, options, quantity }),
    [menuId, temperature, options, quantity],
  );
  const blocking = validation.ok ? [] : validation.issues.filter((issue) => issue.code !== "temperature_required");
  const needsTemperature = !validation.ok && validation.issues.some((issue) => issue.code === "temperature_required");

  const basePrice = (temperature ? findVariant(menu, temperature)?.price : undefined) ?? Math.min(...menu.variants.map((v) => v.price));
  const unitPrice = basePrice + optionsPrice(options);

  const groups = [...new Set(available.map((option) => option.group))];

  function setCount(optionId: string, count: number) {
    setOptions((prev) => {
      const next = { ...prev };
      if (count <= 0) delete next[optionId];
      else next[optionId] = count;
      return next;
    });
  }

  function submit() {
    if (!validation.ok) return;
    const title = lineTitle({ menuId, temperature, options } as CartLine);
    if (target.mode === "add") {
      onSubmit(
        { type: "add_item", menu_id: menuId, temperature, options, quantity, confirmed: true },
        `${title} ${quantity}${menu.type === "음료" ? "잔" : "개"} 담아 주세요`,
      );
    } else {
      onSubmit(
        {
          type: "update_item",
          target: { line_id: target.line.lineId },
          temperature: temperature ?? undefined,
          set_options: options,
          replace_options: true,
          quantity,
          confirmed: true,
        },
        `${title} ${quantity}${menu.type === "음료" ? "잔" : "개"}로 바꿔 주세요`,
      );
    }
  }

  return (
    <div className="scrim" role="presentation" onClick={onClose}>
      <div
        className="sheet"
        role="dialog"
        aria-modal="true"
        aria-label={`${menu.name} 옵션`}
        onClick={(event) => event.stopPropagation()}
      >
        <div className="sheet-img">
          <MenuImage src={primaryImage(menu)} alt="" />
        </div>
        <div className="sheet-body">
          <div>
            <div className="sheet-title">
              <h3>{menu.name}</h3>
              <span className="num" style={{ fontWeight: 700 }}>{won(basePrice)}</span>
            </div>
            <p className="sheet-desc">{menu.description}</p>
          </div>

          {menu.temperatures.length > 0 && (
            <div>
              <div className="field-label">
                온도 {menu.temperatures.length === 1 && <small>{menu.temperatures[0] === "ICE" ? "아이스만 가능" : "따뜻한 것만 가능"}</small>}
              </div>
              <div className="seg">
                {(["HOT", "ICE"] as const).map((temp) => {
                  const variant = findVariant(menu, temp);
                  return (
                    <button
                      key={temp}
                      type="button"
                      aria-pressed={temperature === temp}
                      disabled={!menu.temperatures.includes(temp)}
                      onClick={() => setTemperature(temp)}
                    >
                      {temp === "HOT" ? "따뜻하게" : "아이스"}
                      {variant ? ` · ${won(variant.price)}` : ""}
                    </button>
                  );
                })}
              </div>
            </div>
          )}

          {groups.map((group) => (
            <div key={group}>
              <div className="field-label">{group}</div>
              {available
                .filter((option) => option.group === group)
                .map((option) => {
                  const count = options[option.id] ?? 0;
                  return (
                    <div className="opt-row" key={option.id}>
                      <div>
                        <div className="opt-name">{option.name}</div>
                        <div className="opt-price">
                          {option.priceDelta ? `+${won(option.priceDelta)}` : "추가금 없음"}
                          {option.maxQuantity > 1 ? ` · 최대 ${option.maxQuantity}` : ""}
                        </div>
                      </div>
                      {option.maxQuantity > 1 ? (
                        <div className="stepper">
                          <button type="button" aria-label={`${option.name} 빼기`} disabled={count === 0} onClick={() => setCount(option.id, count - 1)}>−</button>
                          <output className="num">{count}</output>
                          <button type="button" aria-label={`${option.name} 더하기`} disabled={count >= option.maxQuantity} onClick={() => setCount(option.id, count + 1)}>+</button>
                        </div>
                      ) : (
                        <button type="button" className="toggle" aria-pressed={count > 0} onClick={() => setCount(option.id, count > 0 ? 0 : 1)}>
                          {count > 0 ? "선택됨" : "선택"}
                        </button>
                      )}
                    </div>
                  );
                })}
            </div>
          ))}

          <div>
            <div className="field-label">수량</div>
            <div className="stepper">
              <button type="button" aria-label="수량 빼기" disabled={quantity <= 1} onClick={() => setQuantity((q) => q - 1)}>−</button>
              <output className="num">{quantity}</output>
              <button type="button" aria-label="수량 더하기" disabled={quantity >= MAX_LINE_QUANTITY} onClick={() => setQuantity((q) => q + 1)}>+</button>
            </div>
          </div>

          {blocking.length > 0 && (
            <div className="issues" role="alert">
              {blocking.map((issue) => (
                <span key={`${issue.code}-${issue.optionId ?? ""}`}>{issue.message}</span>
              ))}
            </div>
          )}
        </div>
        <div className="sheet-foot">
          <button type="button" className="sheet-close" onClick={onClose}>
            닫기
          </button>
          <button type="button" className="cta num" onClick={submit} disabled={!validation.ok}>
            {needsTemperature
              ? "온도를 선택해 주세요"
              : `${won(unitPrice * quantity)} ${target.mode === "add" ? "담기" : "변경하기"}`}
          </button>
        </div>
      </div>
    </div>
  );
}
