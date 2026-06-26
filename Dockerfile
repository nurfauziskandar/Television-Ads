FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p static/uploads/ads static/uploads/channels static/uploads/logo data

EXPOSE 8000

CMD ["python", "app.py"]
