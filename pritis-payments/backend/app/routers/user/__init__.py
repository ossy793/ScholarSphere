"""Routes for the user site (dpp.pritis.name.ng)."""

from fastapi import APIRouter

from app.routers.user import auth, dues, me, payments, payouts, rep, support

router = APIRouter(prefix="/api/user")
router.include_router(payouts.router)
router.include_router(auth.router)
router.include_router(me.router)
router.include_router(rep.router)
router.include_router(dues.router)
router.include_router(payments.router)
router.include_router(support.router)
