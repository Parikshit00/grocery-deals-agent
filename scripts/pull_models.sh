#!/usr/bin/env bash
# Ensure required Ollama models are present (vision + fallback + embeddings).
# The reasoning model is served by vLLM (see serve_vllm.sh), not Ollama.
set -euo pipefail

if ! pgrep -x ollama >/dev/null 2>&1; then
  echo ">> Ollama not running. Start it with: ollama serve &"
  echo "   (continuing; 'ollama pull' will start it on demand)"
fi

VISION_MODEL="${VISION_MODEL:-qwen2.5vl}"
FALLBACK_CHAT_MODEL="${FALLBACK_CHAT_MODEL:-llama3.1}"
EMBEDDING_MODEL="${EMBEDDING_MODEL:-bge-m3}"

for m in "$VISION_MODEL" "$FALLBACK_CHAT_MODEL" "$EMBEDDING_MODEL"; do
  echo ">> ollama pull ${m}"
  ollama pull "$m"
done

echo ">> Models ready:"
ollama list
