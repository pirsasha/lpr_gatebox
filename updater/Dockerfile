FROM python:3.11-slim

WORKDIR /app

# NEW: docker-compose (v1) + docker-cli + zip
RUN apt-get update && apt-get install -y docker.io docker-compose zip && rm -rf /var/lib/apt/lists/*

COPY updater/app.py /app/app.py

EXPOSE 9010
CMD ["python", "/app/app.py"]