#!/usr/bin/env bash
# Serve the reasoning model with vLLM (OpenAI-compatible API) on GPU 0.
set -euo pipefail

CONDA="${CONDA:-/opt/miniforge/bin/conda}"
VLLM_ENV="${VLLM_ENV:-vllm}"
LLM_MODEL="${LLM_MODEL:-Qwen/Qwen2.5-7B-Instruct}"
PORT="${LLM_PORT:-8000}"

echo ">> Serving ${LLM_MODEL} via vLLM on GPU 0, port ${PORT}"
CUDA_VISIBLE_DEVICES=0 "$CONDA" run -n "$VLLM_ENV" \
  python -m vllm.entrypoints.openai.api_server \
  --model "$LLM_MODEL" \
  --port "$PORT" \
  --gpu-memory-utilization 0.85 \
  --max-model-len 16384
