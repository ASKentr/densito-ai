import axios from "axios";
import { useEffect, useState } from "react";
import { API_BASE } from "./client";

// Состояние ИИ-модуля из GET /health (backend: ai_module/status.py). Один общий
// опрос на всё приложение: раз в 30 с и при возврате на вкладку.

export interface AiComponent { name: string; ok: boolean; detail: string; ms: number }
export interface AiStatus {
  state: "checking" | "ready" | "degraded" | "offline";
  modelVersion: string;
  components: AiComponent[];
  checkedAt: Date | null;
}

const POLL_MS = 30_000;
let current: AiStatus = { state: "checking", modelVersion: "", components: [], checkedAt: null };
const listeners = new Set<(s: AiStatus) => void>();
let timer: number | undefined;

async function refresh() {
  try {
    const { data } = await axios.get(`${API_BASE}/health`, { timeout: 15_000 });
    current = {
      state: data?.ai?.ready ? "ready" : "degraded",
      modelVersion: data?.ai?.model_version ?? "",
      components: data?.ai?.components ?? [],
      checkedAt: new Date(),
    };
  } catch {
    current = { state: "offline", modelVersion: "", components: [], checkedAt: new Date() };
  }
  listeners.forEach((l) => l(current));
}

function start() {
  if (timer !== undefined) return;
  refresh();
  timer = window.setInterval(refresh, POLL_MS);
  window.addEventListener("focus", refresh);
}

export function recheckAiStatus() { return refresh(); }

export function useAiStatus(): AiStatus {
  const [s, setS] = useState(current);
  useEffect(() => {
    listeners.add(setS);
    start();
    setS(current);
    return () => { listeners.delete(setS); };
  }, []);
  return s;
}

export const AI_STATE_TEXT: Record<AiStatus["state"], string> = {
  checking: "Проверка ИИ-модуля…",
  ready: "ИИ-модуль готов",
  degraded: "ИИ-модуль недоступен",
  offline: "Сервер недоступен",
};

/** Почему анализ сейчас нельзя запустить (null — можно). */
export function aiBlockReason(s: AiStatus): string | null {
  if (s.state === "degraded") return "ИИ-модуль недоступен — подробности по индикатору в шапке";
  if (s.state === "offline") return "Нет связи с сервером";
  return null;
}
