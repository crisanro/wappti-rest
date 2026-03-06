import pytz
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, status, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, or_, text

# --- IMPORTANTE: Asegúrate de que Payment y WhatsAppCampaign estén en models.py ---
from models import Appointment, Establishment,ReferralWithdrawal,ReferralPayoutMethod, Customer, Payment, WhatsAppCampaign, UsageAuditLog, Profile, AppAccessPin, WhatsAppAuthPin
from core.database import get_db
from core.auth import verify_app_admin # Usamos el que valida tu UID de Firebase

router = APIRouter(prefix="/admin", tags=["Admin Dashboard"])

@router.get("/dashboard-stats")
async def get_admin_dashboard(
    db: Session = Depends(get_db),
    admin_user: dict = Depends(verify_app_admin)
):
    # Set timezone-aware current time
    now = datetime.now(pytz.UTC)

    # --- 1. REVENUE SECTION (Mirror Period Logic) ---
    def get_income_metrics(days):
        """
        Calculates revenue for the current period and compares it with the 
        previous mirror period using unit-based percentage logic.
        """
        # Define ranges
        current_start = now - timedelta(days=days)
        previous_start = current_start - timedelta(days=days)
        previous_end = current_start
        
        # Aggregate queries
        current_total = db.query(func.sum(Payment.amount)).filter(
            Payment.created_at >= current_start,
            Payment.is_refund == False
        ).scalar() or 0.0
        
        previous_total = db.query(func.sum(Payment.amount)).filter(
            Payment.created_at >= previous_start,
            Payment.created_at < previous_end,
            Payment.is_refund == False
        ).scalar() or 0.0
        
        # Growth Mathematics (Ratio vs Unit)
        growth_pct = 0.0
        if previous_total > 0:
            # (Current / Previous - 1) * 100
            ratio = current_total / previous_total
            growth_pct = (ratio - 1) * 100
        elif current_total > 0:
            growth_pct = 100.0  # Initial growth from zero
            
        return {
            "amount": round(current_total, 2), 
            "growth_percentage": round(growth_pct, 1) 
        }

    # --- 2. REMINDERS SECTION (Cumulative Counters) ---
    def get_reminder_metrics(days):
        start_date = now - timedelta(days=days)
        base_query = db.query(Appointment).filter(
            Appointment.created_at >= start_date,
            Appointment.whatsapp_id.isnot(None),
            Appointment.whatsapp_id != ""
        )
        total = base_query.count()
        failed = base_query.filter(Appointment.whatsapp_status == "failed").count()
        return {
            "total": total, 
            "delivered": total - failed, 
            "failed": failed
        }

    # --- 3. ACTIVE USERS (Last 15 days Activity across tables) ---
    activity_tables = [
        "appointments", "calendar_notes", "customers", 
        "payments", "system_audit", "whatsapp_campaigns"
    ]
    activity_threshold = now - timedelta(days=15)
    active_establishment_ids = set()

    for table in activity_tables:
        raw_sql = text(f"SELECT DISTINCT establishment_id FROM {table} WHERE created_at >= :threshold")
        try:
            results = db.execute(raw_sql, {"threshold": activity_threshold}).fetchall()
            for row in results:
                if row[0]: active_establishment_ids.add(row[0])
        except Exception:
            # Skip tables that might not fit the schema perfectly to prevent 500 errors
            continue

    # --- 4. NEW ESTABLISHMENTS (Last 7 days - Unique by Name) ---
    new_raw_list = db.query(Establishment.id, Establishment.name).filter(
        Establishment.created_at >= (now - timedelta(days=7))
    ).order_by(Establishment.created_at.desc()).all()
    
    unique_names = set()
    cleaned_new_establishments = []
    for est in new_raw_list:
        if est.name not in unique_names:
            cleaned_new_establishments.append({"id": est.id, "name": est.name})
            unique_names.add(est.name)

    # --- 5. LATEST 5 CAMPAIGNS ---
    latest_campaigns = db.query(WhatsAppCampaign).order_by(
        WhatsAppCampaign.created_at.desc()
    ).limit(5).all()

    # --- FINAL JSON RESPONSE ---
    return {
        "revenue_summary": {
            "monthly_30d": get_income_metrics(30),
            "quarterly_90d": get_income_metrics(90),
            "yearly_365d": get_income_metrics(365)
        },
        "reminders_performance": {
            "today": get_reminder_metrics(1),
            "weekly": get_reminder_metrics(7),
            "monthly": get_reminder_metrics(30)
        },
        "latest_campaigns": [
            {
                "id": c.id, 
                "name": c.name or "Unnamed Campaign", 
                "objective": c.description or "No objective defined", 
                "created_at": c.created_at, 
                "status": c.status
            } for c in latest_campaigns
        ],
        "wappti_community": {
            "active_establishments_15d": len(active_establishment_ids),
            "total_registered": db.query(Establishment).count(),
            "new_registrations_weekly": cleaned_new_establishments
        }
    }


