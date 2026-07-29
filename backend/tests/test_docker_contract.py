from __future__ import annotations

from pathlib import Path

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DOCKER_ROOT = REPOSITORY_ROOT / "docker"


def test_compose_keeps_data_private_and_binds_cors_to_public_origin():
    compose = yaml.safe_load((DOCKER_ROOT / "docker-compose.yml").read_text("utf-8"))
    services = compose["services"]

    assert "ports" not in services["db"]
    assert "ports" not in services["backend"]
    assert services["backend"]["environment"]["CORS_ORIGINS"].startswith(
        "${APP_ORIGIN:"
    )
    assert services["frontend"]["ports"] == ["127.0.0.1:${APP_PORT:-80}:80"]
    assert services["db"]["networks"] == ["private"]
    assert compose["networks"]["private"]["internal"] is True


def test_backend_image_installs_the_shared_ai_core_package():
    dockerfile = (DOCKER_ROOT / "Dockerfile.backend").read_text("utf-8")
    dockerignore = (DOCKER_ROOT / "Dockerfile.backend.dockerignore").read_text("utf-8")

    assert "COPY ai_core/" in dockerfile
    assert "pip install --no-deps /tmp/ai_core" in dockerfile
    assert "!ai_core/**" in dockerignore


def test_nginx_sets_the_locked_content_security_policy():
    nginx = (DOCKER_ROOT / "nginx.conf").read_text("utf-8")
    assert "client_max_body_size 11m;" in nginx
    assert "default-src 'self'" in nginx
    assert "frame-ancestors 'none'" in nginx
    assert "connect-src 'self'" in nginx


def test_nginx_rate_limits_auth_at_the_unspoofable_client_socket_boundary():
    nginx = (DOCKER_ROOT / "nginx.conf").read_text("utf-8")
    assert "limit_req_zone $binary_remote_addr zone=auth_register:" in nginx
    assert "limit_req_zone $binary_remote_addr zone=auth_login:" in nginx
    assert "location = /api/v1/auth/register" in nginx
    assert "limit_req zone=auth_register" in nginx
    assert "location = /api/v1/auth/login" in nginx
    assert "limit_req zone=auth_login" in nginx
    assert "limit_req_status 429;" in nginx
    assert "forwarded-allow-ips=*" not in nginx


def test_example_origin_tracks_non_default_public_port():
    example = (DOCKER_ROOT / ".env.example").read_text("utf-8")
    assert "APP_ORIGIN=http://localhost" in example
    assert "APP_PORT=80" in example
