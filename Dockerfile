FROM python:3.11-slim

WORKDIR /app

RUN mkdir -p /app/data

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 5000 5001 5002 5003 5004 5005

ENV BLOCKCHAIN_HOST=0.0.0.0
ENV BLOCKCHAIN_DATA_DIR=/app/data
ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["python", "main.py"]
CMD ["-p", "5000"]
