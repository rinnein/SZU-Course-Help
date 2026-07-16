"""Create a clean source archive from Git-tracked and intentional new files."""

from __future__ import annotations

import argparse
import hashlib
import subprocess
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RELEASE_DIR = ROOT / "release"


def project_version() -> str:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        return str(tomllib.load(handle)["project"]["version"])


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def source_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    paths = [ROOT / item.decode("utf-8") for item in result.stdout.split(b"\0") if item]
    return sorted(path for path in paths if path.is_file())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default=project_version())
    parser.add_argument("--release-dir", type=Path, default=DEFAULT_RELEASE_DIR)
    args = parser.parse_args()

    release_dir = args.release_dir.resolve()
    release_dir.mkdir(parents=True, exist_ok=True)
    root_name = f"SZU-Course-Help-v{args.version}-source"
    archive_path = release_dir / f"{root_name}.zip"
    if archive_path.exists():
        archive_path.unlink()

    files = source_files()
    with zipfile.ZipFile(
        archive_path,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
        allowZip64=True,
    ) as archive:
        for path in files:
            archive.write(path, (Path(root_name) / path.relative_to(ROOT)).as_posix())

    checksum = sha256(archive_path)
    archive_path.with_suffix(".zip.sha256").write_text(
        f"{checksum}  {archive_path.name}\n",
        encoding="ascii",
        newline="\n",
    )
    print(f"Source archive: {archive_path}")
    print(f"Files: {len(files)}")
    print(f"SHA256: {checksum}")


if __name__ == "__main__":
    main()
