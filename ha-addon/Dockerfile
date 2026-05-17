FROM python:3.12-slim

WORKDIR /app

COPY app/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt \
    && (pip install --no-cache-dir curl_cffi || true)

COPY app /app
COPY run.sh /run.sh

RUN chmod +x /run.sh

CMD ["/run.sh"]
