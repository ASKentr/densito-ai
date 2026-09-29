import { useEffect, useRef, useState } from "react";
import { AI_STATE_TEXT, recheckAiStatus, useAiStatus } from "../api/aiStatus";
import { Icon } from "./ui";

/** Индикатор в шапке: готов ли ИИ-модуль; по клику — состав и версия. */
export function AiStatusBadge() {
  const s = useAiStatus();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  async function recheck() {
    setBusy(true);
    try { await recheckAiStatus(); } finally { setBusy(false); }
  }

  return (
    <div className="ai-status" ref={ref}>
      <button type="button" className={`ai-pill ai-${s.state}`} onClick={() => setOpen((o) => !o)}
              aria-expanded={open} title="Состояние ИИ-модуля">
        <i className="ai-dot" />
        <span>{AI_STATE_TEXT[s.state]}</span>
      </button>
      {open && (
        <div className="ai-pop" role="dialog" aria-label="Состояние ИИ-модуля">
          <div className="ai-pop-head">
            <b>{AI_STATE_TEXT[s.state]}</b>
            {s.modelVersion && <span className="mono small muted">{s.modelVersion}</span>}
          </div>
          {s.state === "offline" ? (
            <p className="small muted">Backend не отвечает. Проверьте, что сервис запущен (<code>./run.sh status</code>).</p>
          ) : (
            <ul className="ai-list">
              {s.components.map((c) => (
                <li key={c.name}>
                  <span className={c.ok ? "ai-ok" : "ai-bad"}><Icon name={c.ok ? "check" : "x"} size={14} /></span>
                  <div>
                    <div className="ai-name">{c.name}</div>
                    <div className="small muted">{c.detail}</div>
                  </div>
                </li>
              ))}
            </ul>
          )}
          <div className="ai-pop-foot">
            <span className="small muted">
              {s.checkedAt ? `Проверено в ${s.checkedAt.toLocaleTimeString("ru-RU")}` : "Проверка…"} · обновляется каждые 30 с
            </span>
            <button type="button" className="btn-sm" onClick={recheck} disabled={busy}>
              <Icon name="refresh" size={14} /> {busy ? "…" : "Проверить"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
