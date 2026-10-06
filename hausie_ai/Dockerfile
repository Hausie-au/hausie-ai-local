FROM python:3.11-slim

ARG BUILD_VERSION=0.5.1
ARG BUILD_ARCH

LABEL \
  io.hass.version="${BUILD_VERSION}" \
  io.hass.type="app" \
  io.hass.arch="${BUILD_ARCH}"

ENV PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY app /app/hausie_ai/app
COPY run.sh /run.sh

RUN chmod +x /run.sh

CMD ["/bin/sh", "/run.sh"]

