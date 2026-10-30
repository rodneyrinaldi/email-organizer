FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# LibreOffice (anexos office), Pango/HarfBuzz (WeasyPrint), fontes, readpst (PST)
RUN apt-get update && apt-get install -y --no-install-recommends \
        libreoffice-writer-nogui libreoffice-calc-nogui libreoffice-impress-nogui \
        libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 \
        fonts-dejavu-core fonts-liberation2 \
        pst-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
# dependências numa camada própria: mudar o código não baixa nada do PyPI de novo
RUN python -c "import tomllib; p = tomllib.load(open('pyproject.toml', 'rb'))['project']; \
print(*p['dependencies'], *p['optional-dependencies']['dev'], sep=chr(10))" > /tmp/req.txt \
    && pip install hatchling editables -r /tmp/req.txt
COPY README.md LICENSE THIRD_PARTY.md ./
COPY src ./src


FROM base AS dev
# código montado em /app pelo compose; instalação editável aponta para /app/src
RUN pip install --no-deps --no-build-isolation -e .
CMD ["pytest", "-q"]


FROM base AS app
RUN pip install --no-deps --no-build-isolation .
VOLUME ["/output", "/pst"]
EXPOSE 8080
CMD ["uvicorn", "email_organizer.web:app", "--host", "0.0.0.0", "--port", "8080"]
