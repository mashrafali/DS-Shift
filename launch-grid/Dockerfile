FROM python:3.12-slim

ARG GOVC_VERSION=0.54.1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl tar \
    && curl -fsSL "https://github.com/vmware/govmomi/releases/download/v${GOVC_VERSION}/govc_Linux_x86_64.tar.gz" \
      | tar -xz -C /usr/local/bin govc \
    && govc version \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY launchgrid_app.py .

CMD ["uvicorn", "launchgrid_app:app", "--host", "0.0.0.0", "--port", "8300"]
