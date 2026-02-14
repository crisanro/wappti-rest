from sqlalchemy.orm import Session
from sqlalchemy import func
from models import SystemAudit, Establishment, SystemBlockedIP # Asegúrate de tener BlockedIP en tus modelos
from fastapi import Request
from datetime import datetime, timezone, timedelta

def register_action_log(
    db: Session, 
    establishment_id: str, 
    action: str, 
    method: str, 
    path: str, 
    payload: dict = None, 
    request: Request = None,
    status_code: int = 200
):
    """
    Registers an action in system_audit, updates establishment heartbeat (last_use),
    and detects potential DDoS abuse to blacklist IPs.
    """
    # 1. Secure IP identification
    client_ip = "0.0.0.0"
    if request:
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            client_ip = forwarded.split(",")[0]
        else:
            client_ip = request.client.host if request.client else "0.0.0.0"

    try:
        # 2. Create the audit log record
        new_log = SystemAudit(
            establishment_id=establishment_id,
            action=action,
            method=method,
            path=path,
            payload=payload if payload else {},
            ip=client_ip,
            status_code=status_code
        )
        db.add(new_log)

        # 3. ESTABLISHMENT HEARTBEAT
        # Update last_use column with the current UTC time
        db.query(Establishment).filter(Establishment.id == establishment_id).update({
            "last_use": datetime.now(timezone.utc)
        })

        # 4. ABUSE DETECTION (DDoS Mitigation)
        # Check requests from this IP in the last 60 seconds
        one_minute_ago = datetime.now(timezone.utc) - timedelta(minutes=1)
        
        request_count = db.query(SystemAudit).filter(
            SystemAudit.ip == client_ip,
            SystemAudit.created_at >= one_minute_ago
        ).count()

        if request_count > 20: # Adjust threshold as needed
            # Option A: Adding to the DB-based Blacklist (Persistent)
            already_blocked = db.query(SystemBlockedIP).filter(SystemBlockedIP.ip_address == client_ip).first()
            if not already_blocked:
                db.add(SystemBlockedIP(
                    ip_address=client_ip, 
                    reason=f"Abuse detected: {request_count} req/min"
                ))
            
            # Option B: Adding to the memory-based set (Instant)
            # from main import blacklisted_ips
            # blacklisted_ips.add(client_ip)
            
            print(f"⚠️ IP AUTO-BLOCKED: {client_ip} ({request_count} req/min)")

        # 5. Single commit for all operations (Atomic transaction)
        db.commit()

    except Exception as e:
        db.rollback()
        # Log error to console without breaking the user experience
        print(f"❌ Critical Audit/Security Error: {str(e)}")