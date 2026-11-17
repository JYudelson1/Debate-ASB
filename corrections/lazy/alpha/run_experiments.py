#!/usr/bin/env python3
"""Run lazy training experiments in parallel on Modal with GPU.

Batches ~10 experiments per GPU container to balance parallelism with overhead.
225 experiments -> ~23 containers.

Usage:
    modal run run_experiments.py
    modal run run_experiments.py --batch-size 15
"""

import json
import math
import time
from pathlib import Path

import modal

app = modal.App("lazy-experiments")

lazy_image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install("torch", "torchvision", "numpy", "scipy")
    .add_local_dir(
        str(Path(__file__).parent),
        remote_path="/workspace",
        copy=True,
        ignore=["__pycache__", "*.egg-info", ".venv", "runs", "figures"],
    )
)

EXPERIMENT_ARGS_TEMPLATE = {
    'dtype': 'float64',
    'seed_testset': 0,
    'seed_kernelset': 0,
    'seed_trainset': 0,
    'dataset': 'fashion',
    'ptr': 10000,
    'ptk': 0,
    'pte': 50000,
    'd': None,
    'data_param1': None,
    'data_param2': None,
    'arch': 'fc',
    'act': 'swish',
    'act_beta': 1.0,
    'bias': 0,
    'last_bias': 0,
    'var_bias': 0,
    'L': 3,
    'mix_angle': 45,
    'cv_L1': 2,
    'cv_L2': 2,
    'cv_h_base': 1,
    'cv_fsz': 5,
    'cv_pad': 1,
    'cv_stride_first': 1,
    'init_kernel': 0,
    'init_kernel_ptr': 0,
    'regular': 1,
    'running_kernel': None,
    'final_kernel': 0,
    'final_kernel_ptr': 0,
    'final_headless': 0,
    'final_headless_ptr': 0,
    'init_features_ptr': 0,
    'final_features': 0,
    'final_features_ptr': 0,
    'train_kernel': 1,
    'store_kernel': 0,
    'delta_kernel': 0,
    'stretch_kernel': 0,
    'save_outputs': 0,
    'save_state': 0,
    'save_weights': 0,
    'f0': 1,
    'tau_over_h': 1e-3,
    'tau_over_h_kernel': 1e-3,
    'tau_alpha_crit': 1e3,
    'temperature': 0.0,
    'batch_min': 1,
    'batch_max': None,
    'dt_amp': 1.1,
    'dt_dam': 1.1 ** 3,
    'max_wall': 600,
    'max_wall_kernel': 600,
    'wall_max_early_stopping': None,
    'chunk': 100000,
    'max_dgrad': 1e-4,
    'max_dout': 1e-1,
    'loss': 'softhinge',
    'loss_beta': 20.0,
    'loss_margin': 1.0,
    'stop_margin': 1.0,
    'stop_frac': 1.0,
    'bs': None,
    'ckpt_step': 100,
    'ckpt_tau': 1e4,
}

H_VALUES = [100, 300, 1000]
ALPHA_VALUES = [
    1e-5, 3e-5, 1e-4, 3e-4, 1e-3, 3e-3, 1e-2, 3e-2, 0.1, 0.3, 1.0, 3.0,
    10, 30, 100, 300, 1000, 3000, 10000, 30000, 100000, 300000, 1000000,
    3000000, 10000000,
]
SEEDS = [0, 1, 2]


def extract_metrics(run: dict, h: int, alpha: float, seed: int) -> dict | None:
    """Extract final metrics from a training run."""
    if 'regular' not in run or 'dynamics' not in run['regular']:
        return None
    dynamics = run['regular']['dynamics']
    if not dynamics:
        return None
    final = dynamics[-1]
    return {
        'h': h,
        'alpha': alpha,
        'seed_init': seed,
        'dataset': 'fashion',
        'ptr': 10000,
        'pte': 50000,
        'final_train_err': final['train']['err'],
        'final_train_loss': final['train']['loss'],
        'final_train_aloss': final['train']['aloss'],
        'final_train_margin': final['train']['mind'],
        'final_train_nd': final['train']['nd'],
        'final_test_err': final['test']['err'],
        'final_test_loss': final['test']['loss'],
        'final_test_aloss': final['test']['aloss'],
        'final_test_margin': final['test']['mind'],
        'n_steps': final['step'],
        'final_t': final['t'],
        'wall_time': final['wall'],
        'converged': final['train']['err'] < 0.5,
        'init_train_err': dynamics[0]['train']['err'],
        'init_test_err': dynamics[0]['test']['err'],
    }


