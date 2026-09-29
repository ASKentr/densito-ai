import { useEffect, useState } from "react";
import { createViolationType, listViolationTypes, updateViolationType } from "../api/client";
import type { Severity, ViolationType } from "../api/types";

const SEVERITY_LABELS: Record<Severity, string> = { low: "Низкая", medium: "Средняя", high: "Высокая", critical: "Критическая" };

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
      <h1>Справочник нарушений</h1>
      <p className="muted">Редактируется администратором. AI-модуль и backend обращаются к записям по устойчивому
        полю <code>code</code>, поэтому переименование не ломает анализ; системные типы нельзя удалить — только деактивировать.</p>

      <table className="studies-table">
        <thead><tr><th>Code</th><th>Название</th><th>Категория</th><th>Критичность по умолчанию</th><th>Системный</th><th>Активен</th><th></th></tr></thead>
        <tbody>
          {items.map((vt) => (
            <tr key={vt.id}>
              <td><code>{vt.code}</code></td>
              <td>{vt.name_ru}<div className="muted small">{vt.description}</div></td>
              <td>{vt.category}</td>
              <td>{SEVERITY_LABELS[vt.default_severity]}</td>
              <td>{vt.is_system ? "Да" : "Нет"}</td>
              <td>{vt.is_active ? "Активен" : "Отключён"}</td>
              <td><button type="button" onClick={() => toggleActive(vt)}>{vt.is_active ? "Отключить" : "Включить"}</button></td>
            </tr>
          ))}
        </tbody>
      </table>

      <h3>Добавить новый тип нарушения</h3>
      <form onSubmit={handleCreate} className="admin-form">
        <input placeholder="code (латиницей, уникальный)" value={form.code} onChange={(e) => setForm((f) => ({ ...f, code: e.target.value }))} required />
        <input placeholder="Название (рус.)" value={form.name_ru} onChange={(e) => setForm((f) => ({ ...f, name_ru: e.target.value }))} required />
        <input placeholder="Категория" value={form.category} onChange={(e) => setForm((f) => ({ ...f, category: e.target.value }))} required />
        <select value={form.default_severity} onChange={(e) => setForm((f) => ({ ...f, default_severity: e.target.value as Severity }))}>
          {Object.entries(SEVERITY_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        <textarea placeholder="Описание" value={form.description} onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))} />
        <button type="submit">Создать</button>
      </form>
      {error && <div className="error-text">{error}</div>}
    </div>
  );
}
