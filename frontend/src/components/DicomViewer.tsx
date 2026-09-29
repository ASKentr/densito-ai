import { useCallback, useEffect, useRef, useState } from "react";
import { fetchImageRawArrayBuffer } from "../api/client";
import type { FindingOut } from "../api/types";
import { type DicomPixelData, parseDicomPixels, renderWindowedImageData } from "./dicomPixels";

const SEVERITY_COLOR: Record<string, string> = {
  low: "#eab308", medium: "#f97316", high: "#ef4444", critical: "#b91c1c",
};

interface Props {
  studyId: number;
  imageId: number;
  findings: FindingOut[];
  drawMode: boolean;
  /** [мм по строкам (Y), мм по столбцам (X)] — рисуем в физических пропорциях */
  pixelSpacing?: number[] | null;
  onRegionDrawn?: (bbox: { x: number; y: number; w: number; h: number }) => void;
  onExitDrawMode?: () => void;
}

export function DicomViewer({ studyId, imageId, findings, drawMode, pixelSpacing, onRegionDrawn, onExitDrawMode }: Props) {
  // высота пикселя относительно ширины: 1,05 / 0,6 = 1,75 для снимков датасета
  // (внешнее ревью, замечание 5: раньше пиксель рисовался квадратным)
  const aspect = pixelSpacing && pixelSpacing[0] > 0 && pixelSpacing[1] > 0 ? pixelSpacing[0] / pixelSpacing[1] : 1;
  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const offscreenRef = useRef<HTMLCanvasElement | null>(null);
  const dataRef = useRef<DicomPixelData | null>(null);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [wc, setWc] = useState(0);
  const [ww, setWw] = useState(1);
  const [scale, setScale] = useState(1);
  const [offset, setOffset] = useState({ x: 0, y: 0 });

  const panState = useRef<{ dragging: boolean; lastX: number; lastY: number }>({ dragging: false, lastX: 0, lastY: 0 });
  const drawState = useRef<{ drawing: boolean; startX: number; startY: number; curX: number; curY: number }>({
    drawing: false, startX: 0, startY: 0, curX: 0, curY: 0,
  });

  // --- загрузка и парсинг DICOM ---
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchImageRawArrayBuffer(studyId, imageId)
      .then((buf) => {
        if (cancelled) return;
        const parsed = parseDicomPixels(buf);
        dataRef.current = parsed;
        setWc(parsed.defaultWc);
        setWw(parsed.defaultWw);

        const container = containerRef.current;
        if (container) {
          const fitScale = Math.min(
            (container.clientWidth - 16) / parsed.cols,
            (container.clientHeight - 16) / (parsed.rows * aspect)
          );
          setScale(fitScale > 0 ? fitScale : 1);
          setOffset({
            x: (container.clientWidth - parsed.cols * fitScale) / 2,
            y: (container.clientHeight - parsed.rows * aspect * fitScale) / 2,
          });
        }
        setLoading(false);
      })
      .catch((e) => {
        if (!cancelled) {
          setError(e?.message || "Не удалось загрузить и разобрать DICOM-файл");
          setLoading(false);
        }
      });
    return () => { cancelled = true; };
  }, [studyId, imageId, aspect]);

  const redraw = useCallback(() => {
    const canvas = canvasRef.current;
    const data = dataRef.current;
    const container = containerRef.current;
    if (!canvas || !data || !container) return;

    canvas.width = container.clientWidth;
    canvas.height = container.clientHeight;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    if (!offscreenRef.current) {
      offscreenRef.current = document.createElement("canvas");
    }
    const off = offscreenRef.current;
    off.width = data.cols;
    off.height = data.rows;
    const offCtx = off.getContext("2d");
    if (offCtx) {
      offCtx.putImageData(renderWindowedImageData(data, wc, ww), 0, 0);
    }

    ctx.fillStyle = "#0b0f14";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(off, offset.x, offset.y, data.cols * scale, data.rows * scale * aspect);

    // оверлей находок
    for (const f of findings) {
      if (f.bbox_x == null || f.bbox_y == null || f.bbox_w == null || f.bbox_h == null) continue;
      const x = offset.x + f.bbox_x * data.cols * scale;
      const y = offset.y + f.bbox_y * data.rows * scale * aspect;
      const w = f.bbox_w * data.cols * scale;
      const h = f.bbox_h * data.rows * scale * aspect;
      const color = SEVERITY_COLOR[f.severity] ?? "#f97316";
      ctx.strokeStyle = color;
      ctx.lineWidth = f.source === "ai" ? 2 : 3;
      if (f.source === "expert") ctx.setLineDash([6, 3]); else ctx.setLineDash([]);
      ctx.strokeRect(x, y, w, h);
      ctx.setLineDash([]);

      const label = `${f.source === "ai" ? "ИИ" : "Эксперт"}: ${f.violation_name}`;
      ctx.font = "500 12px 'IBM Plex Sans', system-ui, sans-serif";
      const textW = ctx.measureText(label).width;
      ctx.fillStyle = color;
      ctx.fillRect(x, Math.max(0, y - 18), textW + 8, 18);
      ctx.fillStyle = "#0b0f14";
      ctx.fillText(label, x + 4, Math.max(12, y - 5));
    }

    // текущий рисуемый прямоугольник (режим добавления находки экспертом)
    if (drawState.current.drawing) {
      const { startX, startY, curX, curY } = drawState.current;
      ctx.strokeStyle = "#38bdf8";
      ctx.lineWidth = 2;
      ctx.setLineDash([4, 4]);
      ctx.strokeRect(Math.min(startX, curX), Math.min(startY, curY), Math.abs(curX - startX), Math.abs(curY - startY));
      ctx.setLineDash([]);
    }
  }, [wc, ww, scale, offset, findings, aspect]);

  useEffect(() => { redraw(); }, [redraw]);

  useEffect(() => {
    const handleResize = () => redraw();
    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, [redraw]);

  function handleWheel(e: React.WheelEvent<HTMLCanvasElement>) {
    e.preventDefault();
    const canvas = canvasRef.current;
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;
    const factor = e.deltaY < 0 ? 1.15 : 1 / 1.15;
    const newScale = Math.min(Math.max(scale * factor, 0.1), 20);
    setOffset((prev) => ({
      x: mx - ((mx - prev.x) / scale) * newScale,
      y: my - ((my - prev.y) / scale) * newScale,
    }));
    setScale(newScale);
  }

  function toCanvasCoords(clientX: number, clientY: number) {
    const canvas = canvasRef.current;
    if (!canvas) return { x: 0, y: 0 };
    const rect = canvas.getBoundingClientRect();
    return { x: clientX - rect.left, y: clientY - rect.top };
  }

  function handleMouseDown(e: React.MouseEvent<HTMLCanvasElement>) {
    if (drawMode) {
      const { x, y } = toCanvasCoords(e.clientX, e.clientY);
      drawState.current = { drawing: true, startX: x, startY: y, curX: x, curY: y };
    } else {
      panState.current = { dragging: true, lastX: e.clientX, lastY: e.clientY };
    }
  }

  function handleMouseMove(e: React.MouseEvent<HTMLCanvasElement>) {
    if (drawMode && drawState.current.drawing) {
      const { x, y } = toCanvasCoords(e.clientX, e.clientY);
      drawState.current.curX = x;
      drawState.current.curY = y;
      redraw();
      return;
    }
    if (panState.current.dragging) {
      const dx = e.clientX - panState.current.lastX;
      const dy = e.clientY - panState.current.lastY;
      panState.current.lastX = e.clientX;
      panState.current.lastY = e.clientY;
      setOffset((prev) => ({ x: prev.x + dx, y: prev.y + dy }));
    }
  }

  function handleMouseUp() {
    if (drawMode && drawState.current.drawing) {
      drawState.current.drawing = false;
      const data = dataRef.current;
      if (data && onRegionDrawn) {
        const x0 = (Math.min(drawState.current.startX, drawState.current.curX) - offset.x) / (data.cols * scale);
        const y0 = (Math.min(drawState.current.startY, drawState.current.curY) - offset.y) / (data.rows * scale * aspect);
        const x1 = (Math.max(drawState.current.startX, drawState.current.curX) - offset.x) / (data.cols * scale);
        const y1 = (Math.max(drawState.current.startY, drawState.current.curY) - offset.y) / (data.rows * scale * aspect);
        if (x1 - x0 > 0.005 && y1 - y0 > 0.005) {
          onRegionDrawn({ x: Math.max(0, x0), y: Math.max(0, y0), w: Math.min(1, x1) - Math.max(0, x0), h: Math.min(1, y1) - Math.max(0, y0) });
        }
      }
      onExitDrawMode?.();
      redraw();
    }
    panState.current.dragging = false;
  }

  function resetView() {
    const data = dataRef.current;
    const container = containerRef.current;
    if (!data || !container) return;
    const fitScale = Math.min((container.clientWidth - 16) / data.cols, (container.clientHeight - 16) / (data.rows * aspect));
    setScale(fitScale > 0 ? fitScale : 1);
    setOffset({
      x: (container.clientWidth - data.cols * fitScale) / 2,
      y: (container.clientHeight - data.rows * aspect * fitScale) / 2,
    });
  }

  return (
    <div className="dicom-viewer">
      <div className="dicom-toolbar">
        <label>
          Центр окна (WC)
          <input type="range" min={dataRef.current ? dataRef.current.minVal : 0}
                 max={dataRef.current ? dataRef.current.maxVal : 100}
                 value={wc} onChange={(e) => setWc(Number(e.target.value))} />
        </label>
        <label>
          Ширина окна (WW)
          <input type="range" min={1}
                 max={dataRef.current ? (dataRef.current.maxVal - dataRef.current.minVal) * 1.5 : 100}
                 value={ww} onChange={(e) => setWw(Number(e.target.value))} />
        </label>
        <button type="button" onClick={resetView}>Вписать снимок</button>
        {drawMode && <span className="draw-hint">Выделите область на снимке зажатой левой кнопкой мыши</span>}
      </div>
      <div className="dicom-canvas-container" ref={containerRef}>
        {loading && <div className="dicom-overlay-msg">Загрузка снимка…</div>}
        {error && <div className="dicom-overlay-msg error">{error}</div>}
        <canvas
          ref={canvasRef}
          onWheel={handleWheel}
          onMouseDown={handleMouseDown}
          onMouseMove={handleMouseMove}
          onMouseUp={handleMouseUp}
          onMouseLeave={handleMouseUp}
          style={{ cursor: drawMode ? "crosshair" : "grab" }}
        />
      </div>
      <div className="viewer-legend">
        <span><i style={{ borderColor: "#d6dee8" }} /> Находка ИИ</span>
        <span><i style={{ borderColor: "#d6dee8", borderTopStyle: "dashed" }} /> Находка эксперта</span>
        <span>Цвет рамки — критичность: жёлтый → красный</span>
        <span>Колесо мыши — масштаб, перетаскивание — сдвиг</span>
      </div>
    </div>
  );
}
