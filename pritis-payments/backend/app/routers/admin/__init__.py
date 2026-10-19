"""Routes for the admin site (admin.dpp.pritis.name.ng)."""

from fastapi import APIRouter

from app.routers.admin import (
    audit_logs,
    auth,
    dues,
    payouts,
    refunds,
    rep_applications,
    reports,
    revenue,
    search,
    settings,
    settlements,
    staff,
    support,
)

router = APIRouter(prefix="/api/admin")
router.include_router(reports.router)
router.include_router(revenue.router)
router.include_router(search.router)
router.include_router(support.router)
router.include_router(payouts.router)
router.include_router(refunds.router)
router.include_router(settlements.router)
router.include_router(dues.router)
router.include_router(rep_applications.router)
router.include_router(auth.router)
router.include_router(auth.me_router)
router.include_router(staff.router)
router.include_router(settings.router)
router.include_router(audit_logs.router)
