# Explicit Python patch and immutable registry digest; no Ubuntu system Python dependency.
FROM python:3.11.17-slim-bookworm@sha256:2333bd330d12de02514770b3585cad313644316047cdee24a7acfdece6de6efb
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 PHANTOMGUARD_ROOT=/workspace
WORKDIR /app
COPY requirements/runtime.lock /app/requirements/runtime.lock
RUN python -m pip install --no-cache-dir -r requirements/runtime.lock
COPY pyproject.toml README.md /app/
COPY src /app/src
RUN python -m pip install --no-cache-dir --no-deps . && useradd --uid 10001 --create-home phantomguard && mkdir -p /workspace/runs && chown -R 10001:10001 /workspace
USER 10001:10001
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/readyz',timeout=8).read()"
CMD ["phantomguard", "serve", "--host", "0.0.0.0", "--port", "8765"]
