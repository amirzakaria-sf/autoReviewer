FROM python:3.12-slim

# uv, not pip -- a real dependency resolver with a persistent, content-
# addressable cache (this stage's own layer cache still short-circuits an
# unchanged pyproject.toml either way, but a rebuild AFTER a real dependency
# change resolves and installs meaningfully faster with uv than pip). The
# static-binary COPY-from-image pattern is uv's own documented Docker
# approach -- no curl/install-script, no extra apt package.
COPY --from=ghcr.io/astral-sh/uv:0.9.7 /uv /usr/local/bin/uv

WORKDIR /srv

# docker-runner.py shells out to `docker run` against the mounted host socket —
# the client binary has to actually be in this image for that to work; the
# socket mount alone is not enough.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl gnupg git \
    && install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc \
    && chmod a+r /etc/apt/keyrings/docker.asc \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian bookworm stable" > /etc/apt/sources.list.d/docker.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends docker-ce-cli docker-compose-plugin docker-buildx-plugin \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY app ./app

RUN uv pip install --system --no-cache . \
    && playwright install --with-deps chromium

EXPOSE 8300

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8300"]
