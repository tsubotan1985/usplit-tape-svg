FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000 \
    DATA_DIR=/app/data

WORKDIR /app
COPY app ./app
RUN mkdir -p /app/data/jobs /app/data/out

EXPOSE 8000
CMD ["python", "-m", "app.server"]
