import base64

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import models, schemas
from app.connector_client import normalize_connector_type, validate_connector_platform
from app.database import Base
from app.main import app, delete_user, hash_password, seed_defaults, update_user, validate_profile_photo
from app.service_status import display_name, unavailable_statuses


def test_about():
    client = TestClient(app)
    response = client.get("/api/about")
    assert response.status_code == 200
    assert response.json()["product"] == "DS Shift"


def test_profile_photo_validation():
    png = b"\x89PNG\r\n\x1a\n" + b"profile-photo"
    encoded = base64.b64encode(png).decode()
    photo = f"data:image/png;base64,{encoded}"

    assert validate_profile_photo(photo) == photo

    with pytest.raises(HTTPException, match="does not match"):
        validate_profile_photo(f"data:image/png;base64,{base64.b64encode(b'not-an-image').decode()}")


def test_seed_defaults_rebrands_existing_settings():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        db.add(models.AppSetting(product_name="DS Replace"))
        db.commit()

        seed_defaults(db)

        assert db.query(models.AppSetting).one().product_name == "DS Shift"


def test_connector_platform_validation_and_aliases():
    assert normalize_connector_type("AWS") == "Amazon Web Services"
    assert validate_connector_platform("cloud", "Azure") == "Microsoft Azure"
    assert validate_connector_platform("host", "Nutanix AHV") == "Nutanix AHV"

    with pytest.raises(ValueError, match="Unsupported cloud connector type"):
        validate_connector_platform("cloud", "Other Cloud")


def test_admin_cannot_delete_or_deactivate_self():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        admin = models.LocalUser(
            username="admin",
            password_hash=hash_password("password"),
            role="admin",
            is_active="true",
        )
        db.add(admin)
        db.commit()
        db.refresh(admin)

        with pytest.raises(HTTPException, match="delete your own"):
            delete_user(admin.id, db, admin)

        with pytest.raises(HTTPException, match="demote or deactivate"):
            update_user(admin.id, schemas.UserUpdate(is_active=False), db, admin)


def test_service_status_fallback():
    result = unavailable_statuses("monitor unavailable")

    assert display_name("cloud-connector-engine") == "Cloud-Connector-Engine"
    assert [row["service"] for row in result["services"]] == [
        "backend",
        "cloud-connector-engine",
        "database",
        "frontend",
        "host-connector-engine",
        "reverse-proxy",
    ]
    assert all(row["status"] == "DOWN" for row in result["services"])
