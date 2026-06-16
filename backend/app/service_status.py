import os

import httpx


STATUS_MONITOR_URL = os.getenv("SERVICE_STATUS_MONITOR_URL", "http://service-status-monitor:8090")
EXPECTED_SERVICES = [
    "backend",
    "cloud-connector",
    "database",
    "frontend",
    "host-connector",
    "launchgrid",
    "reverse-proxy",
    "spark-engine",
]


def display_name(service: str) -> str:
    if service == "launchgrid":
        return "LaunchGrid"
    return "-".join(part.capitalize() for part in service.split("-"))


def unavailable_statuses(message: str) -> dict:
    return {
        "services": [
            {
                "service": service,
                "name": display_name(service),
                "status": "DOWN",
                "container_state": "unknown",
                "detail": message,
            }
            for service in EXPECTED_SERVICES
        ],
        "monitor_error": message,
    }


def get_service_statuses() -> dict:
    try:
        with httpx.Client(timeout=5) as client:
            response = client.get(f"{STATUS_MONITOR_URL}/status")
            response.raise_for_status()
            return response.json()
    except Exception as exc:
        return unavailable_statuses(f"Service status monitor unavailable: {exc}")
