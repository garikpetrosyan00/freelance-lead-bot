"""Handlers package."""

from .skills import router as skills_router
from .start import router as start_router
from .subscription import router as subscription_router
from .test_lead import router as test_lead_router
from .plan import router as plan_router
from .settings import router as settings_router
from .my_id import router as my_id_router
from .buy_pro import router as buy_pro_router
from .payment_status import router as payment_status_router
from .payment_admin import router as payment_admin_router
from .analytics_admin import router as analytics_admin_router
from .monitoring_admin import router as monitoring_admin_router
from .upgrade_request import router as upgrade_request_router

__all__ = [
    "start_router",
    "skills_router",
    "test_lead_router",
    "subscription_router",
    "plan_router",
    "settings_router",
    "my_id_router",
    "buy_pro_router",
    "payment_status_router",
    "payment_admin_router",
    "analytics_admin_router",
    "monitoring_admin_router",
    "upgrade_request_router",
]
