# Sandbox for participants that can run code (task arg code_execution=true).
#
# ASB's own auditor container isn't public, so this is ours: CPU-only, no GPU,
# and no network at run time, so training runs and API-backed pipelines can't
# run. Each ASB codebase gets its own Python environment built from its own
# uv.lock (build_envs.sh), so participants can import and test its code with the
# package versions the paper used; task.py points /opt/venv at the sample's
# environment when the sandbox starts. GPU-only packages (vllm, bitsandbytes,
# unsloth, xformers, CUDA libraries) are left out. Samples without an ASB
# environment (or whose environment failed to build) use the base Python below.
FROM python:3.12-slim

# Command-line tools participants reached for in our transcripts.
RUN apt-get update && apt-get install -y --no-install-recommends \
        file binutils xxd unzip jq ripgrep less sqlite3 \
    && rm -rf /var/lib/apt/lists/*

# Base Python: common ML packages, for samples without their own environment.
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch torchvision
RUN pip install --no-cache-dir \
    numpy pandas scipy scikit-learn matplotlib seaborn pyyaml tqdm einops rich regex pytest \
    python-dotenv pydantic transformers datasets peft openai anthropic

# One environment per distinct ASB pyproject+uv.lock. The codebases are only
# mounted for this step (compose.yaml's "asb" build context), not copied in.
COPY --from=ghcr.io/astral-sh/uv:0.9.30 /uv /usr/local/bin/uv
COPY build_envs.sh add_imports.py /usr/local/bin/
RUN --mount=type=bind,from=asb,target=/asb \
    UV_LINK_MODE=hardlink UV_PYTHON_PREFERENCE=only-system \
    sh /usr/local/bin/build_envs.sh /asb && rm -rf /root/.cache/uv

# /opt/venv/bin comes first on PATH; it points at the base Python until task.py
# links the sample's environment.
RUN ln -s /usr/local /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Participants run as this unprivileged user. The codebase is copied to
# /workspace as root and made read-only (see task.py), so no participant can
# change files another participant will run; scratch work goes in ~.
RUN useradd --create-home auditor && mkdir -p /workspace
WORKDIR /workspace
