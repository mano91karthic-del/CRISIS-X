import zipfile
from pathlib import Path

import pytest

from app.services.archive_safety import ArchiveSecurityError, safe_extract_zip


def _make_zip(path: Path, entries: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return path


def test_extracts_a_well_formed_archive(tmp_path: Path) -> None:
    zip_path = _make_zip(
        tmp_path / "good.zip",
        {"manifest.json": b"{}", "assets/dem.tif": b"fake-tif-bytes"},
    )
    dest = tmp_path / "out"
    dest.mkdir()

    extracted = safe_extract_zip(zip_path, dest)

    names = sorted(p.relative_to(dest).as_posix() for p in extracted)
    assert names == ["assets/dem.tif", "manifest.json"]
    assert (dest / "manifest.json").read_bytes() == b"{}"
    assert (dest / "assets" / "dem.tif").read_bytes() == b"fake-tif-bytes"


def test_rejects_not_a_zip_file(tmp_path: Path) -> None:
    fake = tmp_path / "not_a_zip.zip"
    fake.write_bytes(b"this is not a zip archive")
    dest = tmp_path / "out"
    dest.mkdir()

    with pytest.raises(ArchiveSecurityError):
        safe_extract_zip(fake, dest)


@pytest.mark.parametrize(
    "evil_name",
    [
        "../escape.txt",
        "../../etc/passwd",
        "a/../../escape.txt",
        "/etc/passwd",
        "\\windows\\system32\\evil.dll",
        "C:\\evil.txt",
        "C:/evil.txt",
    ],
)
def test_rejects_path_traversal_and_absolute_paths(tmp_path: Path, evil_name: str) -> None:
    zip_path = _make_zip(tmp_path / "evil.zip", {evil_name: b"payload"})
    dest = tmp_path / "out"
    dest.mkdir()

    with pytest.raises(ArchiveSecurityError):
        safe_extract_zip(zip_path, dest)

    # Nothing should have been written outside (or inside) the staging dir.
    assert list(dest.iterdir()) == []


def test_rejects_too_many_entries(tmp_path: Path) -> None:
    entries = {f"file_{i}.txt": b"x" for i in range(10)}
    zip_path = _make_zip(tmp_path / "many.zip", entries)
    dest = tmp_path / "out"
    dest.mkdir()

    with pytest.raises(ArchiveSecurityError):
        safe_extract_zip(zip_path, dest, max_entries=5)


def test_rejects_single_file_over_uncompressed_limit(tmp_path: Path) -> None:
    big_content = b"x" * (2 * 1024 * 1024)  # 2 MB
    zip_path = _make_zip(tmp_path / "big.zip", {"huge.bin": big_content})
    dest = tmp_path / "out"
    dest.mkdir()

    with pytest.raises(ArchiveSecurityError):
        safe_extract_zip(zip_path, dest, max_single_uncompressed_bytes=1024 * 1024)


def test_rejects_total_over_uncompressed_limit(tmp_path: Path) -> None:
    chunk = b"x" * (600 * 1024)  # 600 KB each
    zip_path = _make_zip(tmp_path / "totals.zip", {"a.bin": chunk, "b.bin": chunk, "c.bin": chunk})
    dest = tmp_path / "out"
    dest.mkdir()

    with pytest.raises(ArchiveSecurityError):
        safe_extract_zip(zip_path, dest, max_total_uncompressed_bytes=1024 * 1024)


def test_rejects_archive_over_size_limit(tmp_path: Path) -> None:
    zip_path = _make_zip(tmp_path / "small.zip", {"a.txt": b"hello world"})
    dest = tmp_path / "out"
    dest.mkdir()

    with pytest.raises(ArchiveSecurityError):
        safe_extract_zip(zip_path, dest, max_archive_bytes=10)


def test_directory_entries_are_skipped_not_rejected(tmp_path: Path) -> None:
    zip_path = tmp_path / "with_dirs.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("assets/", b"")  # explicit directory entry
        zf.writestr("assets/file.txt", b"content")
    dest = tmp_path / "out"
    dest.mkdir()

    extracted = safe_extract_zip(zip_path, dest)

    assert len(extracted) == 1
    assert extracted[0] == (dest / "assets" / "file.txt").resolve()
