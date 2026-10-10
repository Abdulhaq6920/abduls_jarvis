"""Device (local agent) credential management. Admin only."""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException

from jarvis_backend.api.deps import get_container
from jarvis_backend.auth.deps import admin_only
from jarvis_backend.auth.service import Principal
from jarvis_backend.container import Container
from jarvis_backend.schemas_auth import DeviceCreate, DeviceCreated, DeviceOut

router = APIRouter(prefix="/api/devices", tags=["devices"])


@router.get("", response_model=List[DeviceOut])
async def list_devices(_: Principal = admin_only,
                       c: Container = Depends(get_container)) -> list:
    return [DeviceOut.model_validate(d) for d in await c.auth.devices.list()]


@router.post("", response_model=DeviceCreated, status_code=201)
async def create_device(body: DeviceCreate, principal: Principal = admin_only,
                        c: Container = Depends(get_container)) -> DeviceCreated:
    await c.limiter.enforce(f"device-create:{principal.subject}", limit=10, window=3600)
    row, secret = await c.auth.devices.create(body.name, created_by=principal.subject)
    await c.audit.record("device_created", device_id=row.id, name=row.name, by=principal.subject)
    return DeviceCreated(**DeviceOut.model_validate(row).model_dump(), secret=secret)


@router.delete("/{device_id}", status_code=204)
async def revoke_device(device_id: str, principal: Principal = admin_only,
                        c: Container = Depends(get_container)) -> None:
    if not await c.auth.revoke_device(device_id, by=principal.subject):
        raise HTTPException(404, "Device not found or already revoked.")
