# Codex Cloud

Use a Codex Cloud environment for running this repository's Inspect evaluations
from Codex. The model calls go to OpenRouter, so the evaluation workload does
not need a GPU.

## Create the environment

1. In Codex, choose **Work in → Cloud → Create environment** and select this
   repository from GitHub.
2. Set the **Install script** to:

   ```bash
   bash scripts/codex_cloud_install.sh
   ```

   It installs `uv` if needed, then runs `scripts/setup.sh`, which syncs the
   locked dependencies from `uv.lock`. Leave the **Start skill** empty; this is
   a batch command-line project and does not start a service.
3. Allow package-manager access for dependency installation. Add
   `openrouter.ai` to the allowed domains for model calls. If fetching
   MonitoringBench data, allow `huggingface.co` and any download host the
   environment reports as blocked. ASB is fetched from GitHub.
4. Add `OPENROUTER_API_KEY` as a Codex Cloud HTTP secret/network secret allowed
   for `openrouter.ai`. Do not put the key in the repository or install script.
5. Review and publish the environment. Start a new Cloud task to use the
   published setup.

## Run an evaluation

Use the same commands as in the main README, for example:

```bash
uv run inspect eval src/debate_asb/protocols/lone_judge.py --model none --log-model-api \
  -T sample_ids=hop_jump,hop_stride
```

Fetch only the data needed for a task. ASB is about 4.4 GB and is downloaded
with `scripts/fetch_asb.sh`; MonitoringBench can be fetched with
`uv run python -m debate_asb.datasets.monitoringbench fetch --preset pilot_v1`.
Neither dataset is downloaded during environment installation.

Cloud tasks check out the GitHub revision selected for the environment. Push
local changes before expecting a new Cloud task to include them. Keep important
evaluation logs and reports in the task or commit them to the repository; data
and logs are gitignored locally.
