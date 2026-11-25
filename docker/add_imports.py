"""Print the packages a codebase imports but its environment lacks, as install specs.

Run with the codebase's environment's python: python add_imports.py <codebase dir>.
Only known PyPI packages are listed (import name -> distribution), so a name the
codebase imports from somewhere it doesn't ship (e.g. "grid") is never guessed
at. GPU-only packages are never listed; torch is listed as "torch" and
build_envs.sh installs it from the CPU index.
"""

import ast
import importlib.util
import sys
from pathlib import Path

KNOWN = {
    "accelerate": "accelerate", "anthropic": "anthropic", "astor": "astor",
    "black": "black", "datasets": "datasets", "debugpy": "debugpy",
    "devtools": "devtools", "dotenv": "python-dotenv", "einops": "einops",
    "emoji": "emoji", "fire": "fire", "geonamescache": "geonamescache",
    "haiku": "dm-haiku", "httpcore": "httpcore", "huggingface_hub": "huggingface-hub",
    "inspect_ai": "inspect-ai", "jax": "jax", "langsmith": "langsmith",
    "loguru": "loguru", "matplotlib": "matplotlib", "modal": "modal",
    "mpmath": "mpmath", "nltk": "nltk", "nnsight": "nnsight", "numpy": "numpy",
    "openai": "openai", "pandas": "pandas", "peft": "peft", "pydantic": "pydantic",
    "pytest": "pytest", "pytz": "pytz", "regex": "regex", "requests": "requests",
    "rich": "rich", "scipy": "scipy", "seaborn": "seaborn", "sklearn": "scikit-learn",
    "tables": "tables", "tiktoken": "tiktoken", "torch": "torch", "tqdm": "tqdm",
    "transformer_lens": "transformer-lens", "transformers": "transformers",
    "trl": "trl", "wandb": "wandb", "xarray": "xarray", "yaml": "pyyaml",
}

root = Path(sys.argv[1])
local = {p.stem for p in root.rglob("*.py")} | {p.name for p in root.rglob("*") if p.is_dir()}
wanted = set()
for file in root.rglob("*.py"):
    try:
        tree = ast.parse(file.read_text(errors="replace"))
    except SyntaxError:
        continue
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            wanted |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            wanted.add(node.module.split(".")[0])
missing = [
    KNOWN[name]
    for name in sorted(wanted - local - set(sys.stdlib_module_names))
    if name in KNOWN and importlib.util.find_spec(name) is None
]
# torch first, from the CPU index: otherwise a package that needs it would pull
# PyPI's CUDA build in as a dependency.
for dist in sorted(missing, key=lambda d: d != "torch"):
    print(dist)
