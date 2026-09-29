import { useState } from "react";
import { addExpertFinding, reviewFinding } from "../api/client";
import type { FindingOut, Severity, ViolationType } from "../api/types";

const SEVERITY_LABELS: Record<Severity, string> = { low: "Низкая", medium: "Средняя", high: "Высокая", critical: "Критическая" };
const STATUS_LABELS: Record<string, string> = {
  pending: "Ожидает проверки", confirmed: "Подтверждено экспертом", rejected: "Отклонено экспертом",
  modified: "Изменено экспертом", added: "Добавлено экспертом",
};

interface Props {
  studyId: number;
  imageId: number | null;
  findings: FindingOut[];
  violationTypes: ViolationType[];
  pendingBbox: { x: number; y: number; w: number; h: number } | null;
  onRequestDraw: () => void;
  onBboxConsumed: () => void;
  onChanged: () => void;
}

export function FindingsPanel({
  studyId, imageId, findings, violationTypes, pendingBbox, onRequestDraw, onBboxConsumed, onChanged,
}: Props) {
  const [newVtId, setNewVtId] = useState<number | "">("");
  const [newSeverity, setNewSeverity] = useState<Severity>("medium");
  const [newComment, setNewComment] = useState("");
  const [busy, setBusy] = useState(false);

  async function act(id: number, action: "confirm" | "reject") {
    setBusy(true);
    try {
      await reviewFinding(id, action);
      onChanged();
    } finally {
      setBusy(false);
    }
  }

  async function modify(id: number, violation_type_id: number) {
    setBusy(true);
    try {
      await reviewFinding(id, "modify", { violation_type_id });
      onChanged();
    } finally {
      setBusy(false);
    }
  }

  async function submitNewFinding(e: React.FormEvent) {
    e.preventDefault();
    if (!imageId || !newVtId || !pendingBbox) return;
    setBusy(true);
    try {
      await addExpertFinding(studyId, {
        image_id: imageId, violation_type_id: Number(newVtId), severity: newSeverity,
        bbox_x: pendingBbox.x, bbox_y: pendingBbox.y, bbox_w: pendingBbox.w, bbox_h: pendingBbox.h,
        comment: newComment,
      });
      setNewVtId(""); setNewComment(""); setNewSeverity("medium");
      onBboxConsumed();
      onChanged();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="findings-panel">
      <h3>Результат AI и экспертная проверка</h3>
      {findings.length === 0 && <p className="muted">Нарушений не найдено (или анализ ещё не запускался).</p>}
      <ul className="findings-list">
        {findings.map((f) => (
          <li key={f.id} className={`finding-item severity-${f.severity}`}>
            <div className="finding-head">
              <strong>{f.violation_name}</strong>
              <span className={`badge source-${f.source}`}>{f.source === "ai" ? "AI" : "Эксперт"}</span>
            </div>
            <div className="finding-meta">
              Критичность: {SEVERITY_LABELS[f.severity]}
              {f.confidence != null && <> · Уверенность AI: {(f.confidence * 100).toFixed(0)}%</>}
              {" · "}{STATUS_LABELS[f.status]}
              {f.reviewed_by && <> ({f.reviewed_by})</>}
            </div>
            {f.comment && <div className="finding-comment">{f.comment}</div>}
            {f.source === "ai" && f.status === "pending" && (
              <div className="finding-actions">
                <button type="button" disabled={busy} onClick={() => act(f.id, "confirm")}>Подтвердить</button>
                <button type="button" disabled={busy} onClick={() => act(f.id, "reject")}>Отклонить</button>
                <select disabled={busy} defaultValue="" onChange={(e) => e.target.value && modify(f.id, Number(e.target.value))}>
                  <option value="" disabled>Изменить категорию…</option>
                  {violationTypes.map((vt) => <option key={vt.id} value={vt.id}>{vt.name_ru}</option>)}
                </select>
              </div>
            )}
          </li>
        ))}
      </ul>

      <div className="add-finding-block">
        <h4>Добавить собственную находку</h4>
        {!pendingBbox ? (
          <button type="button" onClick={onRequestDraw} disabled={!imageId}>Выделить область на снимке</button>
        ) : (
          <form onSubmit={submitNewFinding} className="add-finding-form">
            <div className="muted">Область выделена. Заполните категорию и сохраните.</div>
            <select value={newVtId} onChange={(e) => setNewVtId(e.target.value ? Number(e.target.value) : "")} required>
              <option value="" disabled>Категория нарушения…</option>
              {violationTypes.filter((v) => v.is_active).map((vt) => <option key={vt.id} value={vt.id}>{vt.name_ru}</option>)}
            </select>
            <select value={newSeverity} onChange={(e) => setNewSeverity(e.target.value as Severity)}>
              {Object.entries(SEVERITY_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
            <textarea placeholder="Комментарий" value={newComment} onChange={(e) => setNewComment(e.target.value)} />
            <div className="finding-actions">
              <button type="submit" disabled={busy || !newVtId}>Сохранить находку</button>
              <button type="button" onClick={onBboxConsumed}>Отменить</button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
