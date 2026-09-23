# Sandbox for participants that can run code (task arg code_execution=true).
#
# ASB's own auditor container isn't public, so this is ours: CPU-only Python
# with the packages the ASB codebases most commonly import, so modules import
# and small functions can be tested. No GPU, and no network at run time, so
# training runs and API-backed pipelines can't run. Participants are told to
# run `pip list` to see what's here. GPU-only packages (vllm, bitsandbytes,
# unsloth, nnsight, transformer_lens, jax) aren't installed.
FROM python:3.12-slim

RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch torchvision
RUN pip install --no-cache-dir \
    numpy pandas scipy scikit-learn matplotlib seaborn pyyaml tqdm einops rich regex pytest \
    python-dotenv pydantic transformers datasets peft openai anthropic

# Participants run as this unprivileged user. The codebase is copied to
# /workspace as root and made read-only (see task.py), so no participant can
# change files another participant will run; scratch work goes in ~.
RUN useradd --create-home auditor && mkdir -p /workspace
WORKDIR /workspace
