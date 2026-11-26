You can use `list_files`, `read_file`, and `search` to inspect this paper and
codebase, and `run_bash` to execute CPU code in its Docker sandbox. You have up to
{max_steps} rounds of tool calls for this investigation/turn. Each command is
killed after 300 seconds. There is no network access and no GPU. Python 3.12,
CPU torch, numpy, pandas, scipy and other common packages are installed; inspect
`pip list` if needed. You cannot install packages or download model weights.

`/workspace` contains only this codebase and is read-only. Use a fresh directory
under `~` for your own scripts and counterfactual copies. Other participants in
this sample share the sandbox: do not read or alter their scratch work. Never
modify the supplied original. No other codebase or reference implementation is
available to you.

You ARE permitted to run bounded CPU experiments: reanalyse saved outputs,
compare the existing computation with a justified counterfactual in a scratch
copy, test individual functions, and run small synthetic or reduced-size probes.
Prefer existing data and checkpoints. Do not launch full training runs, external
API calls, GPU jobs, or full experiment pipelines. A command timeout is a limit,
not an instruction to spend that much time.
