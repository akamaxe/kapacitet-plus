# Kapacitet+ in one container: the API and the built interface on one port.
#
# The data are not in the image: they derive from OpenStreetMap (ODbL) and the code is MIT. Mount a
# data folder computed with `python -m kapacitet all`, or one filled by `python -m kapacitet unbundle`
# with the data release's kapacitet-data.zip, at /app/data:
#
#   docker build -t kapacitet .
#   mkdir -p data
#   docker run --user "$(id -u):$(id -g)" -p 127.0.0.1:8000:8000 -e KAPACITET_AUTH=off -v "$PWD/data:/app/data" kapacitet
#
# or, with a named volume instead of a folder of the host:
#
#   docker run -p 127.0.0.1:8000:8000 -e KAPACITET_AUTH=off -v kapacitet-data:/app/data kapacitet
#
# Inside the container the server listens on 0.0.0.0, which is what Docker needs to forward the
# port, so it starts only when KAPACITET_AUTH says whether sign-in is wanted: `oidc` (docs/sso.md)
# or `off`. The examples run it without sign-in, and that is why the port is published on
# 127.0.0.1 only: whoever reaches the port can use all of the API, approvals included. Reaching it
# from another machine needs sign-in, or a reverse proxy with TLS and a login in front.
#
# The server runs as an unprivileged user (uid 10001) and writes only to /app/data. A folder of the
# host keeps its owner there, so the first example runs the container as yourself (`--user`), on a
# folder made beforehand (Docker would make a missing one as root); without that the server reads
# the data but cannot save an evaluation, the queue or an application. A named volume starts owned
# by uid 10001 and needs neither. One written by an image from before uid 10001 is root's: the
# README says how to hand it over.
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

FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    KAPACITET_HOST=0.0.0.0 \
    KAPACITET_PORT=8000
COPY pyproject.toml constraints.txt README.md LICENSE ./
COPY src/ src/
# editable, so the package finds config/, web/dist and data/ beside it (paths.py)
RUN pip install --no-cache-dir -c constraints.txt -e ".[forecast,postgres,telemetry,timestamp]"
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
