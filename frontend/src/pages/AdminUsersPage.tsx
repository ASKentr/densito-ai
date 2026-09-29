import { useEffect, useState } from "react";
import { createUser, deactivateUser, listUsers } from "../api/client";
import type { Role, UserOut } from "../api/types";
import { initials } from "../components/ui";

export function AdminUsersPage() {
  const [users, setUsers] = useState<UserOut[]>([]);
  const [form, setForm] = useState({ username: "", password: "", full_name: "", role: "expert" as Role });
  const [error, setError] = useState<string | null>(null);

  function reload() { listUsers().then(setUsers); }
  useEffect(reload, []);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await createUser(form);
      setForm({ username: "", password: "", full_name: "", role: "expert" });
      reload();
    } catch (err: any) {
      setError(err?.response?.data?.detail || "Не удалось создать пользователя");
    }
  }

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>Пользователи</h1>
          <p>Эксперты проверяют находки ИИ; администратор дополнительно ведёт справочник и учётные записи.</p>
        </div>
      </div>

      <div className="stack">
        <div className="card table-card">
          <div className="table-wrap">
            <table className="studies-table">
              <thead><tr><th>Пользователь</th><th>Роль</th><th>Статус</th><th></th></tr></thead>
              <tbody>
                {users.map((u) => (
                  <tr key={u.id}>
                    <td>
                      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                        <div className="avatar">{initials(u.full_name || u.username)}</div>
                        <div>
                          <div className="cell-title">{u.full_name || u.username}</div>
                          <div className="cell-sub mono">{u.username}</div>
                        </div>
                      </div>
                    </td>
                    <td>
                      <span className={`badge plain ${u.role === "admin" ? "source-expert" : "source-ai"}`}>
                        {u.role === "admin" ? "Администратор" : "Эксперт"}
                      </span>
                    </td>
                    <td>
                      <span className={`badge ${u.is_active ? "verdict-qualitative" : "status-uploaded"}`}>
                        {u.is_active ? "Активен" : "Отключён"}
                      </span>
                    </td>
                    <td style={{ textAlign: "right" }}>
                      {u.is_active && <button type="button" className="btn-sm btn-bad" onClick={() => deactivateUser(u.id).then(reload)}>Отключить</button>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        <div className="card card-pad">
          <div className="section-title">Новый пользователь</div>
          <form onSubmit={handleCreate} className="admin-form">
            <input placeholder="Логин" value={form.username} onChange={(e) => setForm((f) => ({ ...f, username: e.target.value }))} required />
            <input placeholder="ФИО" value={form.full_name} onChange={(e) => setForm((f) => ({ ...f, full_name: e.target.value }))} />
            <input placeholder="Пароль" type="password" value={form.password} onChange={(e) => setForm((f) => ({ ...f, password: e.target.value }))} required />
            <select value={form.role} onChange={(e) => setForm((f) => ({ ...f, role: e.target.value as Role }))}>
              <option value="expert">Эксперт</option>
              <option value="admin">Администратор</option>
            </select>
            <button type="submit" className="btn-primary">Создать</button>
          </form>
          {error && <div className="error-text">{error}</div>}
        </div>
      </div>
    </div>
  );
}
