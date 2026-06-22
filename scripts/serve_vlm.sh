#!/usr/bin/env bash
# Serve the OCR VLM (Qwen3-VL) via vLLM on GPU 1 - reads prospekt pages.
set -euo pipefail

CONDA="${CONDA:-conda}"
ENV_NAME="${VLLM_ENV:-gda_vllm}"
MODEL="${VLM_MODEL_PATH:-/home/parikshitk/models/Qwen3-VL-32B-Instruct-FP8}"
PORT="${VLM_PORT:-28801}"
GPU="${VLM_GPU:-1}"

CUDA_VISIBLE_DEVICES="$GPU" exec "$CONDA" run -n "$ENV_NAME" --no-capture-output \
  vllm serve "$MODEL" --served-model-name qwen3-vl --host 0.0.0.0 --port "$PORT" \
  --gpu-memory-utilization 0.92 --max-model-len 32768
