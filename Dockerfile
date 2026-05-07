FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && playwright install --with-deps chromium

COPY app ./app
COPY config ./config
COPY main.py kpnet_main.py energy_model_main.py cloud_job_runner.py db_pipeline_main.py sheets_export_main.py ./

ENTRYPOINT ["python", "cloud_job_runner.py"]
