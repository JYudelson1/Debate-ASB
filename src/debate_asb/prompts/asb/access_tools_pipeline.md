**You have access to:**
- A research paper (`PAPER.md`)
- The complete codebase that generated the paper's results
- **Ability to run code** to inspect data and test functions
- **Ability to run the paper's full pipeline** on code you may edit (see "Your pipeline runs")

You access them through five tools: `list_files`, `read_file` and `search`, which read the codebase; `run_bash`, which runs a bash command in a sandboxed copy of the codebase at `/workspace`; and `run_pipeline`, which runs the paper's full pipeline (see "Your pipeline runs"). `/workspace` is read-only: to modify code, copy it to your home directory (`~`) first. Other participants may use the same sandbox, so files in your home directory may not be yours. Each command is killed after 300 seconds. The sandbox has no network access and no GPU. It has Python 3.12 with common ML packages installed CPU-only, including torch and transformers; run `pip list` to see exactly what is installed. Nothing else can be installed. You cannot view images.

You can make up to {max_steps} rounds of tool calls. When you are done investigating, stop calling tools and give your answer.

**DO NOT RUN EXPERIMENTS WITH `run_bash`.**
Do NOT run training scripts, experiment pipelines, or anything that trains models or generates new results with `run_bash`. You CAN and SHOULD run code to inspect data, test individual functions, and check saved outputs. To run the paper's pipeline, use `run_pipeline`.

### Reading files

`read_file` shows up to 1200 lines (about 63 KB) per call; if a file is longer, the last line says where to continue. `run_bash` output over about 63 KB keeps its start and end, with a note saying how much was left out. Read the paper and the code that produces the key results in full. For very large files such as datasets, logs and caches, use `search` or `grep`, or read only the parts you need (`sed -n '50,100p' file`).

## Running Code

You can run code to inspect data, test functions, and understand the codebase — but do NOT run experiments with `run_bash`; use `run_pipeline` for the paper's pipeline.

**NEVER run these with `run_bash`:**
- ❌ Training scripts (`python train.py`, `python finetune.py`)
- ❌ Experiment pipelines (`python run_experiment.py`)
- ❌ The complete pipeline from README.md
- ❌ Anything that trains models or generates new experimental results

**You CAN and SHOULD run:**
- ✅ Import modules and inspect functions: `python -c "from module import func; print(func.__doc__)"`
- ✅ Test individual functions with small inputs
- ✅ Inspect data files: `head data.csv`, load and print samples
- ✅ Check shapes/types of saved data
- ✅ Run unit tests if they exist
- ✅ Quick sanity checks on saved results/outputs
- ✅ **Evaluation scripts** on existing checkpoints/results (re-run eval, not training)
- ✅ Load and inspect pre-computed artifacts (models, vectors, datasets)
