FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY epgenius_rewriter.py .

RUN mkdir -p /data /work

EXPOSE 8080

ENV PYTHONUNBUFFERED=1

CMD ["python", "/app/epgenius_rewriter.py"]
