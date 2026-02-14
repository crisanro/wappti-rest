from core.database import Base

from .calendar import (Appointment, CalendarNote)

from .customer import (
    Customer,
    CustomerTag,
    CustomerHistory,
    CustomerFeedback, CustomerPlanItem, CustomerPlan, CustomerDebt, CustomerPayment
)

from .establishments import (
    Establishment,
    Profile,
    AppAccessPin,
    EstablishmentCredit,
    EstablishmentReview,
    AppAccessPin,
    WhatsAppAuthPin
)

from .finance import (
    Payment,
    ReferralWithdrawal,
    ReferralCode,
    ReferralBalance,
    ReferralPayoutMethod
)

# 2. Comunicaciones y WhatsApp (communications.py)
from .marketing import (
    WhatsAppCampaign, 
    WhatsAppDispatch, 
    WhatsAppSession, 
    WhatsAppError, 
    AppNotification, 
    AppAd
)

from .system import (
    SystemAudit,
    UsageAuditLog,
    SystemAlert,
    UserSuggestion,
    FAQ,
    TutorialLink,
    PendingFollowup,
    GrowthTip, 
    SystemBlockedIP
)

from .kipu import (CustomerBillingProfile)

# Metadata centralizada para migraciones de Alembic
metadata = Base.metadata