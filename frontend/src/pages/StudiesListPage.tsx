import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { listStudies, uploadStudy, type StudyFilters } from "../api/client";
import type { StudyListItem } from "../api/types";
import { formatDicomDate, Icon, REVIEW_LABELS, STATUS_LABELS, VERDICT_LABELS } from "../components/ui";

export function StudiesListPage() {
  const navigate = useNavigate();
  const [studies, setStudies] = useState<StudyListItem[]>([]);
  const [all, setAll] = useState<StudyListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [filters, setFilters] = useState<StudyFilters>({});
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const reload = useCallback(() => {
    setLoading(true);
    listStudies(filters).then(setStudies).finally(() => setLoading(false));
    listStudies({}).then(setAll);
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

  const analyzed = all.filter((s) => s.overall_verdict);
  const withIssues = analyzed.filter((s) => s.overall_verdict === "non_qualitative").length;
  const pending = analyzed.filter((s) => s.review_status !== "reviewed").length;
  const reviewed = all.filter((s) => s.review_status === "reviewed").length;
  const hasFilters = Object.values(filters).some(Boolean);

  return (
    <div className="studies-page">
      <div className="page-header">
        <div>
          <h1>Исследования</h1>
          <p>Загрузите DICOM-файл или ZIP-архив одного исследования: ИИ проверит качество, эксперт подтвердит результат.</p>
        </div>
        <div className="page-actions">
          <input ref={fileInputRef} type="file" accept=".dcm,.zip,application/dicom,application/zip"
                 onChange={handleFileSelected} />
          <button type="button" className="btn-primary" onClick={() => fileInputRef.current?.click()} disabled={uploading}>
            <Icon name="upload" /> {uploading ? "Загрузка…" : "Загрузить DICOM / ZIP"}
          </button>
        </div>
      </div>
      {uploadError && <div className="error-text">{uploadError}</div>}

      <div className="stats-grid">
        <div className="stat-card">
          <div className="stat-label"><i style={{ background: "var(--primary)" }} />Всего исследований</div>
          <div className="stat-value">{all.length}</div>
          <div className="stat-sub">проанализировано ИИ: {analyzed.length}</div>
        </div>
        <div className="stat-card">
          <div className="stat-label"><i style={{ background: "var(--bad)" }} />С нарушениями качества</div>
          <div className="stat-value">{withIssues}</div>
          <div className="stat-sub">{analyzed.length ? Math.round((withIssues / analyzed.length) * 100) : 0}% от проанализированных</div>
        </div>
        <div className="stat-card">
          <div className="stat-label"><i style={{ background: "var(--warn)" }} />Ожидают проверки</div>
          <div className="stat-value">{pending}</div>
          <div className="stat-sub">требуют решения эксперта</div>
        </div>
        <div className="stat-card">
          <div className="stat-label"><i style={{ background: "var(--ok)" }} />Проверено экспертом</div>
          <div className="stat-value">{reviewed}</div>
          <div className="stat-sub">проверка завершена</div>
        </div>
      </div>

      <div className="card table-card">
        <div className="filters-bar">
          <div className="search-field">
            <Icon name="search" />
            <input
              placeholder="Поиск по ID, описанию, области"
              value={filters.search ?? ""}
              onChange={(e) => setFilters((f) => ({ ...f, search: e.target.value || undefined }))}
            />
          </div>
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
          {hasFilters && <button type="button" className="btn-ghost" onClick={() => setFilters({})}>Сбросить</button>}
        </div>

        {loading ? (
          <div className="empty-state">Загрузка списка…</div>
        ) : studies.length === 0 ? (
          <div className="empty-state">
            <Icon name="inbox" size={28} />
            <div>{hasFilters ? "Под фильтры ничего не подходит." : "Исследований пока нет — загрузите DICOM-файл или ZIP-архив."}</div>
          </div>
        ) : (
          <div className="table-wrap">
            <table className="studies-table">
              <thead>
                <tr>
                  <th>ID</th><th>Дата</th><th>Исследование</th><th>Обработка</th>
                  <th>Оценка ИИ</th><th>Нарушений</th><th>Экспертная проверка</th><th>Обезличено</th>
                </tr>
              </thead>
              <tbody>
                {studies.map((s) => (
                  <tr key={s.id} onClick={() => navigate(`/studies/${s.id}`)} className="clickable-row">
                    <td className="cell-id">{s.display_id}</td>
                    <td className="num">{formatDicomDate(s.study_date)}</td>
                    <td>
                      <div className="cell-title">{s.study_description || s.body_part || "—"}</div>
                      {s.modality && <div className="cell-sub">{s.modality}</div>}
                    </td>
                    <td><span className={`badge status-${s.status}`}>{STATUS_LABELS[s.status]}</span></td>
                    <td>
                      {s.overall_verdict
                        ? <span className={`badge verdict-${s.overall_verdict}`}>{VERDICT_LABELS[s.overall_verdict]}</span>
                        : <span className="muted">—</span>}
                    </td>
                    <td className="num">{s.findings_count}</td>
                    <td><span className={`badge review-${s.review_status}`}>{REVIEW_LABELS[s.review_status]}</span></td>
                    <td>
                      {s.anonymization_ok === null ? <span className="muted">—</span>
                        : s.anonymization_ok ? <span className="badge plain verdict-qualitative">Да</span>
                        : <span className="badge plain verdict-non_qualitative">Нет</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
