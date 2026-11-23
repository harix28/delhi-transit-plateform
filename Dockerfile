FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY . .

# Generate demo data if processed data is not present
RUN python scripts/create_demo.py

ENV TRANSIT_CONFIG=config.demo.yaml
ENV PORT=8000
EXPOSE 8000

CMD ["sh", "-c", "uvicorn src.api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
