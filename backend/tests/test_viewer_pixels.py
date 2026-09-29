"""Разбор пикселей во вьюере (frontend/src/components/dicomPixels.ts) — на
настоящих DICOM-файлах, как в внешнем ревью 2026-09-29 (замечания 6 и 7).
Модуль TypeScript транспилируется компилятором из frontend/node_modules и
выполняется в Node.js; без Node или node_modules тест пропускается."""
import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import ExplicitVRBigEndian, ExplicitVRLittleEndian, JPEGBaseline8Bit, generate_uid

FRONT = Path(__file__).resolve().parents[2] / "frontend"
MODULE = FRONT / "src" / "components" / "dicomPixels.ts"
TS = FRONT / "node_modules" / "typescript"
DP = FRONT / "node_modules" / "dicom-parser"
PIXELS = [1, 256, 1000, 4095]

RUNNER = r"""
const fs = require("fs");
const [tsPath, dpPath, modPath, ...files] = process.argv.slice(2);
const ts = require(tsPath);
const js = ts.transpileModule(fs.readFileSync(modPath, "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, esModuleInterop: true },
}).outputText;
const mod = { exports: {} };
new Function("require", "module", "exports", js)((p) => (p === "dicom-parser" ? require(dpPath) : require(p)), mod, mod.exports);
const out = {};
for (const f of files) {
  const b = fs.readFileSync(f);
  try {
    const d = mod.exports.parseDicomPixels(b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength));
    out[f] = { pixels: Array.from(d.pixels), invert: d.invert };
  } catch (e) {
    out[f] = { error: String(e.message || e) };
  }
}
console.log(JSON.stringify(out));
"""

pytestmark = pytest.mark.skipif(
    not (shutil.which("node") and TS.exists() and DP.exists()), reason="нет Node.js или frontend/node_modules")


def _dicom(path: Path, syntax, photometric="MONOCHROME2") -> Path:
    ds = Dataset()
    ds.SOPClassUID = "1.2.840.10008.5.1.4.1.1.1.1"
    ds.SOPInstanceUID = generate_uid()
    ds.Rows, ds.Columns = 2, 2
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = photometric
    ds.BitsAllocated, ds.BitsStored, ds.HighBit, ds.PixelRepresentation = 16, 12, 11, 0
    big = syntax == ExplicitVRBigEndian
    ds.PixelData = np.array(PIXELS, dtype=">u2" if big else "<u2").tobytes()
    if syntax == JPEGBaseline8Bit:                 # сжатый формат: инкапсулированные фрагменты
        from pydicom.encaps import encapsulate
        ds.PixelData = encapsulate([b"\xff\xd8 fake jpeg \xff\xd9"])
        ds["PixelData"].VR = "OB"
        ds["PixelData"].is_undefined_length = True
    meta = FileMetaDataset()
    meta.MediaStorageSOPClassUID = ds.SOPClassUID
    meta.MediaStorageSOPInstanceUID = ds.SOPInstanceUID
    meta.TransferSyntaxUID = syntax
    ds.file_meta = meta
    ds.is_little_endian = not big
    ds.is_implicit_VR = False
    ds.preamble = b"\x00" * 128
    ds.save_as(str(path), write_like_original=False)
    return path


def test_viewer_pixel_decoding(tmp_path):
    files = {
        "le": _dicom(tmp_path / "le.dcm", ExplicitVRLittleEndian),
        "be": _dicom(tmp_path / "be.dcm", ExplicitVRBigEndian),
        "mono1": _dicom(tmp_path / "mono1.dcm", ExplicitVRLittleEndian, "MONOCHROME1"),
        "jpeg": _dicom(tmp_path / "jpeg.dcm", JPEGBaseline8Bit),
    }
    runner = tmp_path / "run.js"
    runner.write_text(RUNNER, encoding="utf-8")
    res = subprocess.run(["node", str(runner), str(TS), str(DP), str(MODULE), *map(str, files.values())],
                         capture_output=True, text=True, encoding="utf-8", timeout=120, check=True)
    out = {k: json.loads(res.stdout)[str(v)] for k, v in files.items()}

    assert out["le"]["pixels"] == PIXELS
    assert out["be"]["pixels"] == PIXELS                    # раньше: [256, 1, 59395, 65295]
    assert out["mono1"]["invert"] is True and out["le"]["invert"] is False
    assert "не поддерживается" in out["jpeg"]["error"]      # сжатие — явный отказ, а не мусор
