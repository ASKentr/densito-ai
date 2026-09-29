import { useEffect, useState } from "react";
import { getAuditLog, getStats } from "../api/client";
import type { AuditLogEntryOut, StatsOut } from "../api/types";
import { ACTION_LABELS, CATEGORY_LABELS } from "../components/ui";

export function AdminStatsPage() {
  const [stats, setStats] = useState<StatsOut | null>(null);
  const [log, setLog] = useState<AuditLogEntryOut[]>([]);

  useEffect(() => {
    getStats().then(setStats);
    getAuditLog().then(setLog);
  }, []);

  const tiles: [string, keyof StatsOut, string][] = [
    ["Всего исследований", "total_studies", "var(--primary)"],
    ["Проанализировано ИИ", "analyzed_studies", "var(--info)"],
    ["Качественных", "qualitative", "var(--ok)"],
    ["С нарушениями", "non_qualitative", "var(--bad)"],
    ["Проверено экспертом", "reviewed", "var(--ok)"],
    ["Ожидают проверки", "pending_review", "var(--warn)"],
  ];
  const categories = stats ? Object.entries(stats.findings_by_category).sort((a, b) => b[1] - a[1]) : [];
  const maxCat = Math.max(1, ...categories.map(([, n]) => n));

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>Статистика</h1>
          <p>Сводка по исследованиям, находкам и действиям пользователей.</p>
        </div>
      </div>
      {stats && (
        <div className="stats-grid">
          {tiles.map(([label, key, color]) => (
            <div className="stat-card" key={key}>
              <div className="stat-label"><i style={{ background: color }} />{label}</div>
              <div className="stat-value">{stats[key] as number}</div>
            </div>
          ))}
        </div>
      )}

      <div className="split">
        <div className="card card-pad">
          <div className="section-title">Нарушения по категориям</div>
          {categories.length === 0 ? <p className="muted">Находок пока нет.</p> : (
            <div className="bars">
              {categories.map(([cat, n]) => (
                <div className="bar-row" key={cat}>
                  <span>{CATEGORY_LABELS[cat] ?? cat}</span>
                  <div className="bar-track"><div className="bar-fill" style={{ width: `${(n / maxCat) * 100}%` }} /></div>
                  <span className="mono" style={{ textAlign: "right" }}>{n}</span>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="card table-card">
          <div className="card-pad" style={{ paddingBottom: 0 }}><div className="section-title">Журнал действий</div></div>
          <div className="table-wrap" style={{ maxHeight: 480, overflowY: "auto" }}>
            <table className="studies-table">
              <thead><tr><th>Время</th><th>Пользователь</th><th>Действие</th><th>Детали</th></tr></thead>
              <tbody>
                {log.map((e) => (
                  <tr key={e.id}>
                    <td className="num small" style={{ whiteSpace: "nowrap" }}>{new Date(e.created_at).toLocaleString("ru-RU")}</td>
                    <td>{e.username}</td>
                    <td>
                      {ACTION_LABELS[e.action] ?? e.action}
                      {e.entity_id ? <span className="cell-sub"> · {e.entity} #{e.entity_id}</span> : null}
                    </td>
                    <td className="small muted">{e.details}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}
