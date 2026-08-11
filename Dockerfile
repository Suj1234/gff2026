# Underwriting agent — FastAPI + DSPy. Runs POST /underwrite for the journey to call.
FROM python:3.13-slim

WORKDIR /app

# System certs for the corporate TLS proxy (dspy/tiktoken fetch model-cost data on-network).
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Only the agent package is needed to serve /underwrite.
COPY underwriting ./underwriting

EXPOSE 8899
# 1 worker is plenty for a demo; grey-zone calls are LLM-bound, not CPU-bound.
CMD ["uvicorn", "underwriting.api:app", "--host", "0.0.0.0", "--port", "8899"]
