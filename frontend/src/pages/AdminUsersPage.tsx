import { useEffect, useState } from "react";
import { createUser, deactivateUser, listUsers } from "../api/client";
import type { Role, UserOut } from "../api/types";

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
      <h1>Пользователи</h1>
      <table className="studies-table">
        <thead><tr><th>Логин</th><th>ФИО</th><th>Роль</th><th>Статус</th><th></th></tr></thead>
        <tbody>
          {users.map((u) => (
            <tr key={u.id}>
              <td>{u.username}</td><td>{u.full_name}</td>
              <td>{u.role === "admin" ? "Администратор" : "Эксперт"}</td>
              <td>{u.is_active ? "Активен" : "Отключён"}</td>
              <td>{u.is_active && <button type="button" onClick={() => deactivateUser(u.id).then(reload)}>Отключить</button>}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h3>Новый пользователь</h3>
      <form onSubmit={handleCreate} className="admin-form">
        <input placeholder="Логин" value={form.username} onChange={(e) => setForm((f) => ({ ...f, username: e.target.value }))} required />
        <input placeholder="ФИО" value={form.full_name} onChange={(e) => setForm((f) => ({ ...f, full_name: e.target.value }))} />
        <input placeholder="Пароль" type="password" value={form.password} onChange={(e) => setForm((f) => ({ ...f, password: e.target.value }))} required />
        <select value={form.role} onChange={(e) => setForm((f) => ({ ...f, role: e.target.value as Role }))}>
          <option value="expert">Эксперт</option>
          <option value="admin">Администратор</option>
        </select>
        <button type="submit">Создать</button>
      </form>
      {error && <div className="error-text">{error}</div>}
    </div>
  );
}
