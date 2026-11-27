"""ASB's sabotaged twins restored to the state ASB evaluated: the `<id>_corrected` samples.

ASB applied its honest codebases' incidental fixes to six sabotaged twins after it had
evaluated them, and in lazy_alpha and chain_justification the fix overwrote part of the
sabotage. corrections/ holds the restored files (written by scripts/make_corrections.py;
its README gives the evidence). A corrected sample is its base sample with the codebase
moved to a full copy: ASB's variant with those files overlaid, built once under
~/.cache/debate-asb/built/<manifest digest>/ (DEBATE_ASB_BUILT_ROOT overrides). The build
checks every hash the manifest records, so a different ASB checkout fails loudly instead
of building something else. Label and answer key stay the base's, and no path or id a
participant can see names the correction.
"""

import hashlib
import json
import os
import shutil
from pathlib import Path

from inspect_ai.dataset import Sample

from debate_asb.datasets.asb import ASB_ROOT, PROJECT_ROOT, asb_samples

CORRECTIONS = PROJECT_ROOT / "corrections"
MANIFEST = CORRECTIONS / "manifest.json"


def read_manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def built_root() -> Path:
    """Where corrected copies are built: one folder per version of the manifest."""
    digest = hashlib.sha256(MANIFEST.read_bytes()).hexdigest()[:12]
    base = os.environ.get("DEBATE_ASB_BUILT_ROOT") or (
        Path.home() / ".cache" / "debate-asb" / "built"
    )
    return Path(base) / digest


def corrected_samples(sample_ids: list[str]) -> list[Sample]:
    """Samples for `<id>_corrected` ids, in the order given."""
    entries = {e["id"]: e for e in read_manifest()["samples"]}
    unknown = [i for i in sample_ids if i not in entries]
    if unknown:
        raise ValueError(f"No correction for: {unknown}")
    bases = {
        str(s.id): s for s in asb_samples([entries[i]["base"] for i in sample_ids])
    }
    return [_moved(i, bases[entries[i]["base"]], built_codebase(entries[i])) for i in sample_ids]  # fmt: skip


def _moved(sample_id: str, base: Sample, codebase: Path) -> Sample:
    assert base.metadata is not None
    artifacts = base.metadata["artifacts"]
    return base.model_copy(update={
        "id": sample_id,
        "metadata": {
            **base.metadata,
            "artifacts": {**artifacts, "codebase": {"root": str(codebase)}},
        },
    })  # fmt: skip


def built_codebase(entry: dict) -> Path:
    """The full corrected copy of one twin, built and checked on first use."""
    target = built_root() / entry["codebase"]
    if target.is_dir():  # only ever created whole, by the rename below
        return target
    source = ASB_ROOT / "codebases" / entry["codebase"]
    partial = built_root() / "partial" / f"{entry['id']}.{os.getpid()}"
    shutil.rmtree(partial, ignore_errors=True)
    shutil.copytree(source, partial)
    if (found := tree_sha256(partial)) != entry["shipped_tree_sha256"]:
        shutil.rmtree(partial, ignore_errors=True)
        raise ValueError(
            f"{entry['id']}: data/asb/codebases/{entry['codebase']} isn't the copy the "
            f"correction was made against (tree sha256 {found}, expected "
            f"{entry['shipped_tree_sha256']}; ASB commit {read_manifest()['asb_commit']})"
        )
    for f in entry["files"]:
        dest = partial / f["path"]
        shutil.copyfile(CORRECTIONS / entry["codebase"] / f["path"], dest)
        if file_sha256(dest) != f["corrected_sha256"]:
            shutil.rmtree(partial, ignore_errors=True)
            raise ValueError(
                f"{entry['id']}: corrections/{entry['codebase']}/{f['path']} doesn't match "
                "manifest.json (were its line endings converted on checkout?)"
            )
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        partial.rename(target)
    except OSError:  # another process finished the same build first
        shutil.rmtree(partial, ignore_errors=True)
        if not target.is_dir():
            raise
    return target


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_sha256(root: Path) -> str:
    """sha256 over every file's relative path and sha256, in path order (the same on any OS)."""
    files = {p.relative_to(root).as_posix(): p for p in root.rglob("*") if p.is_file()}
    digest = hashlib.sha256()
    for name in sorted(files):
        digest.update(f"{name}\0{file_sha256(files[name])}\n".encode())
    return digest.hexdigest()
