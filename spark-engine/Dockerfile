FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt
COPY spark_app.py .

EXPOSE 8200
CMD ["python", "-m", "uvicorn", "spark_app:app", "--host", "0.0.0.0", "--port", "8200"]
