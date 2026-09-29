"""Имя загружаемого файла не должно выводить запись за пределы папки исследования."""
import pytest

from app.storage import safe_upload_name


@pytest.mark.parametrize("raw, expected", [
    ("study.dcm", "study.dcm"),
    ("CR000000_ПОП.dcm", "CR000000_ПОП.dcm"),
    ("../../evil.dcm", "evil.dcm"),
    ("..\\..\\evil.dcm", "evil.dcm"),
    ("C:\\Windows\\evil.dcm", "evil.dcm"),
    ("/etc/passwd", "passwd"),
    ("dir/", "upload.bin"),
    ("..", "upload.bin"),
    ("", "upload.bin"),
    (None, "upload.bin"),
])
def test_safe_upload_name(raw, expected):
    assert safe_upload_name(raw) == expected
