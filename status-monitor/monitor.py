import json
import os
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote


DOCKER_SOCKET = os.getenv("DOCKER_SOCKET", "/var/run/docker.sock")
COMPOSE_PROJECT = os.getenv("COMPOSE_PROJECT_NAME", "ds-shift")
PORT = int(os.getenv("STATUS_MONITOR_PORT", "8090"))
HIDDEN_SERVICES = {"edge-gateway", "service-status-monitor"}
SERVICE_ORDER = [
    "backend",
    "cloud-connector",
    "database",
    "frontend",
    "host-connector",
    "reverse-proxy",
    "spark-engine",
]


def docker_get(path: str) -> list[dict]:
    request = f"GET {path} HTTP/1.1\r\nHost: docker\r\nConnection: close\r\n\r\n".encode()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(5)
        client.connect(DOCKER_SOCKET)
        client.sendall(request)
        response = b""
        while chunk := client.recv(65536):
            response += chunk

    headers, body = response.split(b"\r\n\r\n", 1)
    header_lines = headers.splitlines()
    status_line = header_lines[0].decode()
    if " 200 " not in status_line:
        raise RuntimeError(f"Docker API returned {status_line}")
    if any(line.lower() == b"transfer-encoding: chunked" for line in header_lines[1:]):
        body = decode_chunked(body)
    return json.loads(body)


def decode_chunked(body: bytes) -> bytes:
    decoded = bytearray()
    while body:
        size_line, body = body.split(b"\r\n", 1)
        size = int(size_line.split(b";", 1)[0], 16)
        if size == 0:
            break
        decoded.extend(body[:size])
        body = body[size + 2 :]
    return bytes(decoded)


def display_name(service: str) -> str:
    return "-".join(part.capitalize() for part in service.split("-"))


def public_status(state: str, detail: str = "") -> str:
    if "(unhealthy)" in detail.lower():
        return "DOWN"
    if state == "running":
        return "UP"
    if state == "restarting":
        return "RESTARTING"
    return "DOWN"


def service_statuses() -> list[dict]:
    filters = quote(json.dumps({"label": [f"com.docker.compose.project={COMPOSE_PROJECT}"]}))
    containers = docker_get(f"/containers/json?all=1&filters={filters}")
    by_service = {}
    for container in containers:
        labels = container.get("Labels") or {}
        service = labels.get("com.docker.compose.service")
        if not service or service in HIDDEN_SERVICES:
            continue
        by_service.setdefault(service, []).append(container)

    services = SERVICE_ORDER + sorted(set(by_service) - set(SERVICE_ORDER))
    result = []
    for service in services:
        replicas = by_service.get(service, [])
        if not replicas:
            result.append({
                "service": service,
                "name": display_name(service),
                "status": "DOWN",
                "container_state": "missing",
                "detail": "Container not found",
                "replicas": 0,
            })
            continue
        states = [container.get("State", "missing") for container in replicas]
        public_states = [
            public_status(container.get("State", "missing"), container.get("Status", ""))
            for container in replicas
        ]
        status = "DOWN" if "DOWN" in public_states else "RESTARTING" if "RESTARTING" in public_states else "UP"
        result.append({
            "service": service,
            "name": display_name(service),
            "status": status,
            "container_state": ",".join(sorted(set(states))),
            "detail": f"{sum(state == 'running' for state in states)}/{len(replicas)} replicas running",
            "replicas": len(replicas),
        })
    return result


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self.respond(200, {"status": "ok"})
            return
        if self.path == "/status":
            try:
                self.respond(200, {"services": service_statuses()})
            except Exception as exc:
                self.respond(503, {"detail": f"Unable to read Docker service status: {exc}"})
            return
        self.respond(404, {"detail": "Not found"})

    def respond(self, status: int, payload: dict):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
