import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { aiBlockReason, useAiStatus } from "../api/aiStatus";
import { analyzeStudy, completeReview, downloadImageSr, getStudy, listViolationTypes } from "../api/client";
import { DicomViewer } from "../components/DicomViewer";
import { FindingsPanel } from "../components/FindingsPanel";
import { formatDicomDate, Icon, ProbMeter, REVIEW_LABELS, STATUS_LABELS, VERDICT_LABELS } from "../components/ui";
import type { StudyDetail, ViolationType } from "../api/types";

export function StudyCardPage() {
  const { id } = useParams();
  const studyId = Number(id);
  const navigate = useNavigate();

  const [study, setStudy] = useState<StudyDetail | null>(null);
  const [violationTypes, setViolationTypes] = useState<ViolationType[]>([]);
  const [activeImageId, setActiveImageId] = useState<number | null>(null);
  const [drawMode, setDrawMode] = useState(false);
  const [pendingBbox, setPendingBbox] = useState<{ x: number; y: number; w: number; h: number } | null>(null);
  const [analyzing, setAnalyzing] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const aiBlock = aiBlockReason(useAiStatus());

  const reload = useCallback(() => {
    getStudy(studyId).then((s) => {
      setStudy(s);
      setActiveImageId((prev) => prev ?? s.images[0]?.id ?? null);
    });
  }, [studyId]);

  useEffect(() => { reload(); }, [reload]);
  useEffect(() => { listViolationTypes().then(setViolationTypes); }, []);

  async function handleAnalyze() {
    setAnalyzing(true);
    setError(null);
    try {
      const s = await analyzeStudy(studyId);
      setStudy(s);
    } catch (e: any) {
      setError(e?.response?.data?.detail || "Ошибка запуска ИИ-анализа");
    } finally {
      setAnalyzing(false);
    }
  }

  async function handleCompleteReview() {
    await completeReview(studyId);
    reload();
  }

  async function handleExportSr() {
    if (!activeImageId) return;
    setExporting(true);
    setError(null);
    try {
      await downloadImageSr(studyId, activeImageId);
    } catch {
      setError("Не удалось сформировать отчёт DICOM SR");
    } finally {
      setExporting(false);
    }
  }

  if (!study) return <p className="muted">Загрузка исследования…</p>;

  const imageFindings = study.findings.filter((f) => f.image_id === activeImageId);
  const activeImage = study.images.find((img) => img.id === activeImageId) ?? null;
  const analyzed = study.status === "analyzed";

  return (
    <div className="study-card-page">
      <div className="breadcrumbs">
        <button type="button" onClick={() => navigate("/studies")}>Исследования</button>
        <span>/</span>
        <span className="mono">{study.display_id}</span>
      </div>

      <div className="study-head">
        <div>
          <h1>{study.display_id}</h1>
          <div className="head-meta">
            <span className={`badge status-${study.status}`}>{STATUS_LABELS[study.status]}</span>
            {study.overall_verdict && (
              <span className={`badge verdict-${study.overall_verdict}`}>{VERDICT_LABELS[study.overall_verdict]}</span>
            )}
            <span className={`badge review-${study.review_status}`}>{REVIEW_LABELS[study.review_status]}</span>
          </div>
        </div>
        <div className="page-actions">
          <button type="button" onClick={handleAnalyze} disabled={analyzing || !!aiBlock} title={aiBlock ?? undefined}>
            <Icon name={analyzed ? "refresh" : "play"} />
            {analyzing ? "Анализ выполняется…" : analyzed ? "Повторить ИИ-анализ" : "Запустить ИИ-анализ"}
          </button>
          <button type="button" className="btn-primary" onClick={handleCompleteReview}
                  disabled={study.review_status === "reviewed" || !analyzed}>
            <Icon name="check" />
            {study.review_status === "reviewed" ? "Проверка завершена" : "Завершить проверку"}
          </button>
        </div>
      </div>
      {aiBlock && <div className="block-hint" style={{ margin: "-6px 0 12px" }}>ИИ-анализ сейчас недоступен: {aiBlock}</div>}
      {error && <div className="error-text">{error}</div>}

      <div className="study-card-grid">
        <div className="study-info-col">
          <div className="card card-pad">
            <div className="section-title">Сведения</div>
            <dl className="info-list">
              <dt>Дата</dt><dd>{formatDicomDate(study.study_date)}</dd>
              <dt>Исследование</dt><dd>{study.study_description || study.body_part || "—"}</dd>
              <dt>Модальность</dt><dd>{study.modality || "—"}</dd>
              <dt>Файл</dt><dd>{study.original_filename}</dd>
              <dt>Обезличивание</dt>
              <dd>
                {study.anonymization_ok === false ? (
                  <div className="phi-alert">
                    Обнаружены персональные данные
                    <ul>
                      {study.anonymization_report.issues.map((iss, i) => (
                        <li key={i}>{iss.file}: {iss.found_phi.map((p) => p.label).join(", ")}</li>
                      ))}
                    </ul>
                  </div>
                ) : <span className="badge plain verdict-qualitative">Пройдено</span>}
              </dd>
              {study.model_version && (<><dt>Модель ИИ</dt><dd className="mono small">{study.model_version}</dd></>)}
            </dl>
          </div>

          <div className="card card-pad">
            <div className="section-title">Снимки · {study.images.length}</div>
            <div className="image-thumbs">
              {study.images.map((img) => (
                <button key={img.id} type="button" className={img.id === activeImageId ? "thumb active" : "thumb"}
                        onClick={() => setActiveImageId(img.id)}>
                  <span>
                    <span className="thumb-name">{img.filename}</span>
                    <span className="thumb-sub">{img.anatomical_region || "область не определена"}</span>
                  </span>
                  {img.quality_prob != null && (
                    <span className="prob-value" style={{ color: img.quality_prob >= 0.5 ? "var(--bad)" : "var(--muted)" }}>
                      {img.quality_prob.toFixed(2)}
                    </span>
                  )}
                </button>
              ))}
            </div>
          </div>

          {activeImage && (
            <div className="card card-pad">
              <div className="section-title">Результат по снимку</div>
              <dl className="info-list" style={{ marginBottom: 12 }}>
                <dt>Область</dt><dd>{activeImage.anatomical_region || "—"}</dd>
                <dt>Нарушений</dt><dd>{imageFindings.filter((f) => f.status !== "rejected").length}</dd>
              </dl>
              <div className="small muted" style={{ marginBottom: 6 }}>Вероятность нарушения качества</div>
              <ProbMeter value={activeImage.quality_prob} />
              <div className="study-actions" style={{ marginTop: 14 }}>
                <button type="button" onClick={handleExportSr} disabled={!analyzed || exporting}>
                  <Icon name="file" /> {exporting ? "Формирование…" : "Скачать отчёт DICOM SR"}
                </button>
                <div className="sr-hint">Текстовый отчёт в стандарте DICOM: открывается в PACS рядом со снимком.</div>
              </div>
            </div>
          )}
        </div>

        <div className="viewer-col">
          {activeImageId ? (
            <DicomViewer
              studyId={studyId}
              imageId={activeImageId}
              findings={imageFindings}
              drawMode={drawMode}
              onRegionDrawn={(bbox) => { setPendingBbox(bbox); setDrawMode(false); }}
              onExitDrawMode={() => setDrawMode(false)}
            />
          ) : <div className="card empty-state">В исследовании нет изображений.</div>}
        </div>

        <div className="findings-col">
          <div className="card card-pad">
            <FindingsPanel
              studyId={studyId}
              imageId={activeImageId}
              findings={imageFindings}
              violationTypes={violationTypes}
              pendingBbox={pendingBbox}
              analyzed={analyzed}
              onRequestDraw={() => setDrawMode(true)}
              onBboxConsumed={() => setPendingBbox(null)}
              onChanged={reload}
            />
          </div>
        </div>
      </div>
    </div>
  );
}
