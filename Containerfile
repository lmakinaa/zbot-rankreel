FROM registry.fedoraproject.org/fedora:44 AS app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN dnf -y --setopt=install_weak_deps=False install python3 python3-pip ca-certificates shadow-utils \
    && dnf clean all \
    && useradd --create-home --uid 1000 rankreel

RUN python3 -m venv /opt/rankreel-venv
ENV PATH="/opt/rankreel-venv/bin:${PATH}"

WORKDIR /app
COPY rankreel/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt static-ffmpeg==3.0
RUN python - <<'PY'
import os
from static_ffmpeg.run import get_or_fetch_platform_executables_else_raise

for binary, name in zip(get_or_fetch_platform_executables_else_raise(), ("ffmpeg", "ffprobe")):
    os.chmod(binary, 0o755)
    os.symlink(binary, f"/usr/local/bin/{name}")
PY
RUN ffmpeg -version && ffprobe -version

COPY --chown=rankreel:rankreel rankreel/ ./
RUN mkdir -p cache projects output \
    && chown rankreel:rankreel cache projects output

USER rankreel

# Node is needed only for the editor regression tests.
FROM app AS test
USER root
RUN dnf -y --setopt=install_weak_deps=False install nodejs \
    && dnf clean all
USER rankreel
CMD ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider"]

FROM app AS runtime
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/sfx', timeout=3).read()"]
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8765", "--log-level", "warning"]
