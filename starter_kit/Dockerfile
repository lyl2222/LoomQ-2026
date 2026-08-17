FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DEFAULT_TIMEOUT=300 \
    PIP_RETRIES=10

# pyqpanda 依赖 libcurl.so.4
RUN apt-get update && apt-get install -y --no-install-recommends \
    libcurl4 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace/submission
COPY requirements*.txt ./

# Vendor SDKs intentionally live in separate import layers. SpinQit and the
# Braket simulator require incompatible ANTLR runtimes, so merging them into a
# single site-packages directory is not reproducible.
RUN pip install --no-cache-dir --no-deps \
        --target /opt/loomq-sdk/spinq \
        -r requirements-spinq.txt
RUN pip install --no-cache-dir --no-deps \
        --target /opt/loomq-sdk/originq \
        -r requirements-originq.txt
RUN pip install --no-cache-dir --no-deps \
        --target /opt/loomq-sdk/braket \
        -r requirements-braket.txt
COPY . .

CMD ["python", "verify.py"]
