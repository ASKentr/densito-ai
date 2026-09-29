import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";

export function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [username, setUsername] = useState("expert");
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
      <form className="login-card" onSubmit={handleSubmit}>
        <h1>Densito-AI</h1>
        <p className="muted">Оценка качества денситометрических исследований</p>
        <label>
          Логин
          <input name="username" value={username} onChange={(e) => setUsername(e.target.value)} autoFocus />
        </label>
        <label>
          Пароль
          <input name="password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </label>
        {error && <div className="error-text">{error}</div>}
        <button type="submit" disabled={busy}>{busy ? "Вход…" : "Войти"}</button>
        <div className="hint">
          Демо-доступы: <code>expert / expert12345</code> или <code>admin / admin12345</code>
        </div>
      </form>
    </div>
  );
}
