ARG BASE_IMAGE=python@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea
FROM ${BASE_IMAGE}
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.lock.txt /app/requirements.lock.txt
COPY requirements.txt /app/requirements.txt
ARG PIP_OFFLINE=0
RUN PIP_NO_INDEX=$PIP_OFFLINE PIP_DISABLE_PIP_VERSION_CHECK=1 pip install --no-cache-dir -r requirements.txt
RUN mkdir -p /app/app
COPY app/__init__.py app/main.py app/api_proxy.py app/storage_contract.py /app/app/
COPY app/static /app/app/static
RUN mkdir -p /app/runtime /app/app/static && touch /app/app/static/model_params.json
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
