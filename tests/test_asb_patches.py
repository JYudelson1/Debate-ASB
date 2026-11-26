"""Loader isolation and steering-vector cache regressions; no model execution."""

import ast
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest

from debate_asb.datasets import asb


@pytest.fixture
def local_overlay(tmp_path, monkeypatch):
    original = tmp_path / "asb/codebases/awareness/prism"
    original.mkdir(parents=True)
    source = original / "run_experiments.py"
    source.write_text("# original experiment\n")
    (original / "PAPER.md").write_text("unchanged paper\n")
    overlay = tmp_path / "run_experiments.py.txt"
    overlay.write_text("# corrected experiment\n")
    monkeypatch.setattr(asb, "ASB_ROOT", tmp_path / "asb")
    monkeypatch.setattr(asb, "PATCHED_ROOT", tmp_path / "patched")
    monkeypatch.setattr(asb, "PATCHES", {})
    monkeypatch.setattr(
        asb, "LOCAL_PATCHES", {"awareness_prism": {"run_experiments.py": overlay}}
    )
    monkeypatch.setattr(
        asb,
        "LOCAL_PATCH_SOURCE_HASHES",
        {
            "awareness_prism": {
                "run_experiments.py": hashlib.sha256(source.read_bytes()).hexdigest()
            }
        },
    )
    return original, overlay


def test_loader_overlays_only_awareness_prism_and_preserves_original(local_overlay):
    original, overlay = local_overlay
    fixed = asb._workspace("awareness_prism", "codebases/awareness/prism")
    assert fixed != original
    assert (fixed / "run_experiments.py").read_bytes() == overlay.read_bytes()
    assert (original / "run_experiments.py").read_text() == "# original experiment\n"
    assert (fixed / "PAPER.md").read_bytes() == (original / "PAPER.md").read_bytes()
    assert asb._workspace("awareness_lens", "codebases/awareness/lens") == (
        asb.ASB_ROOT / "codebases/awareness/lens"
    )


def test_loader_reuses_copy_and_rebuilds_when_overlay_changes(local_overlay):
    _, overlay = local_overlay
    fixed = asb._workspace("awareness_prism", "codebases/awareness/prism")
    sentinel = fixed / "cache-reused"
    sentinel.write_text("preserved until rebuild")
    assert asb._workspace("awareness_prism", "codebases/awareness/prism") == fixed
    assert sentinel.exists()
    overlay.write_text("# corrected experiment v2\n")
    assert asb._workspace("awareness_prism", "codebases/awareness/prism") == fixed
    assert not sentinel.exists()
    assert (fixed / "run_experiments.py").read_bytes() == overlay.read_bytes()


def test_loader_rejects_source_drift_even_with_cached_copy(local_overlay):
    original, _ = local_overlay
    asb._workspace("awareness_prism", "codebases/awareness/prism")
    (original / "run_experiments.py").write_text(
        "# different upstream implementation\n"
    )
    with pytest.raises(ValueError, match="awareness_prism.*run_experiments.py"):
        asb._workspace("awareness_prism", "codebases/awareness/prism")


def test_asb_origin_overlays_still_work(tmp_path, monkeypatch):
    original = tmp_path / "asb/original"
    original.mkdir(parents=True)
    (original / "prompt.txt").write_text("old prompt")
    (tmp_path / "asb/corrected.txt").write_text("corrected prompt")
    monkeypatch.setattr(asb, "ASB_ROOT", tmp_path / "asb")
    monkeypatch.setattr(asb, "PATCHED_ROOT", tmp_path / "patched")
    monkeypatch.setattr(asb, "PATCHES", {"example": {"prompt.txt": "corrected.txt"}})
    fixed = asb._workspace("example", "original")
    assert (fixed / "prompt.txt").read_text() == "corrected prompt"
    assert (original / "prompt.txt").read_text() == "old prompt"


