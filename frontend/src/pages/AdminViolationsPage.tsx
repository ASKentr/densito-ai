import { useEffect, useState } from "react";
import { createViolationType, listViolationTypes, updateViolationType } from "../api/client";
import type { Severity, ViolationType } from "../api/types";
import { CATEGORY_LABELS } from "../components/ui";

const SEVERITY_LABELS: Record<Severity, string> = { low: "Низкая", medium: "Средняя", high: "Высокая", critical: "Критическая" };
const SEVERITY_CLASS: Record<Severity, string> = {
  low: "review-in_review", medium: "review-in_review", high: "verdict-non_qualitative", critical: "verdict-non_qualitative",
};

export function AdminViolationsPage() {
  const [items, setItems] = useState<ViolationType[]>([]);
  const [form, setForm] = useState({ code: "", name_ru: "", category: "", description: "", default_severity: "medium" as Severity });
  const [error, setError] = useState<string | null>(null);

  function reload() { listViolationTypes().then(setItems); }
  useEffect(reload, []);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await createViolationType(form);
      setForm({ code: "", name_ru: "", category: "", description: "", default_severity: "medium" });
      reload();
    } catch (err: any) {
      setError(err?.response?.data?.detail || "Не удалось создать тип нарушения");
    }
  }

  async function toggleActive(vt: ViolationType) {
    await updateViolationType(vt.id, { is_active: !vt.is_active });
    reload();
  }

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>Справочник нарушений</h1>
          <p>ИИ-модуль ссылается на записи по устойчивому полю <code>code</code>, поэтому переименование не ломает анализ.
            Системные типы нельзя удалить — только отключить.</p>
        </div>
      </div>

      <div className="stack">
        <div className="card table-card">
          <div className="table-wrap">
            <table className="studies-table">
              <thead><tr><th>Нарушение</th><th>Категория</th><th>Критичность</th><th>Тип</th><th>Статус</th><th></th></tr></thead>
              <tbody>
                {items.map((vt) => (
                  <tr key={vt.id} style={vt.is_active ? undefined : { opacity: 0.6 }}>
                    <td style={{ maxWidth: 460 }}>
                      <div className="cell-title">{vt.name_ru}</div>
                      <div className="cell-sub"><span className="mono">{vt.code}</span>{vt.description && <> · {vt.description}</>}</div>
                    </td>
                    <td>{CATEGORY_LABELS[vt.category] ?? vt.category}</td>
                    <td><span className={`badge plain ${SEVERITY_CLASS[vt.default_severity]}`}>{SEVERITY_LABELS[vt.default_severity]}</span></td>
                    <td className="small muted">{vt.is_system ? "Системный" : "Пользовательский"}</td>
                    <td><span className={`badge ${vt.is_active ? "verdict-qualitative" : "status-uploaded"}`}>{vt.is_active ? "Активен" : "Отключён"}</span></td>
                    <td style={{ textAlign: "right" }}>
                      <button type="button" className="btn-sm" onClick={() => toggleActive(vt)}>{vt.is_active ? "Отключить" : "Включить"}</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        <div className="card card-pad">
          <div className="section-title">Добавить тип нарушения</div>
          <form onSubmit={handleCreate} className="admin-form">
            <input placeholder="code (латиницей, уникальный)" value={form.code} onChange={(e) => setForm((f) => ({ ...f, code: e.target.value }))} required />
            <input placeholder="Название" value={form.name_ru} onChange={(e) => setForm((f) => ({ ...f, name_ru: e.target.value }))} required />
            <input placeholder="Категория" value={form.category} onChange={(e) => setForm((f) => ({ ...f, category: e.target.value }))} required />
            <select value={form.default_severity} onChange={(e) => setForm((f) => ({ ...f, default_severity: e.target.value as Severity }))}>
              {Object.entries(SEVERITY_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
            <textarea placeholder="Описание" value={form.description} onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))} />
            <div><button type="submit" className="btn-primary">Создать</button></div>
          </form>
          {error && <div className="error-text">{error}</div>}
        </div>
      </div>
    </div>
  );
}
