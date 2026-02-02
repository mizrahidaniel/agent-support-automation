"""
Agent Support Automation - Customer Portal API
Handles API key management, usage stats, billing, and ticket creation.
"""

from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List
import sqlite3
import secrets
import hashlib
from datetime import datetime, timedelta
import os

app = FastAPI(title="Agent Support API", version="0.1.0")

# CORS for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DATABASE = "support.db"

# Models
class APIKeyRequest(BaseModel):
    customer_id: str
    name: Optional[str] = "Default Key"

class APIKeyRotateRequest(BaseModel):
    old_key: str

class TicketCreate(BaseModel):
    subject: str
    message: str
    category: str = "general"

class TicketResponse(BaseModel):
    id: int
    status: str
    message: str
    created_at: str

class UsageStats(BaseModel):
    today: int
    this_month: int
    all_time: int
    current_rate_limit: int
    rate_limit_reset: Optional[str]

class BillingHistory(BaseModel):
    invoice_id: str
    date: str
    amount: float
    status: str
    description: str

# Database setup
def init_db():
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    
    # API keys table
    c.execute('''
        CREATE TABLE IF NOT EXISTS api_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id TEXT NOT NULL,
            key_hash TEXT NOT NULL UNIQUE,
            name TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_used TIMESTAMP,
            revoked BOOLEAN DEFAULT 0
        )
    ''')
    
    # Usage tracking
    c.execute('''
        CREATE TABLE IF NOT EXISTS usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id TEXT NOT NULL,
            api_key_hash TEXT NOT NULL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            endpoint TEXT,
            success BOOLEAN DEFAULT 1
        )
    ''')
    
    # Support tickets
    c.execute('''
        CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id TEXT NOT NULL,
            subject TEXT NOT NULL,
            message TEXT NOT NULL,
            category TEXT,
            status TEXT DEFAULT 'open',
            ai_responded BOOLEAN DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            resolved_at TIMESTAMP
        )
    ''')
    
    # Ticket responses
    c.execute('''
        CREATE TABLE IF NOT EXISTS ticket_responses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id INTEGER NOT NULL,
            from_agent BOOLEAN DEFAULT 0,
            message TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (ticket_id) REFERENCES tickets(id)
        )
    ''')
    
    # Billing records (mock for MVP)
    c.execute('''
        CREATE TABLE IF NOT EXISTS billing (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id TEXT NOT NULL,
            invoice_id TEXT NOT NULL,
            amount REAL NOT NULL,
            status TEXT DEFAULT 'paid',
            description TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    conn.commit()
    conn.close()

init_db()

# Helper functions
def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()

def verify_customer(customer_id: str = Header(..., alias="X-Customer-ID")):
    if not customer_id:
        raise HTTPException(status_code=401, detail="Customer ID required")
    return customer_id

# API Routes

@app.post("/api/keys/create")
def create_api_key(request: APIKeyRequest):
    """Generate a new API key for customer"""
    key = f"sk_{secrets.token_urlsafe(32)}"
    key_hash = hash_api_key(key)
    
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    c.execute(
        "INSERT INTO api_keys (customer_id, key_hash, name) VALUES (?, ?, ?)",
        (request.customer_id, key_hash, request.name)
    )
    conn.commit()
    key_id = c.lastrowid
    conn.close()
    
    return {
        "key_id": key_id,
        "api_key": key,
        "name": request.name,
        "created_at": datetime.now().isoformat(),
        "warning": "Save this key - it won't be shown again"
    }

@app.post("/api/keys/rotate")
def rotate_api_key(request: APIKeyRotateRequest, customer_id: str = Depends(verify_customer)):
    """Rotate (replace) an existing API key"""
    old_hash = hash_api_key(request.old_key)
    
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    
    # Verify old key belongs to customer
    c.execute(
        "SELECT id FROM api_keys WHERE customer_id = ? AND key_hash = ? AND revoked = 0",
        (customer_id, old_hash)
    )
    result = c.fetchone()
    
    if not result:
        conn.close()
        raise HTTPException(status_code=404, detail="API key not found")
    
    old_key_id = result[0]
    
    # Revoke old key
    c.execute("UPDATE api_keys SET revoked = 1 WHERE id = ?", (old_key_id,))
    
    # Create new key
    new_key = f"sk_{secrets.token_urlsafe(32)}"
    new_hash = hash_api_key(new_key)
    c.execute(
        "INSERT INTO api_keys (customer_id, key_hash, name) VALUES (?, ?, ?)",
        (customer_id, new_hash, "Rotated Key")
    )
    conn.commit()
    new_key_id = c.lastrowid
    conn.close()
    
    return {
        "key_id": new_key_id,
        "api_key": new_key,
        "created_at": datetime.now().isoformat(),
        "warning": "Old key has been revoked"
    }

@app.get("/api/keys/list")
def list_api_keys(customer_id: str = Depends(verify_customer)):
    """List all API keys for customer (no actual keys shown)"""
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    c.execute(
        """SELECT id, name, created_at, last_used, revoked 
           FROM api_keys WHERE customer_id = ?
           ORDER BY created_at DESC""",
        (customer_id,)
    )
    keys = []
    for row in c.fetchall():
        keys.append({
            "key_id": row[0],
            "name": row[1],
            "created_at": row[2],
            "last_used": row[3],
            "revoked": bool(row[4])
        })
    conn.close()
    return {"keys": keys}

@app.delete("/api/keys/{key_id}")
def revoke_api_key(key_id: int, customer_id: str = Depends(verify_customer)):
    """Revoke an API key"""
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    c.execute(
        "UPDATE api_keys SET revoked = 1 WHERE id = ? AND customer_id = ?",
        (key_id, customer_id)
    )
    conn.commit()
    conn.close()
    return {"message": "API key revoked"}

@app.get("/api/usage/stats")
def get_usage_stats(customer_id: str = Depends(verify_customer)) -> UsageStats:
    """Get usage statistics for customer"""
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    
    # Today's usage
    c.execute(
        """SELECT COUNT(*) FROM usage 
           WHERE customer_id = ? AND DATE(timestamp) = DATE('now')""",
        (customer_id,)
    )
    today = c.fetchone()[0]
    
    # This month
    c.execute(
        """SELECT COUNT(*) FROM usage 
           WHERE customer_id = ? AND strftime('%Y-%m', timestamp) = strftime('%Y-%m', 'now')""",
        (customer_id,)
    )
    this_month = c.fetchone()[0]
    
    # All time
    c.execute("SELECT COUNT(*) FROM usage WHERE customer_id = ?", (customer_id,))
    all_time = c.fetchone()[0]
    
    conn.close()
    
    # Mock rate limiting (1000 requests/day for MVP)
    rate_limit = 1000
    reset_time = (datetime.now() + timedelta(days=1)).replace(hour=0, minute=0, second=0)
    
    return UsageStats(
        today=today,
        this_month=this_month,
        all_time=all_time,
        current_rate_limit=rate_limit - today,
        rate_limit_reset=reset_time.isoformat() if today > 0 else None
    )

@app.get("/api/billing/history")
def get_billing_history(customer_id: str = Depends(verify_customer)) -> List[BillingHistory]:
    """Get billing history for last 12 months"""
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    c.execute(
        """SELECT invoice_id, created_at, amount, status, description 
           FROM billing WHERE customer_id = ?
           ORDER BY created_at DESC LIMIT 12""",
        (customer_id,)
    )
    history = []
    for row in c.fetchall():
        history.append(BillingHistory(
            invoice_id=row[0],
            date=row[1],
            amount=row[2],
            status=row[3],
            description=row[4] or "API Usage"
        ))
    conn.close()
    return history

@app.post("/api/tickets/create")
def create_ticket(ticket: TicketCreate, customer_id: str = Depends(verify_customer)):
    """Create a support ticket"""
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    c.execute(
        """INSERT INTO tickets (customer_id, subject, message, category) 
           VALUES (?, ?, ?, ?)""",
        (customer_id, ticket.subject, ticket.message, ticket.category)
    )
    conn.commit()
    ticket_id = c.lastrowid
    
    # Auto-respond with AI (simple keyword matching for MVP)
    ai_response = generate_ai_response(ticket.subject, ticket.message, ticket.category)
    if ai_response:
        c.execute(
            """INSERT INTO ticket_responses (ticket_id, from_agent, message) 
               VALUES (?, ?, ?)""",
            (ticket_id, False, ai_response)
        )
        c.execute("UPDATE tickets SET ai_responded = 1 WHERE id = ?", (ticket_id,))
        conn.commit()
    
    conn.close()
    
    return {
        "ticket_id": ticket_id,
        "status": "open",
        "ai_responded": ai_response is not None,
        "message": "Ticket created successfully"
    }

@app.get("/api/tickets/list")
def list_tickets(customer_id: str = Depends(verify_customer)):
    """List all tickets for customer"""
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    c.execute(
        """SELECT id, subject, message, category, status, ai_responded, created_at 
           FROM tickets WHERE customer_id = ?
           ORDER BY created_at DESC""",
        (customer_id,)
    )
    tickets = []
    for row in c.fetchall():
        tickets.append({
            "id": row[0],
            "subject": row[1],
            "message": row[2],
            "category": row[3],
            "status": row[4],
            "ai_responded": bool(row[5]),
            "created_at": row[6]
        })
    conn.close()
    return {"tickets": tickets}

@app.get("/api/tickets/{ticket_id}/responses")
def get_ticket_responses(ticket_id: int, customer_id: str = Depends(verify_customer)):
    """Get all responses for a ticket"""
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    
    # Verify ticket belongs to customer
    c.execute("SELECT id FROM tickets WHERE id = ? AND customer_id = ?", (ticket_id, customer_id))
    if not c.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Ticket not found")
    
    c.execute(
        """SELECT from_agent, message, created_at 
           FROM ticket_responses WHERE ticket_id = ?
           ORDER BY created_at ASC""",
        (ticket_id,)
    )
    responses = []
    for row in c.fetchall():
        responses.append({
            "from_agent": bool(row[0]),
            "message": row[1],
            "created_at": row[2]
        })
    conn.close()
    return {"responses": responses}

# Simple AI responder (keyword matching for MVP)
def generate_ai_response(subject: str, message: str, category: str) -> Optional[str]:
    """Generate automated response based on keywords"""
    text = f"{subject} {message}".lower()
    
    # API key questions
    if "api key" in text or "reset key" in text:
        return (
            "To rotate your API key:\n"
            "1. Go to the 'API Keys' tab\n"
            "2. Click 'Rotate Key' next to your current key\n"
            "3. Save the new key immediately (it won't be shown again)\n\n"
            "Your old key will be revoked automatically."
        )
    
    # Usage questions
    if "usage" in text or "how many" in text or "calls" in text:
        return (
            "You can view your real-time usage statistics on the dashboard:\n"
            "- Today's requests\n"
            "- This month's total\n"
            "- All-time usage\n\n"
            "Visit the 'Usage' tab for detailed analytics."
        )
    
    # Billing questions
    if "bill" in text or "charge" in text or "invoice" in text:
        return (
            "Your billing history is available in the 'Billing' tab. You'll find:\n"
            "- All invoices (last 12 months)\n"
            "- Payment status\n"
            "- Usage breakdown\n\n"
            "For refunds or billing disputes, please reply to this ticket and we'll prioritize it."
        )
    
    # Rate limit questions
    if "429" in text or "rate limit" in text or "too many requests" in text:
        return (
            "You've hit your rate limit. Check the 'Usage' tab for:\n"
            "- Current limit remaining\n"
            "- Reset time (usually midnight UTC)\n\n"
            "To increase your limit, upgrade your plan or contact us for custom limits."
        )
    
    # Can't auto-respond, escalate to human
    return None

@app.get("/health")
def health():
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8001))
    uvicorn.run(app, host="0.0.0.0", port=port)
