FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HC_SHARE_ENABLED=1 \
    HC_SHARE_TTL=600 \
    HC_PUBLIC_URL="" \
    HC_FORWARDED_ALLOW_IPS="127.0.0.1"

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && useradd --system --uid 10001 --no-create-home hc

COPY app ./app
COPY catalog ./catalog

USER hc
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/healthz')" || exit 1

# One worker on purpose: temporary curl links live in this process's memory.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port 8080 --workers 1 --proxy-headers --forwarded-allow-ips \"$HC_FORWARDED_ALLOW_IPS\""]
