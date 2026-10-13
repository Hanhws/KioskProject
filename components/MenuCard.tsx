"use client";

import { useState } from "react";
import { getMenu, lowestPrice, primaryImage } from "@/lib/order/catalog";
import { won } from "@/lib/order/format";

export function MenuImage({ src, alt }: { src: string | null; alt: string }) {
  const [failed, setFailed] = useState(false);
  if (!src || failed) return <span className="thumb-fallback" aria-hidden>☕</span>;
  return (
    // 외부(요기요) 이미지라 next/image 대신 img를 쓴다
    // eslint-disable-next-line @next/next/no-img-element
    <img className="thumb" src={src} alt={alt} loading="lazy" referrerPolicy="no-referrer" onError={() => setFailed(true)} />
  );
}

export default function MenuCard({
  menuId,
  onOpen,
  onAdd,
  disabled,
}: {
  menuId: string;
  onOpen: (menuId: string) => void;
  onAdd: (menuId: string) => void;
  disabled?: boolean;
}) {
  const menu = getMenu(menuId);
  if (!menu) return null;
  const temps = menu.temperatures.length === 2 ? "HOT·ICE" : menu.temperatures[0] ?? menu.category;
  const multiPrice = new Set(menu.variants.map((v) => v.price)).size > 1;
  return (
    <div className="menu-card" role="group" aria-label={menu.name}>
      <button type="button" className="menu-card-open" onClick={() => onOpen(menu.id)} aria-label={`${menu.name} 옵션 선택`}>
        <span className="menu-card-thumb">
          <MenuImage src={primaryImage(menu)} alt="" />
        </span>
        <span className="name">{menu.name}</span>
        <span className="sub">{temps}</span>
      </button>
      <div className="price-row">
        <span className="price num">
          {won(lowestPrice(menu))}
          {multiPrice ? "~" : ""}
        </span>
        <button type="button" className="add-btn" onClick={() => onAdd(menu.id)} disabled={disabled}>
          담기
        </button>
      </div>
    </div>
  );
}
