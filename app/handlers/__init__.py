"""Handlers package."""

from .skills import router as skills_router
from .start import router as start_router
from .subscription import router as subscription_router
from .test_lead import router as test_lead_router
from .plan import router as plan_router
from .settings import router as settings_router
from .my_id import router as my_id_router
from .upgrade_request import router as upgrade_request_router

__all__ = [
    "start_router",
    "skills_router",
    "test_lead_router",
    "subscription_router",
    "plan_router",
    "settings_router",
    "my_id_router",
    "upgrade_request_router",
]