@router.get("/establishments-list")
async def get_establishments_list(
    db: Session = Depends(get_db),
    admin_user: dict = Depends(verify_app_admin)
):
    now = datetime.now(pytz.UTC)
    thirty_days_ago = now - timedelta(days=30)

    # 1. We define only the tables that we CONFIRMED have 'establishment_id' and 'created_at'
    # Based on your schema, these are the reliable ones for business activity.
    valid_activity_tables = [
        "appointments", 
        "calendar_notes", 
        "customers", 
        "payments", 
        "profiles", 
        "system_audit",
        "whatsapp_campaigns"
    ]

    # 2. Build the UNION ALL query safely
    # We exclude 'referral_balances' because it uses 'referred_customer_id' instead of 'establishment_id'
    union_parts = [
        f"SELECT establishment_id, created_at FROM {table}" 
        for table in valid_activity_tables
    ]
    union_queries = " UNION ALL ".join(union_parts)
    
    last_activity_sql = text(f"""
        WITH all_activities AS ({union_queries})
        SELECT establishment_id, MAX(created_at) as last_date
        FROM all_activities
        WHERE establishment_id IS NOT NULL
        GROUP BY establishment_id
    """)

    try:
        # Execute the search for maximum activity dates
        activity_results = db.execute(last_activity_sql).fetchall()
        activity_map = {row.establishment_id: row.last_date for row in activity_results}
    except Exception as e:
        print(f"⚠️ Activity query failed: {e}")
        activity_map = {}

    # 3. Get active establishments ordered alphabetically by name
    # Filtering by is_deleted = False as requested
    establishments = db.query(Establishment).filter(
        Establishment.is_deleted == False
    ).order_by(Establishment.name.asc()).all()

    # 4. Process the list and calculate totals
    establishment_list = []
    active_last_30d_count = 0

    for est in establishments:
        # Resolve the most recent date: Map > last_use > created_at
        last_act = activity_map.get(est.id)
        final_activity_date = last_act or est.last_use or est.created_at

        # Check for activity in the last 30 days
        is_active_30d = False
        if final_activity_date:
            # Ensure the date is timezone-aware for safe comparison
            if final_activity_date.tzinfo is None:
                final_activity_date = final_activity_date.replace(tzinfo=pytz.UTC)
            
            if final_activity_date >= thirty_days_ago:
                active_last_30d_count += 1
                is_active_30d = True

        establishment_list.append({
            "id": est.id,
            "name": est.name,
            "email": est.email,
            "country": est.country,
            "whatsapp": est.whatsapp,
            "last_activity": final_activity_date,
            "is_active_30d": is_active_30d,
            "is_suspended": est.is_suspended
        })

    # 5. Final response
    return {
        "summary": {
            "total_establishments": len(establishments),
            "active_establishments_30d": active_last_30d_count
        },
        "establishments": establishment_list
    }

