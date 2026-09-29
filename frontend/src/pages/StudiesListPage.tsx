import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { listStudies, uploadStudy, type StudyFilters } from "../api/client";
import type { StudyListItem } from "../api/types";

const STATUS_LABELS: Record<string, string> = {
  uploaded: "Загружено", processing: "Обрабатывается", analyzed: "Проанализировано", error: "Ошибка",
};
const REVIEW_LABELS: Record<string, string> = {
  not_reviewed: "Не проверено", in_review: "На проверке", reviewed: "Проверено экспертом",
};
const VERDICT_LABELS: Record<string, string> = {
  qualitative: "Качественное", non_qualitative: "Есть нарушения",
};

export function StudiesListPage() {
  const navigate = useNavigate();
  const [studies, setStudies] = useState<StudyListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [filters, setFilters] = useState<StudyFilters>({});
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const reload = useCallback(() => {
    setLoading(true);
    listStudies(filters).then(setStudies).finally(() => setLoading(false));
  }, [filters]);

  useEffect(() => { reload(); }, [reload]);

  async function handleFileSelected(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    setUploadError(null);
    try {
      const study = await uploadStudy(file);
      reload();
      navigate(`/studies/${study.id}`);
    } catch (err: any) {
      setUploadError(err?.response?.data?.detail || "Не удалось загрузить файл");
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  }

  return (
    <div className="studies-page">
      <div className="page-header">
        <h1>Исследования</h1>
        <div>
          <input ref={fileInputRef} type="file" accept=".dcm,.zip,application/dicom,application/zip"
                 style={{ display: "none" }} onChange={handleFileSelected} />
          <button type="button" onClick={() => fileInputRef.current?.click()} disabled={uploading}>
            {uploading ? "Загрузка…" : "Загрузить DICOM / ZIP"}
          </button>
        </div>
      </div>
      {uploadError && <div className="error-text">{uploadError}</div>}

      <div className="filters-bar">
        <input
          placeholder="Поиск по ID, описанию, области"
          value={filters.search ?? ""}
          onChange={(e) => setFilters((f) => ({ ...f, search: e.target.value || undefined }))}
        />
        <select value={filters.status ?? ""} onChange={(e) => setFilters((f) => ({ ...f, status: e.target.value || undefined }))}>
          <option value="">Все статусы</option>
          {Object.entries(STATUS_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        <select value={filters.review_status ?? ""} onChange={(e) => setFilters((f) => ({ ...f, review_status: e.target.value || undefined }))}>
          <option value="">Любой статус проверки</option>
          {Object.entries(REVIEW_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        <select value={filters.verdict ?? ""} onChange={(e) => setFilters((f) => ({ ...f, verdict: e.target.value || undefined }))}>
          <option value="">Любая оценка</option>
          {Object.entries(VERDICT_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
      </div>

      {loading ? (
        <p className="muted">Загрузка списка…</p>
      ) : studies.length === 0 ? (
        <p className="muted">Исследований пока нет — загрузите DICOM-файл или ZIP-архив.</p>
      ) : (
        <table className="studies-table">
          <thead>
            <tr>
              <th>ID</th><th>Дата</th><th>Область</th><th>Статус обработки</th>
              <th>Итоговая оценка</th><th>Нарушений</th><th>Экспертная проверка</th><th>Обезличено</th>
            </tr>
          </thead>
          <tbody>
            {studies.map((s) => (
              <tr key={s.id} onClick={() => navigate(`/studies/${s.id}`)} className="clickable-row">
                <td>{s.display_id}</td>
                <td>{s.study_date ? formatDicomDate(s.study_date) : "—"}</td>
                <td>{s.study_description || s.body_part || "—"}</td>
                <td><span className={`badge status-${s.status}`}>{STATUS_LABELS[s.status]}</span></td>
                <td>
                  {s.overall_verdict ? (
                    <span className={`badge verdict-${s.overall_verdict}`}>
                      {VERDICT_LABELS[s.overall_verdict]} ({s.overall_score?.toFixed(0)})
                    </span>
                  ) : "—"}
                </td>
                <td>{s.findings_count}</td>
                <td><span className={`badge review-${s.review_status}`}>{REVIEW_LABELS[s.review_status]}</span></td>
                <td>{s.anonymization_ok === null ? "—" : s.anonymization_ok ? "Да" : "⚠ Нет"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function formatDicomDate(d: string): string {
  if (d.length !== 8) return d;
  return `${d.slice(6, 8)}.${d.slice(4, 6)}.${d.slice(0, 4)}`;
}
