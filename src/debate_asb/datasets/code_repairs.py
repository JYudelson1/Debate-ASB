"""Small, source-checked repairs applied only to copied ASB workspaces."""

import hashlib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SourceRepair:
    expected_sha256: str
    replacements: tuple[tuple[str, str], ...]


SOURCE_REPAIRS: dict[str, dict[str, SourceRepair]] = {
    "diffuse_fox": {
        "src/probes.py": SourceRepair(
            expected_sha256=(
                "fb6a84f0b4691b03dacd71b88472d5e2b535ccf42c58ea11ad540b13f2fea8a4"
            ),
            replacements=(
                (
                    "                    y_fold_train, y_fold_val = "
                    "y_train[train_idx], y_train[val_idx]\n",
                    "                    y_fold_train, y_fold_val = "
                    "y_train[train_idx], y_train[val_idx]\n"
                    "                    # AUROC needs both classes in every validation fold.\n"
                    "                    if np.unique(y_fold_val).size < 2:\n"
                    "                        fold_aurocs = []\n"
                    "                        break\n",
                ),
                (
                    "                    y_fold_pred = "
                    "clf_fold.predict_proba(X_fold_val)[:, 1]\n"
                    "                    fold_auroc = roc_auc_score(y_fold_val, y_fold_pred)\n"
                    "                    fold_aurocs.append(fold_auroc)\n",
                    "                    y_fold_pred = "
                    "clf_fold.predict_proba(X_fold_val)[:, 1]\n"
                    "                    if not np.isfinite(y_fold_pred).all():\n"
                    "                        fold_aurocs = []\n"
                    "                        break\n"
                    "                    fold_auroc = roc_auc_score(y_fold_val, y_fold_pred)\n"
                    "                    if not np.isfinite(fold_auroc):\n"
                    "                        fold_aurocs = []\n"
                    "                        break\n"
                    "                    fold_aurocs.append(fold_auroc)\n",
                ),
                (
                    "                cv_results[alpha] = np.mean(fold_aurocs)\n",
                    "                # Never compare means computed from incomplete folds.\n"
                    "                cv_results[alpha] = (\n"
                    "                    float(np.mean(fold_aurocs))\n"
                    "                    if len(fold_aurocs) == alpha_cv_folds\n"
                    "                    else np.nan\n"
                    "                )\n",
                ),
                (
                    "            best_alpha = max(cv_results, key=cv_results.get)\n",
                    "            finite_cv_results = {\n"
                    "                alpha: score for alpha, score in cv_results.items()\n"
                    "                if np.isfinite(score)\n"
                    "            }\n"
                    "            if not finite_cv_results:\n"
                    "                raise ValueError(\n"
                    '                    "No alpha has finite AUROC in every CV fold "\n'
                    '                    f"(N={len(y_train)}, folds={alpha_cv_folds})"\n'
                    "                )\n"
                    "            best_alpha = max(finite_cv_results, key=finite_cv_results.get)\n",
                ),
            ),
        ),
    },
    "hop_stride": {
        "experiments/fully_synthetic/run_evaluation.py": SourceRepair(
            expected_sha256=(
                "1cdfff9aa7cd074e2bd9326abff62c762ff1df52ec5a6af5604375563b6e51a2"
            ),
            replacements=(
                (
                    '            messages, record["messages"][2]["content"], '
                    'hash(messages[-1]["content"]), record\n',
                    '            messages, record["answer"], '
                    'hash(messages[-1]["content"]), record\n',
                ),
            ),
        ),
        "experiments/fully_synthetic/evaluate_openai_ft_models.py": SourceRepair(
            expected_sha256=(
                "bb197d4e2a851410e94da0d76c13cf3ff39d4f248a7a864d3088d4c31a406687"
            ),
            replacements=(
                (
                    '            messages, record["messages"][2]["content"], '
                    'hash(messages[-1]["content"]), record\n',
                    '            messages, record["answer"], '
                    'hash(messages[-1]["content"]), record\n',
                ),
            ),
        ),
    },
}


def repair_files(sample_id: str, original: Path) -> dict[str, bytes]:
    """Return repaired file bytes without modifying the packaged ASB source.

    A source mismatch is an error: repairs must be reviewed when ASB changes.
    Each exact replacement must occur once, even with a matching source hash.
    """
    repaired = {}
    for relative_path, repair in SOURCE_REPAIRS.get(sample_id, {}).items():
        source = (original / relative_path).read_bytes()
        if hashlib.sha256(source).hexdigest() != repair.expected_sha256:
            raise ValueError(f"ASB repair source changed: {sample_id}/{relative_path}")
        text = source.decode("utf-8")
        for old, new in repair.replacements:
            count = text.count(old)
            if count != 1:
                raise ValueError(
                    f"ASB repair expected one match, found {count}: "
                    f"{sample_id}/{relative_path}"
                )
            text = text.replace(old, new, 1)
        repaired[relative_path] = text.encode("utf-8")
    return repaired
