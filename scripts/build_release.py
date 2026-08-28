"""Build and package a native standalone release for the current platform."""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import stat
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BUILD_DIR = ROOT / "build" / "native-release"
DEFAULT_RELEASE_DIR = ROOT / "release"
APP_NAME = "SZU-Course-Help"


def project_version() -> str:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        return str(tomllib.load(handle)["project"]["version"])


def platform_name() -> tuple[str, str]:
    system = platform.system().lower()
    machine = platform.machine().lower()
    is_arm64 = machine in {"arm64", "aarch64"}
    is_x64 = machine in {"amd64", "x86_64"}

    if system == "windows" and is_x64:
        return "windows-x64", f"{APP_NAME}.exe"
    if system == "darwin" and is_arm64:
        return "macos-arm64", APP_NAME
    if system == "darwin" and is_x64:
        return "macos-x64", APP_NAME
    if system == "linux" and is_x64:
        return "linux-x64", APP_NAME
    raise RuntimeError(f"Unsupported release platform: {system}/{machine}")


def run_nuitka(build_dir: Path, executable_name: str) -> Path:
    if build_dir.exists():
        shutil.rmtree(build_dir)
    build_dir.mkdir(parents=True)

    command = [
        sys.executable,
        "-m",
        "nuitka",
        "--standalone",
        f"--output-dir={build_dir}",
        f"--output-filename={executable_name}",
        "--include-data-dir=static_dist=static_dist",
        "--nofollow-import-to=paddleocr",
        "--nofollow-import-to=paddle",
        "--nofollow-import-to=paddlepaddle",
        "--nofollow-import-to=torch",
        "--nofollow-import-to=torchvision",
        "--nofollow-import-to=skimage",
        "--nofollow-import-to=scipy",
        "--nofollow-import-to=pandas",
        "--include-package=Crypto",
        "--include-package=cv2",
        "--include-package=ddddocr",
        "--include-package-data=ddddocr",
        "--include-package=onnxruntime",
        "--no-deployment-flag=excluded-module-usage",
        "--enable-plugin=no-qt",
        "--assume-yes-for-downloads",
    ]
    if os.name == "nt":
        command.append("--windows-console-mode=force")
    command.append(str(ROOT / "main.py"))

    print("Building native standalone application...")
    subprocess.run(command, cwd=ROOT, check=True)
    distributions = sorted(build_dir.glob("*.dist"))
    if len(distributions) != 1:
        names = ", ".join(path.name for path in distributions) or "none"
        raise RuntimeError(f"Expected one Nuitka distribution, found: {names}")
    return distributions[0]


def write_launcher(stage_dir: Path, platform_id: str, executable_name: str) -> None:
    if platform_id == "windows-x64":
        launcher = stage_dir / "启动抢课助手.bat"
        launcher.write_text(
            "@echo off\r\n"
            "setlocal\r\n"
            "chcp 65001 >nul\r\n"
            'cd /d "%~dp0"\r\n'
            f'"{executable_name}"\r\n'
            'set "exit_code=%ERRORLEVEL%"\r\n'
            'if not "%exit_code%"=="0" (\r\n'
            "  echo.\r\n"
            "  echo 程序异常退出，请保留本窗口并查看上方错误。\r\n"
            "  pause\r\n"
            ")\r\n"
            "exit /b %exit_code%\r\n",
            encoding="utf-8-sig",
            newline="",
        )
        return

    extension = "command" if platform_id.startswith("macos-") else "sh"
    launcher = stage_dir / f"启动抢课助手.{extension}"
    launcher.write_text(
        "#!/bin/bash\n"
        'cd -- "$(dirname -- "$0")" || exit 1\n'
        f'chmod +x "./{executable_name}"\n'
        f'"./{executable_name}"\n'
        "status=$?\n"
        'if [ "$status" -ne 0 ]; then\n'
        '  printf "\\n程序异常退出，请查看上方错误。按回车键关闭..."\n'
        "  read -r _\n"
        "fi\n"
        'exit "$status"\n',
        encoding="utf-8",
        newline="\n",
    )
    launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def add_zip_file(archive: zipfile.ZipFile, path: Path, arcname: str) -> None:
    info = zipfile.ZipInfo.from_file(path, arcname=arcname)
    info.compress_type = zipfile.ZIP_DEFLATED
    with path.open("rb") as handle:
        archive.writestr(info, handle.read(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def create_archive(stage_dir: Path, release_dir: Path) -> Path:
    archive_path = release_dir / f"{stage_dir.name}.zip"
    if archive_path.exists():
        archive_path.unlink()
    with zipfile.ZipFile(archive_path, "w", allowZip64=True) as archive:
        for path in sorted(stage_dir.rglob("*")):
            if path.is_file():
                arcname = (Path(stage_dir.name) / path.relative_to(stage_dir)).as_posix()
                add_zip_file(archive, path, arcname)
    return archive_path


def package_distribution(
    distribution: Path,
    release_dir: Path,
    version: str,
    platform_id: str,
    executable_name: str,
) -> Path:
    package_name = f"{APP_NAME}-v{version}-{platform_id}"
    stage_dir = release_dir / package_name
    if stage_dir.exists():
        shutil.rmtree(stage_dir)
    shutil.copytree(distribution, stage_dir)

    required_files = {
        ROOT / "README.md": stage_dir / "README.md",
        ROOT / "CHANGELOG.md": stage_dir / "更新记录.md",
        ROOT / "LICENSE": stage_dir / "LICENSE",
        ROOT / "docs" / "USER_GUIDE.md": stage_dir / "使用手册.md",
        ROOT / "output" / "pdf" / "SZU-Course-Help-User-Guide.pdf": stage_dir / "使用手册.pdf",
    }
    for source, destination in required_files.items():
        if not source.is_file():
            raise FileNotFoundError(f"Required release document is missing: {source}")
        shutil.copy2(source, destination)

    executable = stage_dir / executable_name
    if not executable.is_file():
        raise FileNotFoundError(f"Nuitka executable is missing: {executable}")
    if os.name != "nt":
        executable.chmod(executable.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    write_launcher(stage_dir, platform_id, executable_name)
    return create_archive(stage_dir, release_dir)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default=project_version())
    parser.add_argument("--build-dir", type=Path, default=DEFAULT_BUILD_DIR)
    parser.add_argument("--release-dir", type=Path, default=DEFAULT_RELEASE_DIR)
    parser.add_argument(
        "--dist-dir",
        type=Path,
        help="Package an existing Nuitka .dist directory instead of compiling again.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    platform_id, executable_name = platform_name()
    build_dir = args.build_dir.resolve()
    release_dir = args.release_dir.resolve()
    release_dir.mkdir(parents=True, exist_ok=True)
    distribution = (
        args.dist_dir.resolve() if args.dist_dir else run_nuitka(build_dir, executable_name)
    )
    archive = package_distribution(
        distribution,
        release_dir,
        args.version,
        platform_id,
        executable_name,
    )
    print(f"Release archive: {archive}")


if __name__ == "__main__":
    main()
