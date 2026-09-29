import axios from "axios";
import type {
  AuditLogEntryOut, FindingOut, StatsOut, StudyDetail, StudyListItem, UserOut, ViolationType,
} from "./types";

export const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

export const client = axios.create({ baseURL: API_BASE });

client.interceptors.request.use((config) => {
  const token = localStorage.getItem("token");
  if (token) {
    config.headers = config.headers ?? {};
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

client.interceptors.response.use(
  (r) => r,
  (error) => {
    if (error?.response?.status === 401) {
      localStorage.removeItem("token");
      localStorage.removeItem("role");
      localStorage.removeItem("username");
      if (!location.pathname.startsWith("/login")) {
        location.href = "/login";
      }
    }
    return Promise.reject(error);
  }
);

export async function login(username: string, password: string) {
  const form = new URLSearchParams();
  form.set("username", username);
  form.set("password", password);
  const { data } = await axios.post(`${API_BASE}/auth/login`, form, {
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
  });
  return data as { access_token: string; role: string; username: string };
}

export async function fetchMe(): Promise<UserOut> {
  const { data } = await client.get("/auth/me");
  return data;
}

export interface StudyFilters {
  search?: string;
  status?: string;
  review_status?: string;
  body_part?: string;
  verdict?: string;
  date_from?: string;
  date_to?: string;
}

export async function listStudies(filters: StudyFilters): Promise<StudyListItem[]> {
  const { data } = await client.get("/studies", { params: filters });
  return data;
}

export async function getStudy(id: number): Promise<StudyDetail> {
  const { data } = await client.get(`/studies/${id}`);
  return data;
}

export async function uploadStudy(file: File, onProgress?: (pct: number) => void): Promise<StudyDetail> {
  const form = new FormData();
  form.append("file", file);
  const { data } = await client.post("/studies/upload", form, {
    headers: { "Content-Type": "multipart/form-data" },
    onUploadProgress: (evt) => {
      if (onProgress && evt.total) onProgress(Math.round((evt.loaded / evt.total) * 100));
    },
  });
  return data;
}

export async function analyzeStudy(id: number): Promise<StudyDetail> {
  const { data } = await client.post(`/studies/${id}/analyze`);
  return data;
}

export async function completeReview(id: number): Promise<void> {
  await client.post(`/studies/${id}/complete_review`);
}

// Эндпоинты снимков требуют Bearer-токен, поэтому обычный <img src="..."> не
// сработает — грузим как blob через авторизованный axios-клиент и отдаём
// object URL. Вызывающий компонент обязан сам вызвать URL.revokeObjectURL.
export async function fetchImagePreviewBlobUrl(studyId: number, imageId: number): Promise<string> {
  const { data } = await client.get(`/studies/${studyId}/images/${imageId}/preview`, { responseType: "blob" });
  return URL.createObjectURL(data);
}

export async function fetchImageRawArrayBuffer(studyId: number, imageId: number): Promise<ArrayBuffer> {
  const { data } = await client.get(`/studies/${studyId}/images/${imageId}/raw`, { responseType: "arraybuffer" });
  return data;
}

function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function filenameFrom(disposition: unknown, fallback: string): string {
  if (typeof disposition !== "string") return fallback;
  return disposition.match(/filename="([^"]+)"/)?.[1] ?? fallback;
}

/** Отчёт DICOM SR по снимку (ТЗ п.2.6) — скачивается файлом .sr.dcm. */
export async function downloadImageSr(studyId: number, imageId: number): Promise<void> {
  const res = await client.get(`/studies/${studyId}/images/${imageId}/sr`, { responseType: "blob" });
  saveBlob(res.data, filenameFrom(res.headers["content-disposition"], `study${studyId}_image${imageId}.sr.dcm`));
}

export interface BatchResult { processed: number; failed: number; filename: string }

/** Пакетная обработка (POST /batch): таблица xlsx/csv или zip с таблицей и SR. */
export async function runBatch(file: File, format: "xlsx" | "csv", sr: boolean,
                               onProgress?: (pct: number) => void): Promise<BatchResult> {
  const form = new FormData();
  form.append("file", file);
  const res = await client.post("/batch", form, {
    params: { format, sr },
    responseType: "blob",
    headers: { "Content-Type": "multipart/form-data" },
    onUploadProgress: (evt) => {
      if (onProgress && evt.total) onProgress(Math.round((evt.loaded / evt.total) * 100));
    },
  });
  const filename = filenameFrom(res.headers["content-disposition"], sr ? "densito_results.zip" : `densito_results.${format}`);
  saveBlob(res.data, filename);
  return {
    processed: Number(res.headers["x-processed-files"] ?? 0),
    failed: Number(res.headers["x-failed-files"] ?? 0),
    filename,
  };
}

export async function reviewFinding(
  findingId: number,
  action: "confirm" | "reject" | "modify",
  payload?: { violation_type_id?: number; severity?: string; comment?: string }
): Promise<FindingOut> {
  const { data } = await client.patch(`/findings/${findingId}`, { action, ...payload });
  return data;
}

export async function addExpertFinding(studyId: number, payload: {
  image_id: number; violation_type_id: number; severity: string;
  bbox_x?: number; bbox_y?: number; bbox_w?: number; bbox_h?: number; comment: string;
}): Promise<FindingOut> {
  const { data } = await client.post(`/studies/${studyId}/findings`, payload);
  return data;
}

export async function listViolationTypes(): Promise<ViolationType[]> {
  const { data } = await client.get("/admin/violation-types");
  return data;
}

export async function createViolationType(payload: Partial<ViolationType>): Promise<ViolationType> {
  const { data } = await client.post("/admin/violation-types", payload);
  return data;
}

export async function updateViolationType(id: number, payload: Partial<ViolationType>): Promise<ViolationType> {
  const { data } = await client.patch(`/admin/violation-types/${id}`, payload);
  return data;
}

export async function listUsers(): Promise<UserOut[]> {
  const { data } = await client.get("/admin/users");
  return data;
}

export async function createUser(payload: { username: string; password: string; full_name: string; role: string }): Promise<UserOut> {
  const { data } = await client.post("/admin/users", payload);
  return data;
}

export async function deactivateUser(id: number): Promise<void> {
  await client.patch(`/admin/users/${id}/deactivate`);
}

export async function getAuditLog(): Promise<AuditLogEntryOut[]> {
  const { data } = await client.get("/admin/audit-log");
  return data;
}

export async function getStats(): Promise<StatsOut> {
  const { data } = await client.get("/admin/stats");
  return data;
}
