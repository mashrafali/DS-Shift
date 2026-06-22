import httpx

from .config import settings


def spark_capabilities() -> dict:
    with httpx.Client(timeout=10) as client:
        response = client.get(f"{settings.spark_engine_url}/capabilities")
        response.raise_for_status()
        return response.json()


def create_spark_job(payload: dict) -> dict:
    with httpx.Client(timeout=30) as client:
        response = client.post(f"{settings.spark_engine_url}/jobs", json=payload)
        if response.status_code >= 400:
            raise ValueError(response.text)
        return response.json()


def preflight_spark_job(payload: dict) -> dict:
    with httpx.Client(timeout=120) as client:
        response = client.post(f"{settings.spark_engine_url}/preflight", json=payload)
        if response.status_code >= 400:
            raise ValueError(response.text)
        return response.json()


def get_spark_job(job_id: int) -> dict:
    with httpx.Client(timeout=10) as client:
        response = client.get(f"{settings.spark_engine_url}/jobs/{job_id}")
        response.raise_for_status()
        return response.json()


def cancel_spark_job(job_id: int) -> dict:
    with httpx.Client(timeout=20) as client:
        response = client.post(f"{settings.spark_engine_url}/jobs/{job_id}/cancel")
        if response.status_code >= 400:
            raise ValueError(response.text)
        return response.json()