@router.get("/establishments/{establishment_id}")
async def get_establishment_detail(
    establishment_id: str,
    db: Session = Depends(get_db),
    admin_user: dict = Depends(verify_app_admin)
):
    # 1. BASE: Establishment info
    establishment = db.query(Establishment).filter(
        Establishment.id == establishment_id,
        Establishment.is_deleted == False
    ).first()

    if not establishment:
        raise HTTPException(status_code=404, detail="Establishment not found")

    # 2. USAGE METRICS (Credits balance logic)
    credits_added = db.query(func.sum(UsageAuditLog.value)).filter(
        UsageAuditLog.establishment_id == establishment_id,
        UsageAuditLog.condition == "top-up"
    ).scalar() or 0

    credits_spent = db.query(func.sum(UsageAuditLog.value)).filter(
        UsageAuditLog.establishment_id == establishment_id,
        UsageAuditLog.condition == "top-down"
    ).scalar() or 0

    # 3. APPOINTMENT STATS (WhatsApp Success vs Failed)
    sent_count = db.query(Appointment).filter(
        Appointment.establishment_id == establishment_id,
        Appointment.whatsapp_status.in_(["delivered", "read", "sent"])
    ).count()

    failed_count = db.query(Appointment).filter(
        Appointment.establishment_id == establishment_id,
        Appointment.whatsapp_status == "failed"
    ).count()

    # 4. STAFF & SECURITY (Profiles & PINs)
    profiles = db.query(Profile).filter(
        Profile.establishment_id == establishment_id
    ).order_by(Profile.name.asc()).all()

    # Access PIN (Assistant Mode)
    access_pin_record = db.query(AppAccessPin).filter(
        AppAccessPin.id == establishment_id
    ).first()

    # WhatsApp Auth Link (Technical diagnostics)
    ws_auth = db.query(WhatsAppAuthPin).filter(
        WhatsAppAuthPin.id == establishment_id
    ).first()

    # 5. FINANCIALS (Payments History)
    payments = db.query(Payment).filter(
        Payment.establishment_id == establishment_id
    ).order_by(Payment.created_at.desc()).all()
    
    total_revenue = sum(p.amount for p in payments if not p.is_refund)

    # --- RESPONSE STRUCTURE ---
    return {
        "basic_info": {
            "id": establishment.id,
            "name": establishment.name,
            "email": establishment.email,
            "country": establishment.country,
            "whatsapp": establishment.whatsapp,
            "created_at": establishment.created_at,
            "is_suspended": establishment.is_suspended,
            "available_credits": establishment.available_credits,
            "language": establishment.language,
            "signatures": {
                "message": establishment.message_signature,
                "virtual_assistant": establishment.virtual_assistant_signature,
                "header": establishment.header_signature
            }
        },
        "usage_metrics": {
            "credits_topped_up": credits_added,
            "credits_spent": credits_spent,
            "whatsapp_delivery": {
                "success": sent_count,
                "failed": failed_count,
                "total_attempts": sent_count + failed_count
            }
        },
        "security_access": {
            "app_access_pin": access_pin_record.pin if access_pin_record else None,
            "whatsapp_technical": {
                "current_pin": ws_auth.pin if ws_auth else None,
                "is_activated": ws_auth.is_activated if ws_auth else False,
                "associated_phone": ws_auth.associated_phone if ws_auth else None,
                "failed_attempts": ws_auth.validation_attempts if ws_auth else []
            }
        },
        "staff_profiles": [
            {"id": p.id, "name": p.name, "timezone": p.timezone} for p in profiles
        ],
        "financial_history": {
            "total_lifetime_value": round(total_revenue, 2),
            "transactions": [
                {
                    "id": p.id, 
                    "amount": p.amount, 
                    "date": p.created_at, 
                    "reason": p.reason,
                    "invoice_url": p.invoice_link,
                    "is_refund": p.is_refund
                } for p in payments
            ]
        }
    }