@pytest.fixture
def steering_script(tmp_path, monkeypatch):
    source = asb.LOCAL_PATCHES["awareness_prism"]["run_experiments.py"].read_text()
    # Compile all of the shipped overlay, then execute only its isolated functions.
    compile(source, "<awareness overlay>", "exec")
    tree = ast.parse(source)
    functions = {
        node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)
    }
    selected = [
        functions[name]
        for name in (
            "_validated_vector_bytes",
            "_atomic_write_bytes",
            "generate_steering_vector",
            "main",
        )
    ]
    for function in selected:
        function.decorator_list = []
    namespace: dict[str, Any] = {
        "Path": Path,
        "os": os,
        "tempfile": tempfile,
        "json": json,
        "__file__": str(tmp_path / "run_experiments.py"),
        "hf_cache": SimpleNamespace(commit=Mock()),
        "STEERING_ADAPTER_CONFIG": ":deployment_steering",
        "STEERING_PROMPTS_FILE": "prompts.json",
    }
    exec(
        compile(
            ast.Module(body=[*selected], type_ignores=[]),
            "<steering functions>",
            "exec",
        ),
        namespace,
    )
    extract_paths = []

    def extract(_formatted, _model, *, token_position, save_path):
        assert token_position == -2
        path = Path(save_path)
        assert not path.exists(), "The extractor would reuse an existing output path"
        extract_paths.append(path)
        path.write_bytes(b"fresh-vector")

    def save_config(config, output):
        output.with_name(f"{output.stem}_cfg.json").write_text(json.dumps(config))

    helpers = SimpleNamespace(
        parse_adapter_config=lambda _value: ("", "deployment_steering"),
        load_prompts=lambda _value: ({"real": ["a"], "fake": ["b"]}, "json"),
        setup_model_and_tokenizer=lambda *_args: ("model", "tokenizer"),
        create_language_model=lambda *_args: "language-model",
        create_config_dict=lambda **kwargs: kwargs,
        save_config_file=save_config,
    )
    core = SimpleNamespace(
        extract_difference_vectors=extract,
        process_contrastive_prompts_simple=lambda *_args, **_kwargs: "formatted",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "path", sys.path.copy())
    monkeypatch.setitem(sys.modules, "os", SimpleNamespace(chdir=lambda _path: None))
    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(set_grad_enabled=lambda _value: None, bfloat16="bfloat16"),
    )
    monkeypatch.setitem(sys.modules, "generate_steering_vectors", helpers)
    monkeypatch.setitem(sys.modules, "steer_core", core)
    vector = tmp_path / "data/steering_vectors/deployment_steering.pt"
    vector.parent.mkdir(parents=True)
    vector.write_bytes(b"bundled-vector")
    return namespace, functions, vector, extract_paths, core


def test_default_reuses_cache_but_forced_regeneration_extracts_fresh(steering_script):
    namespace, _, vector, paths, _ = steering_script
    generate = namespace["generate_steering_vector"]
    assert generate(force=False) == b"bundled-vector"
    assert paths == []
    assert generate(force=True) == b"fresh-vector"
    assert vector.read_bytes() == b"fresh-vector"
    assert len(paths) == 1
    assert not paths[0].parent.exists(), "The extraction temporary directory leaked"


def test_failed_regeneration_preserves_existing_vector(steering_script):
    namespace, _, vector, _, core = steering_script
    core.extract_difference_vectors = Mock(
        side_effect=RuntimeError("extraction failed")
    )
    with pytest.raises(RuntimeError, match="extraction failed"):
        namespace["generate_steering_vector"](force=True)
    assert vector.read_bytes() == b"bundled-vector"
    assert list(vector.parent.iterdir()) == [vector]


def test_shard_replaces_existing_vector_and_rejects_empty_bytes(steering_script):
    namespace, functions, vector, _, _ = steering_script
    body = functions["run_condition_shard"].body
    start = next(
        i
        for i, node in enumerate(body)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "sv_path"
            for target in node.targets
        )
    )
    namespace["steering_vector_bytes"] = b"coordinator-vector"
    exec(
        compile(
            ast.Module(body=body[start : start + 2], type_ignores=[]),
            "<actual shard installation>",
            "exec",
        ),
        namespace,
    )
    assert vector.read_bytes() == b"coordinator-vector"
    with pytest.raises(ValueError, match="non-empty bytes"):
        namespace["_atomic_write_bytes"](vector, b"")
    assert vector.read_bytes() == b"coordinator-vector"
    assert not list(vector.parent.glob(".*.tmp"))


@pytest.mark.parametrize("regenerate", [False, True])
def test_coordinator_forwards_regeneration_flag(steering_script, regenerate):
    namespace, _, vector, _, _ = steering_script
    if not regenerate:
        vector.unlink()
    remote = Mock(return_value=b"remote-vector")
    namespace["generate_steering_vector"] = SimpleNamespace(remote=remote)
    config = vector.parents[2] / "steering_experiment_configs/experiment.jsonl"
    config.parent.mkdir()
    config.write_text("")  # No shards or model execution are needed for this check.
    namespace["main"](regenerate_steering_vector=regenerate)
    remote.assert_called_once_with(force=regenerate)
    assert vector.read_bytes() == b"remote-vector"
