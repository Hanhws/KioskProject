"use client";

import { forwardRef, useEffect, useRef } from "react";
import type { ChatMessage } from "@/lib/kiosk/store";
import type { Choice } from "@/lib/order/types";
import MenuCard from "./MenuCard";

interface Props {
  messages: ChatMessage[];
  busy: boolean;
  draft: string;
  onDraft: (value: string) => void;
  onSend: () => void;
  onChoice: (choice: Choice, messageId: string) => void;
  onOpenMenu: (menuId: string) => void;
  onAddMenu: (menuId: string) => void;
}

const ChatView = forwardRef<HTMLInputElement, Props>(function ChatView(
  { messages, busy, draft, onDraft, onSend, onChoice, onOpenMenu, onAddMenu },
  inputRef,
) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const lastId = messages[messages.length - 1]?.id;

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages.length, busy]);

  return (
    <>
      <div className="chat-scroll" ref={scrollRef}>
        <div className="chat-inner" role="log" aria-live="polite">
          {messages.map((message) => {
            const isLast = message.id === lastId;
            if (message.role === "user") {
              return (
                <div className="msg user" key={message.id}>
                  <div className="msg-body">
                    <div className="bubble">{message.text}</div>
                  </div>
                </div>
              );
            }
            return (
              <div className="msg" key={message.id}>
                <div className="avatar" aria-hidden>
                  K
                </div>
                <div className="msg-body">
                  {message.text && <div className={`bubble${message.tone === "error" ? " error" : ""}`}>{message.text}</div>}
                  {message.menuIds.length > 0 && (
                    <div className="card-row">
                      {message.menuIds.map((menuId) => (
                        <MenuCard key={menuId} menuId={menuId} onOpen={onOpenMenu} onAdd={onAddMenu} disabled={busy} />
                      ))}
                    </div>
                  )}
                  {message.choices.length > 0 && (
                    <div className="chips">
                      {message.choices.map((choice, index) => (
                        <button
                          key={`${choice.label}-${index}`}
                          type="button"
                          className={`chip${choice.variant === "primary" ? " primary" : ""}`}
                          // 지난 질문의 버튼은 누르지 못하게 해 엇갈린 응답을 막는다
                          disabled={busy || !isLast}
                          onClick={() => onChoice(choice, message.id)}
                        >
                          {choice.label}
                        </button>
                      ))}
                    </div>
                  )}
                  {message.evidence && message.evidence.length > 0 && (
                    <div className="evidence">
                      근거
                      {message.evidence.slice(0, 6).map((id) => (
                        <code key={id}>{id}</code>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            );
          })}
          {busy && (
            <div className="msg">
              <div className="avatar" aria-hidden>
                K
              </div>
              <div className="bubble typing" aria-label="답변 작성 중">
                <span />
                <span />
                <span />
              </div>
            </div>
          )}
        </div>
      </div>
      <div className="composer">
        <form
          onSubmit={(event) => {
            event.preventDefault();
            onSend();
          }}
        >
          <input
            ref={inputRef}
            value={draft}
            onChange={(event) => onDraft(event.target.value)}
            placeholder="예: 달콤한 음료 추천해 주세요 / 아아 두 잔 포장이요"
            aria-label="키오스크에 말하기"
            maxLength={300}
            enterKeyHint="send"
          />
          <button type="submit" className="send-btn" disabled={busy || !draft.trim()}>
            보내기
          </button>
        </form>
      </div>
    </>
  );
});

export default ChatView;
