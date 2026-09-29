import { useRef, useState } from "react";
import { aiBlockReason, useAiStatus } from "../api/aiStatus";
import { runBatch, type BatchResult } from "../api/client";
import { Icon } from "../components/ui";

// Пакетная обработка из веб-интерфейса (ТЗ п.2.6, 2.7): тот же POST /batch,
// что и для API/CLI. Исследования в список не сохраняются — только таблица.
export function BatchPage() {
  const inputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [format, setFormat] = useState<"xlsx" | "csv">("xlsx");
  const [withSr, setWithSr] = useState(true);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState(0);
  const [drag, setDrag] = useState(false);
  const [result, setResult] = useState<BatchResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const aiBlock = aiBlockReason(useAiStatus());

  function pick(f: File | undefined | null) {
    if (!f) return;
    setFile(f);
    setResult(null);
    setError(null);
  }

  async function start() {
    if (!file) return;
    setBusy(true);
    setError(null);
    setResult(null);
    setProgress(0);
    try {
      setResult(await runBatch(file, format, withSr, setProgress));
    } catch (e: any) {
      let detail = "Не удалось обработать архив";
      const data = e?.response?.data;
      if (data instanceof Blob) {
        try { detail = JSON.parse(await data.text()).detail ?? detail; } catch { /* не JSON */ }
      }
      setError(detail);
    } finally {
      setBusy(false);
    }
  }

  const sizeMb = file ? (file.size / 1024 / 1024).toFixed(1) : "";

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>Пакетная обработка</h1>
          <p>Архив с исследованиями → таблица результатов по каждому снимку (формат ТЗ, п.2.5).</p>
        </div>
      </div>

      <div className="batch-grid">
        <div className="card card-pad stack">
          <input ref={inputRef} type="file" accept=".zip,.dcm,application/zip,application/dicom"
                 onChange={(e) => pick(e.target.files?.[0])} />
          <div className={drag ? "dropzone drag" : "dropzone"}
               onClick={() => inputRef.current?.click()}
               onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
               onDragLeave={() => setDrag(false)}
               onDrop={(e) => { e.preventDefault(); setDrag(false); pick(e.dataTransfer.files?.[0]); }}>
            <Icon name="archive" size={28} />
            {file
              ? <><b>{file.name}</b><span className="small">{sizeMb} МБ · нажмите, чтобы выбрать другой файл</span></>
              : <><b>Перетащите ZIP-архив сюда или нажмите для выбора</b>
                  <span className="small">Папки и вложенные архивы допускаются; можно загрузить и один DICOM-файл</span></>}
          </div>

          <div>
            <div className="option-row">
              <div style={{ flex: 1 }}>
                <div style={{ fontWeight: 500 }}>Формат таблицы</div>
                <div className="small muted">Колонки — дословно по ТЗ, одна строка на снимок</div>
              </div>
              <div className="seg">
                <button type="button" className={format === "xlsx" ? "on" : ""} onClick={() => setFormat("xlsx")}>XLSX</button>
                <button type="button" className={format === "csv" ? "on" : ""} onClick={() => setFormat("csv")}>CSV</button>
              </div>
            </div>
            <label className="option-row" style={{ cursor: "pointer" }}>
              <input type="checkbox" checked={withSr} onChange={(e) => setWithSr(e.target.checked)} />
              <div>
                <div style={{ fontWeight: 500 }}>Добавить отчёты DICOM SR</div>
                <div className="small muted">Результат придёт ZIP-архивом: таблица и папка sr/ с текстовым отчётом по каждому снимку</div>
              </div>
            </label>
          </div>

          <button type="button" className="btn-primary" onClick={start} disabled={!file || busy || !!aiBlock} style={{ height: 42 }}>
            <Icon name="play" />
            {busy ? (progress < 100 ? `Загрузка архива… ${progress}%` : "ИИ обрабатывает снимки…") : "Запустить обработку"}
          </button>

          {aiBlock && <div className="block-hint">Обработка сейчас недоступна: {aiBlock}</div>}
          {result && (
            <div className={result.failed ? "result-box fail" : "result-box"}>
              <Icon name={result.failed ? "alert" : "check"} size={20} />
              <div>
                <div style={{ fontWeight: 600 }}>Готово: обработано файлов — {result.processed}
                  {result.failed ? `, с ошибкой — ${result.failed}` : ""}</div>
                <div className="small">Файл <span className="mono">{result.filename}</span> сохранён в папку загрузок браузера.</div>
              </div>
            </div>
          )}
          {error && <div className="result-box fail"><Icon name="alert" size={20} /> {error}</div>}
        </div>

        <div className="card card-pad stack">
          <div className="section-title">Что в результате</div>
          <ol className="steps">
            <li><b>path_to_study</b>, <b>study_uid</b>, <b>image_uid</b> — где лежит снимок и его идентификаторы</li>
            <li><b>anatomical_region</b> — поясничный отдел позвоночника или проксимальный отдел бедра</li>
            <li><b>quality_class</b> — 0 качественное, 1 есть нарушение; <b>quality_prob</b> — вероятность</li>
            <li><b>violation_type</b> — одно или несколько нарушений через «;»</li>
            <li><b>processing_status</b> и <b>time_of_processing</b> — Success/Failure и время в секундах</li>
          </ol>
          <div className="section-title" style={{ marginTop: 6 }}>Тот же сценарий без браузера</div>
          <div className="small muted">REST API: <code>POST /batch?format=xlsx&amp;sr=true</code><br />
            Командная строка: <code>./run.sh batch архив.zip результат.xlsx</code></div>
        </div>
      </div>
    </div>
  );
}
