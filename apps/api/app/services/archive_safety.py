"""Safe ZIP extraction — path traversal, absolute-path, and zip-bomb
protection. Reusable for any future archive-based import, not just TERRAIN-X
packages.

Every entry is validated before anything is extracted (fail closed on a
malicious archive rather than partially extracting it), and size limits are
enforced against actual decompressed bytes as they're read — not just the
archive's own (attacker-controlled) declared metadata.
"""

import zipfile
from pathlib import Path, PureWindowsPath

DEFAULT_MAX_ARCHIVE_BYTES = 200 * 1024 * 1024  # 200 MB, compressed, on disk
DEFAULT_MAX_ENTRIES = 200
DEFAULT_MAX_SINGLE_UNCOMPRESSED_BYTES = 300 * 1024 * 1024  # 300 MB
DEFAULT_MAX_TOTAL_UNCOMPRESSED_BYTES = 750 * 1024 * 1024  # 750 MB

_READ_CHUNK_SIZE = 1024 * 1024


class ArchiveSecurityError(ValueError):
    """Raised for any archive that fails safety validation. The archive is
    rejected outright — callers must not partially trust it."""


def _is_unsafe_member_path(name: str) -> bool:
    if not name:
        return True
    if name.startswith("/") or name.startswith("\\"):
        return True
    if PureWindowsPath(name).drive:
        return True
    normalized = name.replace("\\", "/")
    parts = [p for p in normalized.split("/") if p not in ("", ".")]
    if not parts or ".." in parts:
        return True
    return False


def safe_extract_zip(
    zip_path: Path,
    dest_dir: Path,
    *,
    max_archive_bytes: int = DEFAULT_MAX_ARCHIVE_BYTES,
    max_entries: int = DEFAULT_MAX_ENTRIES,
    max_single_uncompressed_bytes: int = DEFAULT_MAX_SINGLE_UNCOMPRESSED_BYTES,
    max_total_uncompressed_bytes: int = DEFAULT_MAX_TOTAL_UNCOMPRESSED_BYTES,
) -> list[Path]:
    """Validates and extracts every file entry in `zip_path` into `dest_dir`.

    Returns the list of extracted file paths (directories excluded). Raises
    ArchiveSecurityError, and extracts nothing, if any check fails.
    """
    dest_dir = dest_dir.resolve()

    archive_size = zip_path.stat().st_size
    if archive_size > max_archive_bytes:
        raise ArchiveSecurityError(
            f"Archive is too large ({archive_size} > {max_archive_bytes} bytes)."
        )

    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as exc:
        raise ArchiveSecurityError(f"Not a valid ZIP archive: {exc}") from exc

    with zf:
        infos = [info for info in zf.infolist() if not info.is_dir()]

        if len(infos) > max_entries:
            raise ArchiveSecurityError(f"Archive has too many entries ({len(infos)} > {max_entries}).")

        declared_total = 0
        resolved_targets: list[tuple[zipfile.ZipInfo, Path]] = []

        for info in infos:
            if _is_unsafe_member_path(info.filename):
                raise ArchiveSecurityError(f"Unsafe path in archive: {info.filename!r}")

            if info.file_size > max_single_uncompressed_bytes:
                raise ArchiveSecurityError(
                    f"Archive member '{info.filename}' is too large uncompressed "
                    f"({info.file_size} > {max_single_uncompressed_bytes} bytes)."
                )
            declared_total += info.file_size
            if declared_total > max_total_uncompressed_bytes:
                raise ArchiveSecurityError(
                    f"Archive's total declared uncompressed size exceeds the limit "
                    f"({max_total_uncompressed_bytes} bytes)."
                )

            target = (dest_dir / info.filename).resolve()
            if not target.is_relative_to(dest_dir):
                raise ArchiveSecurityError(
                    f"Archive member resolves outside the staging directory: {info.filename!r}"
                )

            resolved_targets.append((info, target))

        # Extract only after every entry has passed validation.
        extracted: list[Path] = []
        running_total = 0
        for info, target in resolved_targets:
            target.parent.mkdir(parents=True, exist_ok=True)
            written = 0
            with zf.open(info) as src, target.open("wb") as out:
                while chunk := src.read(_READ_CHUNK_SIZE):
                    written += len(chunk)
                    running_total += len(chunk)
                    # Checked against actual decompressed bytes as they're
                    # read, not just the archive's own declared file_size —
                    # a crafted archive could understate that.
                    if written > max_single_uncompressed_bytes:
                        raise ArchiveSecurityError(
                            f"Archive member '{info.filename}' exceeded the uncompressed "
                            "size limit during extraction."
                        )
                    if running_total > max_total_uncompressed_bytes:
                        raise ArchiveSecurityError(
                            "Archive's total uncompressed size exceeded the limit during extraction."
                        )
                    out.write(chunk)
            extracted.append(target)

        return extracted
