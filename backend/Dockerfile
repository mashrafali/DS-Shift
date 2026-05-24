FROM quay.io/centos/centos:stream9

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
RUN dnf -y --setopt=install_weak_deps=False --nodocs install python3.12 python3.12-pip openssh-clients qemu-img virt-v2v \
    && dnf clean all
RUN ln -sf /usr/bin/python3.12 /usr/local/bin/python \
    && ln -sf /usr/bin/pip3.12 /usr/local/bin/pip
COPY requirements.txt .
RUN python3.12 -m pip install --no-cache-dir -r requirements.txt

COPY app ./app
EXPOSE 8000
CMD ["python3.12", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
