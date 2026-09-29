import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { BrandMark, Icon } from "../components/ui";

const DEMO = [
  { username: "expert", password: "expert12345", label: "Эксперт" },
  { username: "admin", password: "admin12345", label: "Администратор" },
];

export function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(username, password);
      navigate("/studies");
    } catch {
      setError("Неверный логин или пароль");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-page">
      <section className="login-hero">
        <div className="brand"><BrandMark size={34} /><span>Densito-AI</span></div>
        <div style={{ position: "relative", zIndex: 1 }}>
          <h1>Контроль качества денситометрических исследований</h1>
          <p>ИИ-помощник проверяет снимки поясничного отдела позвоночника и проксимального отдела бедра
            до того, как их увидит врач.</p>
          <ul className="hero-points">
            <li><Icon name="check" /> Укладка, ось позвоночника, посторонние предметы и область интереса</li>
            <li><Icon name="check" /> Экспертная проверка находок ИИ прямо на снимке</li>
            <li><Icon name="check" /> Пакетная обработка архивов, таблица результатов и отчёты DICOM SR</li>
          </ul>
        </div>
        <div className="hero-foot">Работает локально: снимки не передаются во внешние системы.</div>
      </section>

      <section className="login-side">
        <form className="login-card" onSubmit={handleSubmit}>
          <div>
            <h2>Вход в систему</h2>
            <p className="muted" style={{ marginTop: 4 }}>Используйте учётную запись эксперта или администратора</p>
          </div>
          <label>
            Логин
            <input name="username" value={username} onChange={(e) => setUsername(e.target.value)} autoFocus autoComplete="username" />
          </label>
          <label>
            Пароль
            <input name="password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" />
          </label>
          {error && <div className="error-text">{error}</div>}
          <button type="submit" className="btn-primary" disabled={busy || !username || !password}>
            {busy ? "Вход…" : "Войти"}
          </button>
          <div className="demo-box">
            <div className="small muted">Демо-доступ: нажмите, чтобы подставить</div>
            <div className="demo-row">
              {DEMO.map((d) => (
                <button key={d.username} type="button" className="btn-sm"
                        onClick={() => { setUsername(d.username); setPassword(d.password); }}>
                  {d.label} · <span className="mono">{d.username}</span>
                </button>
              ))}
            </div>
          </div>
        </form>
      </section>
    </div>
  );
}
