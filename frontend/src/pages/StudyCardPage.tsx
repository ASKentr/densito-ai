import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { analyzeStudy, completeReview, getStudy, listViolationTypes } from "../api/client";
import { DicomViewer } from "../components/DicomViewer";
import { FindingsPanel } from "../components/FindingsPanel";
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
  const [error, setError] = useState<string | null>(null);

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
      setError(e?.response?.data?.detail || "Ошибка запуска AI-анализа");
    } finally {
      setAnalyzing(false);
    }
  }

  async function handleCompleteReview() {
    await completeReview(studyId);
    reload();
  }

  if (!study) return <p className="muted">Загрузка исследования…</p>;

  const imageFindings = study.findings.filter((f) => f.image_id === activeImageId);

  return (
    <div className="study-card-page">
      <button className="link-back" onClick={() => navigate("/studies")}>&larr; К списку исследований</button>

      <div className="study-card-grid">
        <div className="study-info-col">
          <h2>{study.display_id}</h2>
          <dl className="info-list">
            <dt>Дата</dt><dd>{study.study_date || "—"}</dd>
            <dt>Тип/область</dt><dd>{study.study_description || study.body_part || "—"}</dd>
            <dt>Модальность</dt><dd>{study.modality || "—"}</dd>
            <dt>Статус</dt><dd>{study.status}</dd>
            <dt>Обезличивание</dt>
            <dd>
              {study.anonymization_ok === false ? (
                <span className="error-text">
                  ⚠ Обнаружены персональные данные
                  <ul>
                    {study.anonymization_report.issues.map((iss, i) => (
                      <li key={i}>{iss.file}: {iss.found_phi.map((p) => p.label).join(", ")}</li>
                    ))}
                  </ul>
                </span>
              ) : "Пройдено"}
            </dd>
            {study.overall_verdict && (
              <>
                <dt>Итоговая оценка AI</dt>
                <dd>{study.overall_verdict === "qualitative" ? "Качественное" : "Есть нарушения"} ({study.overall_score?.toFixed(0)}/100)</dd>
                <dt>Версия модели</dt><dd>{study.model_version}</dd>
              </>
            )}
          </dl>

          <div className="image-thumbs">
            {study.images.map((img) => (
              <button key={img.id} className={img.id === activeImageId ? "thumb active" : "thumb"}
                      onClick={() => setActiveImageId(img.id)}>
                {img.filename}
              </button>
            ))}
          </div>

          <div className="study-actions">
            <button type="button" onClick={handleAnalyze} disabled={analyzing}>
              {analyzing ? "Анализ выполняется…" : study.status === "analyzed" ? "Повторить AI-анализ" : "Запустить AI-анализ"}
            </button>
            <button type="button" onClick={handleCompleteReview} disabled={study.review_status === "reviewed"}>
              {study.review_status === "reviewed" ? "Проверка завершена" : "Завершить экспертную проверку"}
            </button>
          </div>
          {error && <div className="error-text">{error}</div>}
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
          ) : <p className="muted">В исследовании нет изображений.</p>}
        </div>

        <div className="findings-col">
          <FindingsPanel
            studyId={studyId}
            imageId={activeImageId}
            findings={imageFindings}
            violationTypes={violationTypes}
            pendingBbox={pendingBbox}
            onRequestDraw={() => setDrawMode(true)}
            onBboxConsumed={() => setPendingBbox(null)}
            onChanged={reload}
          />
        </div>
      </div>
    </div>
  );
}
