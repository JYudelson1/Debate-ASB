"""Exercise the repaired CV function without loading sklearn, Torch, or a model."""

import ast
import hashlib
import sys
import types
from dataclasses import replace

import pytest

from debate_asb.datasets.code_repairs import SOURCE_REPAIRS, SourceRepair, repair_files

np = pytest.importorskip("numpy")

# Original CV implementation, reduced to its closed-form path for portable tests.
# The four repair targets are copied verbatim; no ASB checkout is required.
ORIGINAL_CV = """def train_linear_probe(
    X_train, y_train, X_test, y_test, random_state=42,
    alpha_cv_folds=2, alpha_cv_alphas=(100.0, 10000.0),
):
    import config
    from sklearn.metrics import roc_curve, auc, roc_auc_score
    from sklearn.model_selection import KFold
    if True:
        if True:
            kf = KFold(n_splits=alpha_cv_folds, shuffle=True, random_state=random_state)
            cv_results = {}
            for alpha in alpha_cv_alphas:
                fold_aurocs = []
                for fold_idx, (train_idx, val_idx) in enumerate(kf.split(X_train)):
                    X_fold_train, X_fold_val = X_train[train_idx], X_train[val_idx]
                    y_fold_train, y_fold_val = y_train[train_idx], y_train[val_idx]
                    clf_fold = ClosedFormRidgeClassifier(alpha=alpha)
                    clf_fold.fit(X_fold_train, y_fold_train)
                    y_fold_pred = clf_fold.predict_proba(X_fold_val)[:, 1]
                    fold_auroc = roc_auc_score(y_fold_val, y_fold_pred)
                    fold_aurocs.append(fold_auroc)
                cv_results[alpha] = np.mean(fold_aurocs)
            best_alpha = max(cv_results, key=cv_results.get)
            clf = ClosedFormRidgeClassifier(alpha=best_alpha)
            clf.fit(X_train, y_train)
            clf.best_alpha_ = best_alpha
            clf.cv_results_ = cv_results
        y_pred_proba = clf.predict_proba(X_test)[:, 1]
        fpr, tpr, _ = roc_curve(y_test, y_pred_proba)
        auroc = auc(fpr, tpr)
        return auroc, clf, y_pred_proba, fpr, tpr
"""


@pytest.fixture
def cv_function(tmp_path, monkeypatch):
    original = tmp_path / "src" / "probes.py"
    original.parent.mkdir()
    original.write_text(ORIGINAL_CV)
    repair = SOURCE_REPAIRS["diffuse_fox"]["src/probes.py"]
    fixture_repair = replace(
        repair, expected_sha256=hashlib.sha256(original.read_bytes()).hexdigest()
    )
    monkeypatch.setitem(
        SOURCE_REPAIRS, "diffuse_fox", {"src/probes.py": fixture_repair}
    )
    source = repair_files("diffuse_fox", tmp_path)["src/probes.py"]
    assert original.read_text() == ORIGINAL_CV
    return _load_function(source, monkeypatch)


def _load_function(source, monkeypatch):
    """Keep the real repaired function but supply deterministic CPU test doubles."""
    tree = ast.parse(source)
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "train_linear_probe"
    )
    settings = types.SimpleNamespace(
        scores={100.0: [0.8, 0.6], 10000.0: [0.7, 0.9]},
        predictions={},
        metric_calls=[],
        fits=[],
        split_arguments=[],
    )

    class KFold:
        def __init__(self, **kwargs):
            settings.split_arguments.append(kwargs)

        def split(self, X):
            yield np.array([2, 3]), np.array([0, 1])
            yield np.array([0, 1]), np.array([2, 3])

    class Ridge:
        def __init__(self, alpha, **kwargs):
            self.alpha = alpha

        def fit(self, X, y):
            settings.fits.append((self.alpha, len(X)))

        def predict_proba(self, X):
            value = settings.predictions.get(self.alpha, self.alpha)
            return np.column_stack([np.zeros(len(X)), np.full(len(X), value)])

    def roc_auc_score(labels, predictions):
        # Reaching this with a single-class fold would raise in some sklearns.
        assert np.unique(labels).size == 2
        alpha = float(predictions[0])
        fold = sum(a == alpha for a in settings.metric_calls)
        settings.metric_calls.append(alpha)
        return settings.scores[alpha][fold]

    metrics = types.ModuleType("sklearn.metrics")
    setattr(metrics, "roc_auc_score", roc_auc_score)
    setattr(
        metrics,
        "roc_curve",
        lambda labels, predictions: (
            np.array([0.0, 1.0]),
            np.array([0.0, 1.0]),
            np.array([0.0]),
        ),
    )
    setattr(metrics, "auc", lambda fpr, tpr: 0.5)
    selection = types.ModuleType("sklearn.model_selection")
    setattr(selection, "KFold", KFold)
    monkeypatch.setitem(sys.modules, "sklearn", types.ModuleType("sklearn"))
    monkeypatch.setitem(sys.modules, "sklearn.metrics", metrics)
    monkeypatch.setitem(sys.modules, "sklearn.model_selection", selection)
    monkeypatch.setitem(sys.modules, "config", types.ModuleType("config"))
    namespace = {
        "np": np,
        "Tuple": tuple,
        "ClosedFormRidgeClassifier": Ridge,
    }
    function.returns = None
    for argument in function.args.args:
        argument.annotation = None
    exec(
        compile(ast.Module(body=[function], type_ignores=[]), "probes.py", "exec"),
        namespace,
    )
    return namespace["train_linear_probe"], settings


