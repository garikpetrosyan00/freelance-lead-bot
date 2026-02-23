"""Handlers package."""

from .skills import router as skills_router
from .ui_skills_picker import router as skills_picker_router
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
from .ui_flow import router as ui_flow_router
from .ui_settings import router as ui_settings_router
from .support import router as support_router
from .upwork_alerts import router as upwork_alerts_router
from .upwork_api import router as upwork_api_router
from .upwork_oauth import router as upwork_oauth_router
from .upwork_profiles import router as upwork_profiles_router
from .upwork_status import router as upwork_status_router

__all__ = [
    "start_router",
    "skills_router",
    "skills_picker_router",
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
    "ui_flow_router",
    "ui_settings_router",
    "support_router",
    "upwork_alerts_router",
    "upwork_api_router",
    "upwork_oauth_router",
    "upwork_profiles_router",
    "upwork_status_router",
]
