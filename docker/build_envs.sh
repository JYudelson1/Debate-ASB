#!/bin/sh
# Build one CPU-only Python environment per ASB codebase, from that codebase's
# own uv.lock, so participants can import and test its code with the package
# versions the paper actually used. Run at image build time with the ASB
# codebases mounted at $1 (<paper>/<variant>/uv.lock).
#
# /opt/envs/by-lock/<hash>   one environment per distinct pyproject+lock
# /opt/envs/by-id/<sample>   symlink to it, e.g. hop_jump; task.py points
#                            /opt/venv at the sample's one when the sandbox starts.
# torch/torchvision/torchaudio come from PyTorch's CPU index at the locked
# version. GPU-only packages are left out (they can't run here and their wheels
# are huge). Packages the codebase imports but its lock lacks are added within
# the lock's versions (add_imports.py). A codebase whose environment fails to
# build gets none and uses the base Python; the build log says which.
set -u
SRC=$1
OUT=/opt/envs
# Prefixes (nvidia-cublas-cu12, cuda-bindings, jax-cuda12-plugin, unsloth-zoo...)
# and exact names; torch* are installed separately from the CPU index.
GPU_ONLY='^((nvidia|cuda|cupy|jax-cuda|jaxlib-cuda|unsloth)[-_]|(triton|pytorch-triton|vllm|xformers|flash-attn|flash_attn|bitsandbytes|unsloth|deepspeed|torch|torchvision|torchaudio)[=; ])'
mkdir -p "$OUT/by-lock" "$OUT/by-id"

# Install a requirements file without dependencies (the lock already lists them
# all). If that fails because some pinned version can't be installed (e.g. it
# was removed from PyPI), install line by line and report what was skipped.
install_all() {
    uv pip install -q --python "$1/bin/python" --no-deps -r "$2" 2>/dev/null && return 0
    one=$(mktemp)
    grep -Ev '^ *(#|$)' "$2" | while IFS= read -r line; do
        echo "$line" > "$one"
        uv pip install -q --python "$1/bin/python" --no-deps -r "$one" 2>/dev/null \
            || echo "   $3: skipped ${line%% *} (can't be installed)"
    done
    rm -f "$one"
}
for lock in "$SRC"/*/*/uv.lock; do
    dir=$(dirname "$lock")
    id="$(basename "$(dirname "$dir")")_$(basename "$dir")"
    hash=$(cat "$dir/pyproject.toml" "$lock" | sha256sum | cut -c1-12)
    env="$OUT/by-lock/$hash"
    if [ ! -e "$env/.ok" ] && [ ! -e "$env/.failed" ]; then
        echo "== building $id ($hash)"
        req=$(mktemp)
        # All optional extras too (e.g. hop's "llama" extra pins torch and
        # transformers); without them if they conflict with each other.
        export="uv export --frozen --no-hashes --no-dev --no-emit-project --no-header --project $dir --python 3.12 -q"
        if $export --all-extras > "$req" 2> "$req.err" || $export > "$req" 2> "$req.err"; then
            # Kept with their markers: a lock can pin different torch versions per platform.
            grep -E '^(torch|torchvision|torchaudio)==' "$req" > "$req.torch"
            grep -Ev "$GPU_ONLY" "$req" > "$req.cpu"
            if uv venv -q --seed --python 3.12 "$env" \
                && { [ ! -s "$req.torch" ] || uv pip install -q --python "$env/bin/python" --no-deps \
                        --index-url https://download.pytorch.org/whl/cpu -r "$req.torch"; } \
                && install_all "$env" "$req.cpu" "$id"; then
                # Packages the codebase imports but its lock doesn't declare go into
                # its own environment too, constrained to the locked versions; one
                # that can't fit them is skipped, never the lock changed.
                for dist in $("$env/bin/python" /usr/local/bin/add_imports.py "$dir"); do
                    if [ "$dist" = torch ]; then
                        set -- --index-url https://download.pytorch.org/whl/cpu
                    else
                        set --
                    fi
                    uv pip install -q --python "$env/bin/python" -c "$req.cpu" "$@" "$dist" \
                        && echo "   $id: added $dist (not in its lock)" \
                        || echo "   $id: couldn't add $dist within its lock"
                done
                touch "$env/.ok"
            else
                echo "!! $id: install failed"; mkdir -p "$env"; touch "$env/.failed"
            fi
        else
            echo "!! $id: uv export failed: $(tail -1 "$req.err")"; mkdir -p "$env"; touch "$env/.failed"
        fi
        rm -f "$req" "$req.err" "$req.cpu" "$req.torch"
    fi
    [ -e "$env/.ok" ] && ln -sfn "$env" "$OUT/by-id/$id"
done
echo "== environments:"; ls "$OUT/by-id"
