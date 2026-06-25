#!/usr/bin/env bash
# Serve the OCR VLM (Qwen3-VL-32B-Instruct, FP8) via vLLM on GPU 1 - reads prospekt pages.
# Runs in the cu128 `gda_vlm` env. cu128 runs on the CUDA 12.7 driver via CUDA minor-version
# compatibility; the cuda-compat-12-8 dir is prepended to LD_LIBRARY_PATH as a forward-compat
# safety net (datacenter A40) in case a kernel calls a 12.8-only driver symbol.
set -euo pipefail

CONDA="${CONDA:-/opt/miniforge/bin/conda}"
ENV_NAME="${VLM_ENV:-gda_vlm}"
MODEL="${VLM_MODEL_PATH:-/home/parikshitk/models/Qwen3-VL-32B-Instruct-FP8}"
PORT="${VLM_PORT:-28801}"
GPU="${VLM_GPU:-1}"
COMPAT="${CUDA_COMPAT_DIR:-/home/parikshitk/cuda-compat-12.8/usr/local/cuda-12.8/compat}"

# We only ever send one still image per page (no video, no multi-image), so cap the
# multimodal profiler to image=1 / video=0 - otherwise Qwen3-VL reserves worst-case video
# memory and leaves nothing for the KV cache on a 46GB card after 34GB of FP8 weights.
CUDA_VISIBLE_DEVICES="$GPU" LD_LIBRARY_PATH="${COMPAT}:${LD_LIBRARY_PATH:-}" \
  exec "$CONDA" run -n "$ENV_NAME" --no-capture-output \
  vllm serve "$MODEL" --served-model-name qwen3-vl --host 0.0.0.0 --port "$PORT" \
  --gpu-memory-utilization 0.95 --max-model-len 12288 --max-num-seqs 4 \
  --limit-mm-per-prompt '{"image":1,"video":0}'
