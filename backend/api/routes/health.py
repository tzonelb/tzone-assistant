"""Liveness, and nothing else.

This endpoint returned a constant and still does — but deliberately now rather
than by omission. A liveness probe that checks its dependencies restarts the
process when a database is slow, which is precisely when restarting helps least:
the new process finds the same slow database, and the restart loop becomes the
outage.

What the platform can actually *do* right now is a different question with a
different audience, and it lives behind the console at
`GET /api/platform/health/report`. It opens every company database, runs
SQLite's own integrity check over each one and reads the host's memory and disk
— none of which belongs on an unauthenticated URL a load balancer polls every
few seconds.
"""

from fastapi import APIRouter

from backend.services.health_service import health_service


router = APIRouter(prefix="/health", tags=["Health"])


@router.get("/")
def health_check():
    return health_service.liveness()
