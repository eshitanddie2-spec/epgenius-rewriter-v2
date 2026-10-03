FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY epgenius_rewriter.py .

RUN mkdir -p /data /work

EXPOSE 8080

CMD ["python3", "/app/epgenius_rewriter.py"]
