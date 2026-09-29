// Разбор пиксельных данных DICOM для вьюера (без React — проверяется отдельно,
// см. backend/tests/test_viewer_pixels.py).
import dicomParser from "dicom-parser";

export interface DicomPixelData {
  rows: number;
  cols: number;
  pixels: Int32Array; // уже с учётом RescaleSlope/Intercept
  defaultWc: number;
  defaultWw: number;
  minVal: number;
  maxVal: number;
  invert: boolean; // MONOCHROME1: после окна яркость инвертируется (DICOM PS3.3 C.7.6.3.1.2)
}

// Несжатые transfer syntax, которые вьюер умеет читать сам. Сжатые форматы
// (JPEG, JPEG 2000, RLE, deflate) явно отклоняются, а не читаются как сырой массив
// (внешнее ревью, замечание 6).
const TS_IMPLICIT_LE = "1.2.840.10008.1.2";
const TS_EXPLICIT_LE = "1.2.840.10008.1.2.1";
const TS_EXPLICIT_BE = "1.2.840.10008.1.2.2";

export function parseDicomPixels(buf: ArrayBuffer): DicomPixelData {
  const byteArray = new Uint8Array(buf);
  const dataSet = dicomParser.parseDicom(byteArray);

  const ts = (dataSet.string("x00020010") ?? TS_IMPLICIT_LE).split("\0").join("").trim();
  if (![TS_IMPLICIT_LE, TS_EXPLICIT_LE, TS_EXPLICIT_BE].includes(ts)) {
    throw new Error(`Формат пиксельных данных не поддерживается вьюером (transfer syntax ${ts}: сжатие). ` +
                    "ИИ-анализ и отчёт работают — не отображается только снимок.");
  }
  const bigEndian = ts === TS_EXPLICIT_BE;
  const rows = dataSet.uint16("x00280010") ?? 0;
  const cols = dataSet.uint16("x00280011") ?? 0;
  const bitsAllocated = dataSet.uint16("x00280100") ?? 16;
  const pixelRepresentation = dataSet.uint16("x00280103") ?? 0;
  const samples = dataSet.uint16("x00280002") ?? 1;
  const photometric = (dataSet.string("x00280004") ?? "MONOCHROME2").trim().toUpperCase();
  const slope = parseFloat(dataSet.string("x00281053") ?? "1") || 1;
  const intercept = parseFloat(dataSet.string("x00281052") ?? "0") || 0;

  const el = dataSet.elements.x7fe00010;
  if (!el) throw new Error("В файле отсутствует PixelData");
  if (samples !== 1 || !photometric.startsWith("MONOCHROME")) {
    throw new Error(`Цветные снимки (${photometric}) вьюер не отображает`);
  }
  if (bitsAllocated !== 8 && bitsAllocated !== 16) {
    throw new Error(`Разрядность ${bitsAllocated} бит вьюер не поддерживает`);
  }
  const n = rows * cols;
  if (!n || el.length < n * (bitsAllocated / 8)) throw new Error("Размер PixelData не совпадает с Rows × Columns");

  // Порядок байтов — из transfer syntax, а не порядок байтов компьютера
  const raw = new Int32Array(n);
  if (bitsAllocated === 16) {
    const view = new DataView(byteArray.buffer, byteArray.byteOffset + el.dataOffset, n * 2);
    for (let i = 0; i < n; i++) {
      raw[i] = pixelRepresentation === 1 ? view.getInt16(i * 2, !bigEndian) : view.getUint16(i * 2, !bigEndian);
    }
  } else {
    raw.set(new Uint8Array(byteArray.buffer, byteArray.byteOffset + el.dataOffset, n));
  }
  const pixels = raw.map((v) => v * slope + intercept);

  let minVal = Infinity, maxVal = -Infinity;
  for (let i = 0; i < pixels.length; i++) {
    if (pixels[i] < minVal) minVal = pixels[i];
    if (pixels[i] > maxVal) maxVal = pixels[i];
  }

  const wcStr = dataSet.string("x00281050");
  const wwStr = dataSet.string("x00281051");
  const defaultWc = wcStr ? parseFloat(wcStr.split("\\")[0]) : (minVal + maxVal) / 2;
  const defaultWw = wwStr ? parseFloat(wwStr.split("\\")[0]) : Math.max(maxVal - minVal, 1);

  return { rows, cols, pixels, defaultWc, defaultWw, minVal, maxVal, invert: photometric === "MONOCHROME1" };
}

export function renderWindowedImageData(data: DicomPixelData, wc: number, ww: number): ImageData {
  const { rows, cols, pixels } = data;
  const out = new ImageData(cols, rows);
  const w = Math.max(ww, 1);
  const lo = wc - 0.5 - (w - 1) / 2;
  const hi = wc - 0.5 + (w - 1) / 2;
  for (let i = 0; i < pixels.length; i++) {
    const x = pixels[i];
    let v: number;
    if (x <= lo) v = 0;
    else if (x > hi) v = 255;
    else v = ((x - (wc - 0.5)) / (w - 1) + 0.5) * 255;
    if (data.invert) v = 255 - v;
    const o = i * 4;
    out.data[o] = v;
    out.data[o + 1] = v;
    out.data[o + 2] = v;
    out.data[o + 3] = 255;
  }
  return out;
}

