#!/usr/bin/env bash
# Serve the reasoning/supervisor LLM (Qwen3-30B-A3B-Thinking) via vLLM on GPU 0.
set -euo pipefail

CONDA="${CONDA:-conda}"
ENV_NAME="${VLLM_ENV:-gda_vllm}"
MODEL="${LLM_MODEL_PATH:-/home/parikshitk/models/Qwen3-30B-A3B-Thinking-2507-FP8}"
PORT="${LLM_PORT:-28800}"
GPU="${LLM_GPU:-0}"

CUDA_VISIBLE_DEVICES="$GPU" exec "$CONDA" run -n "$ENV_NAME" --no-capture-output \
  vllm serve "$MODEL" --served-model-name qwen3-thinking --host 0.0.0.0 --port "$PORT" \
  --gpu-memory-utilization 0.85 --max-model-len 32768 --reasoning-parser deepseek_r1
