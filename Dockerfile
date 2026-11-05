# Kapacitet+ in one container: the API and the built interface on one port.
#
# The data are not in the image: they derive from OpenStreetMap (ODbL) and the code is MIT. Mount a
# data folder computed with `python -m kapacitet all`, or one filled by `python -m kapacitet unbundle`
# with the data release's kapacitet-data.zip, at /app/data:
#
#   docker build -t kapacitet .
#   docker run -p 127.0.0.1:8000:8000 -v "$PWD/data:/app/data" kapacitet
#
# The port is published on 127.0.0.1 only, because the API has no login: whoever reaches the port
# can use all of it, approvals included. Inside the container the server listens on 0.0.0.0, which
# is what Docker needs to forward the port; reaching it from another machine needs a reverse proxy
# with TLS and a login in front.
#
# The server runs as an unprivileged user (uid 10001) and writes only to /app/data. A folder mounted
# from the host must be writable by that uid, or run the container as yourself with
# `--user "$(id -u):$(id -g)"`; a named volume (`-v kapacitet-data:/app/data`) needs neither.
#
# Without data the interface opens on a setup page that says what to run.
#
# The base images are pinned by digest, with the tag kept beside it to say what the digest is;
# Dependabot (.github/dependabot.yml) proposes the next digest every week.

FROM node:24-slim@sha256:01d566e8d94d8c619591d78652babfccd3c6992a3dffa9ed5ab39e11cf6c70e9 AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.12-slim@sha256:e695ab189fa2d082b1c61c69a6b888a5adeca61ceeccad107b1ed2a125721a9e
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
# The code, the configuration and the interface stay root's, read-only to the server. Its user owns
# data/ alone, created here before VOLUME so that a new volume starts with that owner.
RUN groupadd --system --gid 10001 kapacitet \
 && useradd --system --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent \
      --shell /usr/sbin/nologin kapacitet \
 && mkdir -p /app/data \
 && chown 10001:10001 /app/data
VOLUME /app/data
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health')" || exit 1
CMD ["python", "-m", "kapacitet", "serve"]
