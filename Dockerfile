FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends iproute2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY static/ ./static/

RUN mkdir -p /data
ENV KIDSAFE_DATA=/data
ENV KIDSAFE_DNS_PORT=53
ENV KIDSAFE_WEB_PORT=8080

EXPOSE 53/udp 53/tcp 8080/tcp

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
