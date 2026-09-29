import { useEffect, useState } from "react";
import { getAuditLog, getStats } from "../api/client";
import type { AuditLogEntryOut, StatsOut } from "../api/types";

export function AdminStatsPage() {
  const [stats, setStats] = useState<StatsOut | null>(null);
  const [log, setLog] = useState<AuditLogEntryOut[]>([]);

  useEffect(() => {
    getStats().then(setStats);
    getAuditLog().then(setLog);
  }, []);

  return (
    <div>
      <h1>Статистика</h1>
      {stats && (
        <div className="stats-grid">
          <div className="stat-card"><div className="stat-value">{stats.total_studies}</div><div>Всего исследований</div></div>
          <div className="stat-card"><div className="stat-value">{stats.analyzed_studies}</div><div>Проанализировано AI</div></div>
          <div className="stat-card"><div className="stat-value">{stats.qualitative}</div><div>Качественных</div></div>
          <div className="stat-card"><div className="stat-value">{stats.non_qualitative}</div><div>С нарушениями</div></div>
          <div className="stat-card"><div className="stat-value">{stats.reviewed}</div><div>Проверено экспертом</div></div>
          <div className="stat-card"><div className="stat-value">{stats.pending_review}</div><div>Ожидают проверки</div></div>
        </div>
      )}
      {stats && Object.keys(stats.findings_by_category).length > 0 && (
        <>
          <h3>Нарушения по категориям</h3>
          <ul>
            {Object.entries(stats.findings_by_category).map(([cat, count]) => <li key={cat}>{cat}: {count}</li>)}
          </ul>
        </>
      )}

      <h3>Журнал действий</h3>
      <table className="studies-table">
        <thead><tr><th>Время</th><th>Пользователь</th><th>Действие</th><th>Объект</th><th>Детали</th></tr></thead>
        <tbody>
          {log.map((e) => (
            <tr key={e.id}>
              <td>{new Date(e.created_at).toLocaleString("ru-RU")}</td>
              <td>{e.username}</td><td>{e.action}</td>
              <td>{e.entity}{e.entity_id ? ` #${e.entity_id}` : ""}</td>
              <td>{e.details}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
