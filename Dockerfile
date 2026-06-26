FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p static/uploads/ads static/uploads/channels static/uploads/logo data && \
    useradd -r -u 1001 appuser && \
    chown -R appuser /app

USER appuser

EXPOSE 8000

CMD ["gunicorn", "--worker-class", "eventlet", "-w", "1", "-b", "0.0.0.0:8000", "app:app"]
