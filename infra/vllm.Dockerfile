# Model server image. The upstream cu129 tag ships torch 2.14.0, which is built for CUDA 13 and
# fails on CUDA 12.x drivers, although vLLM 0.30.0 itself pins torch 2.13.0. Reinstall the
# matching cu129 torch; the image's CUDA 12.9 compat libraries then run on drivers >= 525.
FROM vllm/vllm-openai:v0.30.0-cu129@sha256:a67f8f186d4567612ac37a55bd82295006002b18858eb12a8af2f05f86c2ae3f
RUN pip install --no-cache-dir --no-deps --force-reinstall "torch==2.13.0+cu129" \
    --index-url https://download.pytorch.org/whl/cu129
