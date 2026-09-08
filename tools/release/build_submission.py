"""Build a source-only SmartFarm submission without local development inputs."""

from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "deliverables/source/smartfarm-source.zip"
ROOTS = ("implementation", "tests", "evidence/curated/platform", "evidence/curated/hil", "tools/release")
EXCLUDED = {".pio", "__pycache__", ".runtime", "import_ready", "logs", ".pytest_cache", "final", "audits", "planning", "report"}
SUFFIXES = {".py", ".md", ".json", ".js", ".cpp", ".h", ".ini", ".txt", ".service", ".example", ".png"}


def source_files():
    yield ROOT / "README.md"
    yield ROOT / ".gitignore"
    yield ROOT / ".gitattributes"
    yield ROOT / "evidence/README.md"
    yield from sorted((ROOT / "docs").glob("*.md"))
    for name in ROOTS:
        for path in sorted((ROOT / name).rglob("*")):
            if path.is_symlink() or not path.is_file():
                continue
            if any(part in EXCLUDED for part in path.relative_to(ROOT).parts):
                continue
            if path.suffix not in SUFFIXES or path.name == ".env":
                continue
            yield path


def main():
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    files = list(source_files())
    for path in files:
        if "simulator" in path.name or "gateway_sim" in path.name:
            raise ValueError(f"development source in product package: {path}")
    with zipfile.ZipFile(OUTPUT, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, path.relative_to(ROOT).as_posix())
    print(
        f"{OUTPUT}: {len(files)} source files; local_dev and generated state excluded"
    )


if __name__ == "__main__":
    main()
