FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LIBGUESTFS_BACKEND=direct \
    LIBGUESTFS_CACHEDIR=/var/tmp

ARG GOVC_VERSION=0.54.1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        libguestfs-tools \
        libvirt-clients \
        linux-image-amd64 \
        qemu-system-x86 \
        qemu-utils \
        tar \
        virt-v2v \
    && curl -fsSL "https://github.com/vmware/govmomi/releases/download/v${GOVC_VERSION}/govc_Linux_x86_64.tar.gz" \
      | tar -xz -C /usr/local/bin govc \
    && govc version \
    && virt-v2v --version \
    && libguestfs-test-tool --version \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt
COPY spark_app.py host_migrations.py ./

EXPOSE 8200
CMD ["python", "-m", "uvicorn", "spark_app:app", "--host", "0.0.0.0", "--port", "8200"]
