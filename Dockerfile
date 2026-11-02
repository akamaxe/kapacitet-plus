# Kapacitet+ in one container: the API and the built interface on one port.
#
# The data are not in the image: they derive from OpenStreetMap (ODbL) and the code is MIT. Mount a
# data folder computed with `python -m kapacitet all`, or one filled by `python -m kapacitet unbundle`
# with the data release's kapacitet-data.zip, at /app/data:
#
#   docker build -t kapacitet .
#   docker run -p 8000:8000 -v "$PWD/data:/app/data" kapacitet
#
# Without data the interface opens on a setup page that says what to run.

FROM node:24-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    KAPACITET_HOST=0.0.0.0 \
    KAPACITET_PORT=8000
COPY pyproject.toml constraints.txt README.md LICENSE ./
COPY src/ src/
# editable, so the package finds config/, web/dist and data/ beside it (paths.py)
RUN pip install --no-cache-dir -c constraints.txt -e .
COPY config/ config/
COPY --from=web /web/dist web/dist
VOLUME /app/data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health')" || exit 1
CMD ["python", "-m", "kapacitet", "serve"]