def run_cv(function, **kwargs):
    return function(
        np.arange(4).reshape(-1, 1),
        np.array([0, 1, 0, 1]),
        np.arange(2).reshape(-1, 1),
        np.array([0, 1]),
        **kwargs,
    )


def test_finite_cv_keeps_alpha_selection_and_seed(cv_function):
    function, settings = cv_function
    _, clf, *_ = run_cv(function)
    assert clf.best_alpha_ == 10000.0
    assert clf.cv_results_ == {100.0: 0.7, 10000.0: 0.8}
    assert settings.split_arguments == [
        {"n_splits": 2, "shuffle": True, "random_state": 42}
    ]
    assert settings.fits[-1] == (10000.0, 4)


def test_nan_first_candidate_does_not_hide_later_finite_candidate(cv_function):
    function, settings = cv_function
    settings.scores[100.0] = [np.nan, 0.9]
    _, clf, *_ = run_cv(function)
    assert clf.best_alpha_ == 10000.0
    assert np.isnan(clf.cv_results_[100.0])


def test_one_invalid_fold_invalidates_entire_alpha(cv_function):
    function, settings = cv_function
    settings.scores[100.0] = [1.0, np.nan]
    _, clf, *_ = run_cv(function)
    assert clf.best_alpha_ == 10000.0
    assert np.isnan(clf.cv_results_[100.0])


def test_all_invalid_candidates_fail_before_final_fit(cv_function):
    function, settings = cv_function
    settings.scores = {100.0: [np.nan], 10000.0: [np.inf]}
    with pytest.raises(ValueError, match="No alpha has finite AUROC in every CV fold"):
        run_cv(function)
    assert all(size == 2 for _, size in settings.fits)


def test_single_class_fold_never_reaches_metric(cv_function):
    function, settings = cv_function
    with pytest.raises(ValueError, match="No alpha has finite AUROC"):
        function(
            np.arange(4).reshape(-1, 1),
            np.array([0, 0, 1, 1]),
            np.arange(2).reshape(-1, 1),
            np.array([0, 1]),
        )
    assert settings.metric_calls == []
    assert settings.fits == []


def test_nonfinite_prediction_invalidates_alpha_before_metric(cv_function):
    function, settings = cv_function
    settings.predictions[100.0] = np.nan
    _, clf, *_ = run_cv(function)
    assert clf.best_alpha_ == 10000.0
    assert 100.0 not in settings.metric_calls


def test_empty_alpha_grid_fails_clearly(cv_function):
    function, _ = cv_function
    with pytest.raises(ValueError, match="No alpha has finite AUROC"):
        run_cv(function, alpha_cv_alphas=[])


def test_unknown_sample_has_no_repair_and_does_not_read(tmp_path):
    assert repair_files("diffuse_owl", tmp_path / "absent") == {}


def test_source_hash_guard(tmp_path):
    source = tmp_path / "src" / "probes.py"
    source.parent.mkdir()
    source.write_text(ORIGINAL_CV)
    with pytest.raises(ValueError, match="ASB repair source changed"):
        repair_files("diffuse_fox", tmp_path)
    assert source.read_text() == ORIGINAL_CV


@pytest.mark.parametrize("contents", ["unrelated", "target target"])
def test_replacement_requires_exactly_one_match(tmp_path, monkeypatch, contents):
    source = tmp_path / "source.py"
    source.write_text(contents)
    repair = SourceRepair(
        hashlib.sha256(source.read_bytes()).hexdigest(), (("target", "fixed"),)
    )
    monkeypatch.setitem(SOURCE_REPAIRS, "fixture", {"source.py": repair})
    with pytest.raises(ValueError, match="ASB repair expected one match"):
        repair_files("fixture", tmp_path)
    assert source.read_text() == contents


def test_repair_preserves_unrelated_bytes(tmp_path, monkeypatch):
    source = tmp_path / "source.py"
    source.write_bytes(b"# prefix\nvalue = 1\n# suffix\n")
    repair = SourceRepair(
        hashlib.sha256(source.read_bytes()).hexdigest(), (("value = 1", "value = 2"),)
    )
    monkeypatch.setitem(SOURCE_REPAIRS, "fixture", {"source.py": repair})
    assert repair_files("fixture", tmp_path) == {
        "source.py": b"# prefix\nvalue = 2\n# suffix\n"
    }
    assert source.read_bytes() == b"# prefix\nvalue = 1\n# suffix\n"


def test_registered_repair_applies_to_packaged_function(monkeypatch):
    from debate_asb.datasets.asb import ASB_ROOT

    original = ASB_ROOT / "codebases" / "diffuse" / "fox"
    if not original.exists():
        pytest.skip("ASB data not fetched")
    source = repair_files("diffuse_fox", original)["src/probes.py"]
    compile(source, "probes.py", "exec")
    function, settings = _load_function(source, monkeypatch)
    _, clf, *_ = run_cv(
        function,
        mode="closed_form",
        alpha_cv_enabled=True,
        alpha_cv_folds=2,
        alpha_cv_alphas=[100.0, 10000.0],
    )
    assert clf.best_alpha_ == 10000.0
    assert settings.fits[-1] == (10000.0, 4)
