FROM python:3.12-slim

WORKDIR /app
ENV PATH="/opt/venv/bin:$PATH"

RUN apt-get update \
    && apt-get install -y --no-install-recommends chromium ca-certificates fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

COPY app/requirements.txt /app/requirements.txt
RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir -r /app/requirements.txt \
    && (/opt/venv/bin/pip install --no-cache-dir curl_cffi || true)

COPY app /app
COPY run.sh /run.sh

RUN chmod +x /run.sh

CMD ["/run.sh"]