@app.function(image=lazy_image, gpu="H200", timeout=2 * 60 * 60, concurrency_limit=10)
def run_experiment_batch(batch: list[list]) -> list[tuple[str, dict | None]]:
    """Run a batch of experiments sequentially on one GPU.

    Args:
        batch: List of [h, alpha, seed] triples.

    Returns:
        List of (filename, metrics_dict_or_None) pairs.
    """
    import os
    import sys

    os.chdir("/workspace")
    sys.path.insert(0, "/workspace")

    from main import execute

    results = []
    for i, (h, alpha, seed) in enumerate(batch):
        key = f"h{h}_alpha{alpha:.2e}_seed{seed}.json"
        print(f"  [{i + 1}/{len(batch)}] h={h} α={alpha:.2e} seed={seed}")
        try:
            args = {
                **EXPERIMENT_ARGS_TEMPLATE,
                'h': h,
                'alpha': alpha,
                'seed_init': seed,
                'device': 'cuda',
            }
            run = None
            for run in execute(args):
                pass
            metrics = extract_metrics(run, h, alpha, seed) if run else None
            results.append((key, metrics))
        except Exception as e:
            print(f"    FAILED: {e}")
            results.append((key, None))

    return results


@app.local_entrypoint()
def main(batch_size: int = 10):
    """Run all experiments on Modal, batched across GPUs."""
    script_dir = Path(__file__).resolve().parent
    output_dir = script_dir / 'runs'
    output_dir.mkdir(exist_ok=True)

    experiments = []
    skipped = 0
    for h in H_VALUES:
        for alpha in ALPHA_VALUES:
            for seed in SEEDS:
                json_file = output_dir / f"h{h}_alpha{alpha:.2e}_seed{seed}.json"
                if json_file.exists():
                    try:
                        with open(json_file) as f:
                            metrics = json.load(f)
                            if metrics.get('converged') is not None:
                                skipped += 1
                                continue
                    except Exception:
                        json_file.unlink()
                experiments.append([h, alpha, seed])

    total = len(experiments) + skipped
    n_batches = math.ceil(len(experiments) / batch_size)
    print("=" * 70)
    print(f"Total experiments: {total}")
    print(f"Already complete: {skipped}")
    print(f"To run on Modal: {len(experiments)} in {n_batches} batches of ~{batch_size}")
    print("=" * 70)

    if not experiments:
        print("All experiments already complete!")
        return

    batches = [
        experiments[i:i + batch_size]
        for i in range(0, len(experiments), batch_size)
    ]

    start = time.time()
    handles = [
        (batch_idx, run_experiment_batch.spawn(batch))
        for batch_idx, batch in enumerate(batches)
    ]
    print(f"Spawned {len(handles)} batch containers on Modal")

    completed = 0
    failed = 0
    for batch_idx, handle in handles:
        try:
            batch_results = handle.get()
            for key, metrics in batch_results:
                if metrics is not None:
                    with open(output_dir / key, 'w') as f:
                        json.dump(metrics, f, indent=2)
                    completed += 1
                else:
                    failed += 1
            print(f"  Batch {batch_idx + 1}/{len(handles)} done "
                  f"({sum(1 for _, m in batch_results if m)} ok, "
                  f"{sum(1 for _, m in batch_results if not m)} failed)")
        except Exception as e:
            failed += len(batches[batch_idx])
            print(f"  Batch {batch_idx + 1}/{len(handles)} ERROR: {e}")

    elapsed = time.time() - start
    print(f"\nDone: {completed} succeeded, {failed} failed in {elapsed / 60:.1f}m")
    print(f"Results saved in: {output_dir}/")


if __name__ == '__main__':
    modal.runner.deploy_app(app)
