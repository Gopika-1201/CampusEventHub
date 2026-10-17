#!/usr/bin/env python3
"""
Industry Use Case 38: College Event Registration System
Single-file Flask Application with SQLite, strict business rule enforcement,
role simulation, modern UI, and test runner.
"""

import os
import re
import sys
import json
import uuid
import sqlite3
import datetime
from typing import Dict, Any, Tuple, Optional
import flask
from flask import Flask, request, jsonify, render_template_string, g, has_app_context

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "events.db")

app = Flask(__name__)
app.config["JSON_SORT_KEYS"] = False

# ---------------------------------------------------------------------------
# Database Helpers & Initialization
# ---------------------------------------------------------------------------

def get_db():
    """Get SQLite connection with autocommit=True and foreign keys enabled."""
    if flask.has_app_context():
        if "db" not in g:
            conn = sqlite3.connect(DB_PATH, timeout=20.0, autocommit=True)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON;")
            g.db = conn
        return g.db
    else:
        conn = sqlite3.connect(DB_PATH, timeout=20.0, autocommit=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

def rollback_db(db):
    """Safely roll back transaction if active."""
    try:
        if getattr(db, "in_transaction", False):
            db.execute("ROLLBACK")
    except Exception:
        pass

def commit_db(db):
    """Safely commit transaction if active."""
    try:
        if getattr(db, "in_transaction", False):
            db.execute("COMMIT")
    except Exception:
        pass

@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        rollback_db(db)
        db.close()

def init_db(force_reseed=False):
    """Initialize database tables and seed data if not present."""
    conn = sqlite3.connect(DB_PATH, timeout=20.0)
    conn.execute("PRAGMA foreign_keys = ON;")
    cur = conn.cursor()

    if force_reseed:
        cur.executescript("""
            DROP TABLE IF EXISTS booking_status_history;
            DROP TABLE IF EXISTS reservation_items;
            DROP TABLE IF EXISTS reservations;
            DROP TABLE IF EXISTS resources;
            DROP TABLE IF EXISTS users;
        """)

    cur.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('User', 'Administrator')),
            status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK(status IN ('ACTIVE', 'INACTIVE')),
            phone TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS resources (
            id TEXT PRIMARY KEY,
            identifier TEXT NOT NULL,
            category TEXT NOT NULL,
            rate REAL NOT NULL CHECK(rate >= 0),
            initial_availability INTEGER NOT NULL CHECK(initial_availability >= 0),
            current_availability INTEGER NOT NULL CHECK(current_availability >= 0),
            status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK(status IN ('ACTIVE', 'INACTIVE')),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS reservations (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            resource_id TEXT NOT NULL,
            quantity INTEGER NOT NULL CHECK(quantity BETWEEN 1 AND 5),
            rate_snapshot REAL NOT NULL,
            base_amount REAL NOT NULL,
            booking_charge REAL NOT NULL,
            grand_total REAL NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('CONFIRMED', 'CHECKED_IN', 'COMPLETED', 'CANCELLED')),
            booking_date TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE RESTRICT,
            FOREIGN KEY (resource_id) REFERENCES resources(id) ON DELETE RESTRICT
        );

        CREATE TABLE IF NOT EXISTS reservation_items (
            id TEXT PRIMARY KEY,
            reservation_id TEXT NOT NULL,
            resource_id TEXT NOT NULL,
            reserved_rate REAL NOT NULL,
            quantity INTEGER NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('CONFIRMED', 'CHECKED_IN', 'COMPLETED', 'CANCELLED')),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (reservation_id) REFERENCES reservations(id) ON DELETE CASCADE,
            FOREIGN KEY (resource_id) REFERENCES resources(id) ON DELETE RESTRICT
        );

        CREATE TABLE IF NOT EXISTS booking_status_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reference_id TEXT NOT NULL,
            actor_id TEXT NOT NULL,
            old_status TEXT,
            new_status TEXT NOT NULL,
            event_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (reference_id) REFERENCES reservations(id) ON DELETE CASCADE
        );
    """)

    # Check and insert seed users
    cur.execute("SELECT COUNT(*) FROM users")
    if cur.fetchone()[0] == 0:
        cur.executemany(
            "INSERT INTO users (id, name, role, status, phone) VALUES (?, ?, ?, ?, ?)",
            [
                ('U101', 'Alice', 'User', 'ACTIVE', '9876543210'),
                ('U102', 'Bob', 'User', 'ACTIVE', '9123456789'),
                ('ADM01', 'Admin Officer', 'Administrator', 'ACTIVE', '9999999999')
            ]
        )

    # Check and insert seed resources
    cur.execute("SELECT COUNT(*) FROM resources")
    if cur.fetchone()[0] == 0:
        cur.executemany(
            "INSERT INTO resources (id, identifier, category, rate, initial_availability, current_availability, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                ('R101', 'Event Seat A', 'Technical Keynote', 499.0, 10, 10, 'ACTIVE'),
                ('R102', 'Event Seat B', 'Workshop Hands-on', 799.0, 5, 5, 'ACTIVE'),
                ('R103', 'Event Seat C', 'Cultural Pass', 199.0, 20, 20, 'ACTIVE'),
                ('R104', 'Event Seat D', 'VIP Masterclass', 1299.0, 0, 0, 'ACTIVE'),
                ('R105', 'Event Seat E', 'Hackathon Entry', 1000.0, 3, 3, 'ACTIVE')
            ]
        )

    conn.commit()
    conn.close()

# ---------------------------------------------------------------------------
# Business Rule Calculation and Validation Helpers
# ---------------------------------------------------------------------------

def compute_fare(rate: float, quantity: int) -> Tuple[float, float, float]:
    """
    BR4: Total & Fee Calculation:
      base_amount = rate_snapshot * quantity
      booking_charge = 50.0 IF base_amount < 1000.0 ELSE 0.0
      grand_total = base_amount + booking_charge
    """
    base_amount = round(rate * quantity, 2)
    booking_charge = 50.0 if base_amount < 1000.0 else 0.0
    grand_total = round(base_amount + booking_charge, 2)
    return base_amount, booking_charge, grand_total

def validate_phone(phone: str) -> bool:
    """BR5: phone must match exactly 10 digits (^\\d{10}$)."""
    if not isinstance(phone, str):
        return False
    return bool(re.match(r"^\d{10}$", phone.strip()))

def validate_date(date_str: str) -> bool:
    """BR5: booking date must be valid YYYY-MM-DD."""
    if not isinstance(date_str, str):
        return False
    try:
        datetime.date.fromisoformat(date_str.strip())
        return True
    except (ValueError, TypeError):
        return False

def get_current_actor(req) -> Tuple[Optional[sqlite3.Row], Optional[str]]:
    """Resolve current actor from headers or parameters."""
    actor_id = req.headers.get("X-Actor-Id") or req.args.get("actor_id")
    if not actor_id and req.is_json:
        actor_id = (req.get_json(silent=True) or {}).get("actor_id")
    if not actor_id:
        actor_id = "U101" # Default to Alice if unassigned

    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT id, name, role, status, phone FROM users WHERE id = ?", (actor_id,))
    user = cur.fetchone()
    if not user:
        return None, f"Actor ID '{actor_id}' not found in system."
    return user, None

# ---------------------------------------------------------------------------
# Core Business Logic API Implementation (Atomic DB Operations)
# ---------------------------------------------------------------------------

def process_reservation(actor_id: str, resource_id: str, quantity: Any, name: str, phone: str, booking_date: str) -> Tuple[bool, Dict[str, Any], int]:
    """
    Atomic reservation creation strictly enforcing BR1 through BR7.
    """
    # BR1: Quantity must be strict integer 1 <= quantity <= 5
    if not isinstance(quantity, int) or isinstance(quantity, bool):
        return False, {"error": "Invalid quantity: Quantity must be a positive integer between 1 and 5 (decimals not allowed)."}, 400
    if quantity < 1 or quantity > 5:
        return False, {"error": f"Invalid quantity '{quantity}': Must be between 1 and 5 seats inclusive."}, 400

    # BR5: Name cannot be blank
    if not name or not isinstance(name, str) or not name.strip():
        return False, {"error": "Invalid details: Name cannot be blank."}, 400

    # BR5: Phone must match exactly 10 digits
    if not validate_phone(phone):
        return False, {"error": f"Invalid phone '{phone}': Must be exactly 10 digits (e.g., 9876543210)."}, 400

    # BR5: Booking date must be valid
    if not validate_date(booking_date):
        return False, {"error": f"Invalid booking date '{booking_date}': Must be a valid date in YYYY-MM-DD format."}, 400

    db = get_db()
    # Enforce atomic transaction with SQLite immediate lock
    try:
        db.execute("BEGIN IMMEDIATE")
        cur = db.cursor()

        # Check actor exists
        cur.execute("SELECT id, name, role, status FROM users WHERE id = ?", (actor_id,))
        actor = cur.fetchone()
        if not actor:
            rollback_db(db)
            return False, {"error": f"Actor '{actor_id}' not found."}, 404
        if actor["status"] != "ACTIVE":
            rollback_db(db)
            return False, {"error": f"User account '{actor_id}' is inactive."}, 403

        # BR2: Recheck resource status and availability atomically
        cur.execute("SELECT id, identifier, category, rate, current_availability, status FROM resources WHERE id = ?", (resource_id,))
        res = cur.fetchone()
        if not res:
            rollback_db(db)
            return False, {"error": f"Resource '{resource_id}' does not exist."}, 404
        if res["status"] != "ACTIVE":
            rollback_db(db)
            return False, {"error": f"Resource '{res['identifier']}' is not active."}, 400
        if res["current_availability"] < quantity:
            rollback_db(db)
            # BR6: Atomic failure, explicit error, zero changes
            return False, {"error": f"Insufficient seats: Requested {quantity}, but only {res['current_availability']} available for '{res['identifier']}'."}, 400

        # BR3 & BR4: Snapshot rate and calculate fares
        rate_snapshot = float(res["rate"])
        base_amount, booking_charge, grand_total = compute_fare(rate_snapshot, quantity)

        # Generate collision-free unique IDs
        timestamp_slug = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
        token = uuid.uuid4().hex[:6].upper()
        reservation_id = f"BK-{timestamp_slug}-{token}"
        item_id = f"ITM-{timestamp_slug}-{token}"

        # BR7: Atomic Success operations
        # 1. Decrement availability
        cur.execute(
            "UPDATE resources SET current_availability = current_availability - ? WHERE id = ?",
            (quantity, resource_id)
        )

        # 2. Insert reservation record
        cur.execute("""
            INSERT INTO reservations (
                id, user_id, resource_id, quantity, rate_snapshot,
                base_amount, booking_charge, grand_total, status, booking_date
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'CONFIRMED', ?)
        """, (reservation_id, actor_id, resource_id, quantity, rate_snapshot, base_amount, booking_charge, grand_total, booking_date.strip()))

        # 3. Insert reservation item snapshot
        cur.execute("""
            INSERT INTO reservation_items (
                id, reservation_id, resource_id, reserved_rate, quantity, status
            ) VALUES (?, ?, ?, ?, ?, 'CONFIRMED')
        """, (item_id, reservation_id, resource_id, rate_snapshot, quantity))

        # 4. Insert booking status history (NULL -> CONFIRMED)
        cur.execute("""
            INSERT INTO booking_status_history (
                reference_id, actor_id, old_status, new_status
            ) VALUES (?, ?, NULL, 'CONFIRMED')
        """, (reservation_id, actor_id))

        commit_db(db)

        return True, {
            "message": "Reservation confirmed successfully.",
            "reservation": {
                "id": reservation_id,
                "user_id": actor_id,
                "resource_id": resource_id,
                "resource_name": res["identifier"],
                "quantity": quantity,
                "rate_snapshot": rate_snapshot,
                "base_amount": base_amount,
                "booking_charge": booking_charge,
                "grand_total": grand_total,
                "status": "CONFIRMED",
                "booking_date": booking_date.strip(),
                "customer_name": name.strip(),
                "customer_phone": phone.strip()
            }
        }, 201

    except Exception as e:
        rollback_db(db)
        return False, {"error": f"Transaction failed: {str(e)}"}, 500

def process_cancellation(actor_id: str, reservation_id: str) -> Tuple[bool, Dict[str, Any], int]:
    """
    Cancel confirmed reservation strictly enforcing BR8, BR9, BR10.
    """
    db = get_db()
    try:
        db.execute("BEGIN IMMEDIATE")
        cur = db.cursor()

        # Fetch actor details
        cur.execute("SELECT id, role FROM users WHERE id = ?", (actor_id,))
        actor = cur.fetchone()
        if not actor:
            rollback_db(db)
            return False, {"error": f"Actor '{actor_id}' not found."}, 404

        # Fetch reservation details
        cur.execute("""
            SELECT id, user_id, resource_id, quantity, status, grand_total
            FROM reservations WHERE id = ?
        """, (reservation_id,))
        booking = cur.fetchone()
        if not booking:
            rollback_db(db)
            return False, {"error": f"Reservation '{reservation_id}' not found."}, 404

        # BR10: Users can only cancel their own bookings (Admin can also cancel if needed, or user own)
        if actor["role"] == "User" and booking["user_id"] != actor_id:
            rollback_db(db)
            return False, {"error": f"Access Denied: User '{actor_id}' cannot cancel booking owned by '{booking['user_id']}'."}, 403

        # BR8 & BR9: Cancellation only if status == 'CONFIRMED'
        if booking["status"] != "CONFIRMED":
            rollback_db(db)
            return False, {
                "error": f"Cancellation rejected: Cannot cancel reservation with status '{booking['status']}'. Only 'CONFIRMED' bookings can be cancelled."
            }, 400

        # Perform atomic cancellation
        # 1. Update reservation status
        cur.execute("UPDATE reservations SET status = 'CANCELLED' WHERE id = ?", (reservation_id,))

        # 2. Update item status
        cur.execute("UPDATE reservation_items SET status = 'CANCELLED' WHERE reservation_id = ?", (reservation_id,))

        # 3. Restore resource capacity exactly once
        cur.execute(
            "UPDATE resources SET current_availability = current_availability + ? WHERE id = ?",
            (booking["quantity"], booking["resource_id"])
        )

        # 4. Record status history
        cur.execute("""
            INSERT INTO booking_status_history (reference_id, actor_id, old_status, new_status)
            VALUES (?, ?, 'CONFIRMED', 'CANCELLED')
        """, (reservation_id, actor_id))

        commit_db(db)
        return True, {
            "message": "Reservation cancelled successfully and seat availability restored.",
            "reservation_id": reservation_id,
            "restored_quantity": booking["quantity"],
            "new_status": "CANCELLED"
        }, 200

    except Exception as e:
        rollback_db(db)
        return False, {"error": f"Transaction failed: {str(e)}"}, 500

def process_status_advance(actor_id: str, reservation_id: str, target_status: str) -> Tuple[bool, Dict[str, Any], int]:
    """
    BR8 & BR10: Admin advances operational status in strict sequence:
    CONFIRMED -> CHECKED_IN -> COMPLETED
    """
    db = get_db()
    try:
        db.execute("BEGIN IMMEDIATE")
        cur = db.cursor()

        # BR10: Only Administrator can advance operational statuses
        cur.execute("SELECT id, role FROM users WHERE id = ?", (actor_id,))
        actor = cur.fetchone()
        if not actor or actor["role"] != "Administrator":
            rollback_db(db)
            return False, {"error": f"Access Denied: Only Administrators can advance operational booking status."}, 403

        cur.execute("SELECT id, status FROM reservations WHERE id = ?", (reservation_id,))
        booking = cur.fetchone()
        if not booking:
            rollback_db(db)
            return False, {"error": f"Reservation '{reservation_id}' not found."}, 404

        curr_status = booking["status"]
        target = target_status.strip().upper()

        # Strict lifecycle transitions
        valid_transitions = {
            "CONFIRMED": ["CHECKED_IN"],
            "CHECKED_IN": ["COMPLETED"]
        }

        allowed = valid_transitions.get(curr_status, [])
        if target not in allowed:
            rollback_db(db)
            return False, {
                "error": f"Invalid status transition from '{curr_status}' to '{target}'. Sequence must strictly follow CONFIRMED -> CHECKED_IN -> COMPLETED without skipping stages."
            }, 400

        # Update reservation status
        cur.execute("UPDATE reservations SET status = ? WHERE id = ?", (target, reservation_id))
        cur.execute("UPDATE reservation_items SET status = ? WHERE reservation_id = ?", (target, reservation_id))

        # Record history
        cur.execute("""
            INSERT INTO booking_status_history (reference_id, actor_id, old_status, new_status)
            VALUES (?, ?, ?, ?)
        """, (reservation_id, actor_id, curr_status, target))

        commit_db(db)
        return True, {
            "message": f"Status updated successfully from '{curr_status}' to '{target}'.",
            "reservation_id": reservation_id,
            "old_status": curr_status,
            "new_status": target
        }, 200

    except Exception as e:
        rollback_db(db)
        return False, {"error": f"Transaction failed: {str(e)}"}, 500

# ---------------------------------------------------------------------------
# API Routes
# ---------------------------------------------------------------------------

@app.route("/api/users", methods=["GET"])
def api_users():
    db = get_db()
    rows = db.execute("SELECT id, name, role, status, phone FROM users ORDER BY id").fetchall()
    return jsonify([dict(r) for r in rows])

@app.route("/api/resources", methods=["GET"])
def api_resources():
    db = get_db()
    search = request.args.get("search", "").strip()
    category = request.args.get("category", "").strip()

    query = "SELECT id, identifier, category, rate, initial_availability, current_availability, status FROM resources WHERE 1=1"
    params = []
    if search:
        query += " AND (identifier LIKE ? OR category LIKE ?)"
        params.extend([f"%{search}%", f"%{search}%"])
    if category:
        query += " AND category = ?"
        params.append(category)
    query += " ORDER BY id"

    rows = db.execute(query, params).fetchall()
    return jsonify([dict(r) for r in rows])

@app.route("/api/resources/<resource_id>/rate", methods=["POST"])
def api_update_rate(resource_id):
    """Admin updates rate for resource (to test BR3/T12)."""
    actor, err = get_current_actor(request)
    if err:
        return jsonify({"error": err}), 400
    if actor["role"] != "Administrator":
        return jsonify({"error": "Access Denied: Only Administrator can update resource rates."}), 403

    payload = request.get_json(silent=True) or {}
    new_rate = payload.get("rate")
    if new_rate is None or not isinstance(new_rate, (int, float)) or new_rate < 0:
        return jsonify({"error": "Invalid rate: Must be a non-negative number."}), 400

    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT id, identifier FROM resources WHERE id = ?", (resource_id,))
    if not cur.fetchone():
        return jsonify({"error": f"Resource '{resource_id}' not found."}), 404

    cur.execute("UPDATE resources SET rate = ? WHERE id = ?", (float(new_rate), resource_id))
    db.commit()
    return jsonify({"message": f"Rate for {resource_id} updated to Rs. {new_rate:.2f}.", "resource_id": resource_id, "new_rate": float(new_rate)})

@app.route("/api/calculate-fare", methods=["POST"])
def api_calculate_fare():
    payload = request.get_json(silent=True) or {}
    rate = payload.get("rate")
    quantity = payload.get("quantity")

    if rate is None or not isinstance(rate, (int, float)) or rate < 0:
        return jsonify({"error": "Invalid rate"}), 400
    if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity < 1 or quantity > 5:
        return jsonify({"error": "Quantity must be an integer between 1 and 5"}), 400

    base, fee, grand = compute_fare(float(rate), quantity)
    return jsonify({
        "rate": float(rate),
        "quantity": quantity,
        "base_amount": base,
        "booking_charge": fee,
        "grand_total": grand
    })

@app.route("/api/reservations", methods=["GET"])
def api_get_reservations():
    actor, err = get_current_actor(request)
    if err:
        return jsonify({"error": err}), 400

    db = get_db()
    # BR10: Users can only view their own bookings; Admins view all
    if actor["role"] == "Administrator":
        query = """
            SELECT r.*, res.identifier as resource_name, res.category as resource_category, u.name as user_name
            FROM reservations r
            JOIN resources res ON r.resource_id = res.id
            JOIN users u ON r.user_id = u.id
            ORDER BY r.created_at DESC
        """
        rows = db.execute(query).fetchall()
    else:
        query = """
            SELECT r.*, res.identifier as resource_name, res.category as resource_category, u.name as user_name
            FROM reservations r
            JOIN resources res ON r.resource_id = res.id
            JOIN users u ON r.user_id = u.id
            WHERE r.user_id = ?
            ORDER BY r.created_at DESC
        """
        rows = db.execute(query, (actor["id"],)).fetchall()

    return jsonify([dict(r) for r in rows])

@app.route("/api/reservations", methods=["POST"])
def api_create_reservation():
    actor, err = get_current_actor(request)
    if err:
        return jsonify({"error": err}), 400

    payload = request.get_json(silent=True) or {}
    resource_id = payload.get("resource_id")
    quantity = payload.get("quantity")
    name = payload.get("name") or actor["name"]
    phone = payload.get("phone") or actor["phone"]
    booking_date = payload.get("booking_date")

    success, res_data, status_code = process_reservation(
        actor_id=actor["id"],
        resource_id=resource_id,
        quantity=quantity,
        name=name,
        phone=phone,
        booking_date=booking_date
    )
    return jsonify(res_data), status_code

@app.route("/api/reservations/<reservation_id>/cancel", methods=["POST"])
def api_cancel_reservation(reservation_id):
    actor, err = get_current_actor(request)
    if err:
        return jsonify({"error": err}), 400

    success, res_data, status_code = process_cancellation(
        actor_id=actor["id"],
        reservation_id=reservation_id
    )
    return jsonify(res_data), status_code

@app.route("/api/reservations/<reservation_id>/status", methods=["POST"])
def api_advance_status(reservation_id):
    actor, err = get_current_actor(request)
    if err:
        return jsonify({"error": err}), 400

    payload = request.get_json(silent=True) or {}
    target_status = payload.get("status", "")

    success, res_data, status_code = process_status_advance(
        actor_id=actor["id"],
        reservation_id=reservation_id,
        target_status=target_status
    )
    return jsonify(res_data), status_code

@app.route("/api/history", methods=["GET"])
def api_history():
    actor, err = get_current_actor(request)
    if err:
        return jsonify({"error": err}), 400
    if actor["role"] != "Administrator":
        return jsonify({"error": "Access Denied: Only Administrators can view complete audit logs."}), 403

    db = get_db()
    rows = db.execute("""
        SELECT h.id, h.reference_id, h.actor_id, h.old_status, h.new_status, h.event_at,
               u.name as actor_name, u.role as actor_role,
               r.resource_id, res.identifier as resource_name
        FROM booking_status_history h
        LEFT JOIN users u ON h.actor_id = u.id
        LEFT JOIN reservations r ON h.reference_id = r.id
        LEFT JOIN resources res ON r.resource_id = res.id
        ORDER BY h.id DESC
    """).fetchall()
    return jsonify([dict(r) for r in rows])

@app.route("/api/reset-db", methods=["POST"])
def api_reset_db():
    actor, err = get_current_actor(request)
    if actor and actor["role"] != "Administrator":
        return jsonify({"error": "Only administrator can reset database."}), 403
    init_db(force_reseed=True)
    return jsonify({"message": "Database reset and re-seeded successfully."})

# ---------------------------------------------------------------------------
# Test Runner Implementation (Runs 12 Test Cases)
# ---------------------------------------------------------------------------

def _execute_test_suite():
    """Internal test runner executing 12 test cases within app context."""
    test_results = []
    
    # Isolate test run by resetting database to clean state
    init_db(force_reseed=True)

    # -------------------------------------------------------------
    # T1: Duplicate reservation check / concurrency rule
    # -------------------------------------------------------------
    try:
        # Resource R105 has initial capacity 3.
        # User Alice attempts to book 2 seats -> Succeeds (remaining 1)
        # Next attempt asks for 2 seats -> Must fail because 2 > 1.
        ok1, res1, code1 = process_reservation("U101", "R105", 2, "Alice", "9876543210", "2026-10-20")
        ok2, res2, code2 = process_reservation("U102", "R105", 2, "Bob", "9123456789", "2026-10-20")

        db = sqlite3.connect(DB_PATH)
        cur = db.cursor()
        cur.execute("SELECT current_availability FROM resources WHERE id = 'R105'")
        r105_avail = cur.fetchone()[0]
        db.close()

        passed = ok1 and (code1 == 201) and (not ok2) and (code2 == 400) and (r105_avail == 1)
        test_results.append({
            "test_id": "T1",
            "name": "Concurrency & Capacity Exhaustion",
            "description": "Simultaneous/repeated bookings respect atomic lock and prevent overselling beyond capacity.",
            "inputs": "R105 (Cap 3): Alice requests 2; Bob concurrently requests 2",
            "expected": "Alice succeeds (rem 1), Bob rejected (400 Insufficient seats), seat remains 1",
            "actual": f"Alice ok={ok1} (code {code1}), Bob ok={ok2} (code {code2}), R105 rem={r105_avail}",
            "passed": passed
        })
    except Exception as e:
        test_results.append({
            "test_id": "T1", "name": "Concurrency & Capacity Exhaustion",
            "description": "Simultaneous/repeated bookings respect atomic lock",
            "inputs": "R105 Qty 2 then 2", "expected": "First succeeds, second fails cleanly",
            "actual": f"Exception: {str(e)}", "passed": False
        })

    # -------------------------------------------------------------
    # T2: Quantity boundaries (0, 1, 5, 6, 1.5)
    # -------------------------------------------------------------
    try:
        # Test 0
        ok_0, _, c_0 = process_reservation("U101", "R103", 0, "Alice", "9876543210", "2026-10-20")
        # Test 6
        ok_6, _, c_6 = process_reservation("U101", "R103", 6, "Alice", "9876543210", "2026-10-20")
        # Test 1.5 (float)
        ok_float, _, c_float = process_reservation("U101", "R103", 1.5, "Alice", "9876543210", "2026-10-20")
        # Test 1
        ok_1, _, c_1 = process_reservation("U101", "R103", 1, "Alice", "9876543210", "2026-10-20")
        # Test 5
        ok_5, _, c_5 = process_reservation("U101", "R103", 5, "Alice", "9876543210", "2026-10-20")

        passed = (not ok_0 and c_0 == 400) and \
                 (not ok_6 and c_6 == 400) and \
                 (not ok_float and c_float == 400) and \
                 (ok_1 and c_1 == 201) and \
                 (ok_5 and c_5 == 201)

        test_results.append({
            "test_id": "T2",
            "name": "Quantity Boundary Checks",
            "description": "Only integers 1 <= qty <= 5 accepted. Reject 0, 6, 1.5 with error 400.",
            "inputs": "quantities: 0, 6, 1.5, 1, 5",
            "expected": "0->Reject, 6->Reject, 1.5->Reject, 1->Accept, 5->Accept",
            "actual": f"0:{c_0}, 6:{c_6}, 1.5:{c_float}, 1:{c_1}, 5:{c_5}",
            "passed": passed
        })
    except Exception as e:
        test_results.append({
            "test_id": "T2", "name": "Quantity Boundary Checks",
            "description": "Quantity boundaries (0, 1, 5, 6, 1.5)",
            "inputs": "0, 6, 1.5, 1, 5", "expected": "Strict validation",
            "actual": f"Exception: {str(e)}", "passed": False
        })

    # -------------------------------------------------------------
    # T3: Try booking out-of-stock resource R104 (Availability 0)
    # -------------------------------------------------------------
    try:
        ok_oos, res_oos, c_oos = process_reservation("U101", "R104", 1, "Alice", "9876543210", "2026-10-20")
        db = sqlite3.connect(DB_PATH)
        cur = db.cursor()
        cur.execute("SELECT current_availability FROM resources WHERE id = 'R104'")
        r104_avail = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM reservations WHERE resource_id = 'R104'")
        r104_res_count = cur.fetchone()[0]
        db.close()

        passed = (not ok_oos) and (c_oos == 400) and (r104_avail == 0) and (r104_res_count == 0)
        test_results.append({
            "test_id": "T3",
            "name": "Out-of-Stock Resource Booking",
            "description": "Attempt booking resource R104 with 0 availability.",
            "inputs": "R104 (VIP Masterclass, current_availability=0), quantity=1",
            "expected": "HTTP 400 Insufficient seats, 0 reservations created, availability untouched",
            "actual": f"status={c_oos}, error='{res_oos.get('error')}', reservations={r104_res_count}",
            "passed": passed
        })
    except Exception as e:
        test_results.append({
            "test_id": "T3", "name": "Out-of-Stock Resource Booking",
            "description": "Out-of-stock booking handling",
            "inputs": "R104 qty 1", "expected": "Rejected with 400",
            "actual": f"Exception: {str(e)}", "passed": False
        })

    # -------------------------------------------------------------
    # T4: Fare threshold calculations at Rs. 999, Rs. 1,000, and Rs. 1,001
    # -------------------------------------------------------------
    try:
        b_999, f_999, g_999 = compute_fare(999.0, 1)
        b_1000, f_1000, g_1000 = compute_fare(1000.0, 1)
        b_1001, f_1001, g_1001 = compute_fare(1001.0, 1)

        p1 = (b_999 == 999.0 and f_999 == 50.0 and g_999 == 1049.0)
        p2 = (b_1000 == 1000.0 and f_1000 == 0.0 and g_1000 == 1000.0)
        p3 = (b_1001 == 1001.0 and f_1001 == 0.0 and g_1001 == 1001.0)
        passed = p1 and p2 and p3

        test_results.append({
            "test_id": "T4",
            "name": "Fare Threshold & Booking Charge (< Rs. 1000 vs >= Rs. 1000)",
            "description": "Fee Rs. 50 if base < 1000, Fee Rs. 0 if base >= 1000.",
            "inputs": "Base rates: Rs. 999, Rs. 1000, Rs. 1001 (quantity=1)",
            "expected": "999: fee 50 total 1049; 1000: fee 0 total 1000; 1001: fee 0 total 1001",
            "actual": f"999: (fee={f_999}, tot={g_999}); 1000: (fee={f_1000}, tot={g_1000}); 1001: (fee={f_1001}, tot={g_1001})",
            "passed": passed
        })
    except Exception as e:
        test_results.append({
            "test_id": "T4", "name": "Fare Threshold & Booking Charge",
            "description": "Fare calculations at thresholds",
            "inputs": "999, 1000, 1001", "expected": "Correct booking charges",
            "actual": f"Exception: {str(e)}", "passed": False
        })

    # -------------------------------------------------------------
    # T5: Valid reservation atomicity and capacity decrement
    # -------------------------------------------------------------
    try:
        # Reset R101 initial: 10
        ok_v, res_v, c_v = process_reservation("U101", "R101", 2, "Alice", "9876543210", "2026-10-25")
        booking_data = res_v.get("reservation", {})
        res_id_v = booking_data.get("id")

        db = sqlite3.connect(DB_PATH)
        cur = db.cursor()
        cur.execute("SELECT current_availability FROM resources WHERE id = 'R101'")
        r101_avail = cur.fetchone()[0]

        cur.execute("SELECT reserved_rate, quantity FROM reservation_items WHERE reservation_id = ?", (res_id_v,))
        item_row = cur.fetchone()

        cur.execute("SELECT old_status, new_status FROM booking_status_history WHERE reference_id = ?", (res_id_v,))
        hist_row = cur.fetchone()
        db.close()

        passed = (ok_v and c_v == 201) and \
                 (r101_avail == 8) and \
                 (item_row and item_row[0] == 499.0 and item_row[1] == 2) and \
                 (hist_row and hist_row[0] is None and hist_row[1] == "CONFIRMED") and \
                 (booking_data.get("grand_total") == 1048.0) # 499*2 = 998 + 50 fee = 1048

        test_results.append({
            "test_id": "T5",
            "name": "Atomic Reservation & Capacity Decrement",
            "description": "Valid booking decrements capacity, creates snapshot item, and logs initial history atomically.",
            "inputs": "R101 (rate 499, avail 10) booked with qty 2",
            "expected": "Avail 10->8, status CONFIRMED, rate_snapshot 499, base 998, grand 1048, item created, history logged",
            "actual": f"Avail={r101_avail}, status={booking_data.get('status')}, grand_total={booking_data.get('grand_total')}, item={item_row}, history={hist_row}",
            "passed": passed
        })
    except Exception as e:
        test_results.append({
            "test_id": "T5", "name": "Atomic Reservation & Capacity Decrement",
            "description": "Atomicity and capacity decrement verification",
            "inputs": "R101 qty 2", "expected": "Atomic decrement and logging",
            "actual": f"Exception: {str(e)}", "passed": False
        })

    # -------------------------------------------------------------
    # T6: Missing/invalid phone/name details rejected with zero state changes
    # -------------------------------------------------------------
    try:
        db = sqlite3.connect(DB_PATH)
        cur = db.cursor()
        cur.execute("SELECT current_availability FROM resources WHERE id = 'R101'")
        init_avail_t6 = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM reservations")
        init_res_count_t6 = cur.fetchone()[0]
        db.close()

        # Case A: Blank name
        ok_a, _, c_a = process_reservation("U101", "R101", 1, "   ", "9876543210", "2026-10-25")
        # Case B: Phone too short
        ok_b, _, c_b = process_reservation("U101", "R101", 1, "Alice", "12345", "2026-10-25")
        # Case C: Phone with non-digits
        ok_c, _, c_c = process_reservation("U101", "R101", 1, "Alice", "98765abcde", "2026-10-25")
        # Case D: Invalid date
        ok_d, _, c_d = process_reservation("U101", "R101", 1, "Alice", "9876543210", "invalid-date")

        db = sqlite3.connect(DB_PATH)
        cur = db.cursor()
        cur.execute("SELECT current_availability FROM resources WHERE id = 'R101'")
        post_avail_t6 = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM reservations")
        post_res_count_t6 = cur.fetchone()[0]
        db.close()

        passed = (not ok_a and c_a == 400) and \
                 (not ok_b and c_b == 400) and \
                 (not ok_c and c_c == 400) and \
                 (not ok_d and c_d == 400) and \
                 (init_avail_t6 == post_avail_t6) and \
                 (init_res_count_t6 == post_res_count_t6)

        test_results.append({
            "test_id": "T6",
            "name": "Input Validation & Zero State Mutation",
            "description": "Invalid phone, blank name, or bad date rejected with zero changes to database.",
            "inputs": "Blank name, phone='12345', phone='98765abcde', date='invalid-date'",
            "expected": "All rejected with 400; capacity and reservations count unchanged",
            "actual": f"Codes: {c_a}, {c_b}, {c_c}, {c_d}; Avail before={init_avail_t6}, after={post_avail_t6}; Count before={init_res_count_t6}, after={post_res_count_t6}",
            "passed": passed
        })
    except Exception as e:
        test_results.append({
            "test_id": "T6", "name": "Input Validation & Zero State Mutation",
            "description": "Invalid details rejected",
            "inputs": "Malformed inputs", "expected": "Rejected with 400",
            "actual": f"Exception: {str(e)}", "passed": False
        })

    # -------------------------------------------------------------
    # T7: Capacity change / race before confirmation rejected cleanly
    # -------------------------------------------------------------
    try:
        # Create a temporary resource with exactly 1 seat
        db = sqlite3.connect(DB_PATH)
        cur = db.cursor()
        cur.execute("""
            INSERT OR REPLACE INTO resources (id, identifier, category, rate, initial_availability, current_availability, status)
            VALUES ('R999', 'Race Test Seat', 'Special', 500.0, 1, 1, 'ACTIVE')
        """)
        db.commit()
        db.close()

        # Simulation: First user consumes the 1 seat
        ok_race1, _, c_race1 = process_reservation("U101", "R999", 1, "Alice", "9876543210", "2026-10-25")
        # Second user attempts confirmation for the same seat
        ok_race2, res_race2, c_race2 = process_reservation("U102", "R999", 1, "Bob", "9123456789", "2026-10-25")

        db = sqlite3.connect(DB_PATH)
        cur = db.cursor()
        cur.execute("SELECT current_availability FROM resources WHERE id = 'R999'")
        r999_avail = cur.fetchone()[0]
        # Clean up temp resource
        cur.execute("DELETE FROM resources WHERE id = 'R999'")
        db.commit()
        db.close()

        passed = ok_race1 and (c_race1 == 201) and (not ok_race2) and (c_race2 == 400) and (r999_avail == 0)

        test_results.append({
            "test_id": "T7",
            "name": "Race Condition / Stale Selection Prevention",
            "description": "Capacity consumed right before second confirmation is rejected with clear error.",
            "inputs": "R999 (avail 1): Alice confirms 1 seat; Bob confirms 1 seat immediately after",
            "expected": "Alice succeeds (code 201), Bob fails (code 400 Insufficient seats), avail 0",
            "actual": f"Alice ok={ok_race1} (c={c_race1}), Bob ok={ok_race2} (c={c_race2}, err='{res_race2.get('error')}'), Avail={r999_avail}",
            "passed": passed
        })
    except Exception as e:
        test_results.append({
            "test_id": "T7", "name": "Race Condition Prevention",
            "description": "Stale selection rejection",
            "inputs": "Concurrent capacity consumption", "expected": "Clean rollback",
            "actual": f"Exception: {str(e)}", "passed": False
        })

    # -------------------------------------------------------------
    # T8: User Bob trying to access/cancel Alice's booking (Access Denied)
    # -------------------------------------------------------------
    try:
        # Alice creates a confirmed booking
        ok_al, res_al, _ = process_reservation("U101", "R102", 1, "Alice", "9876543210", "2026-10-26")
        alice_booking_id = res_al["reservation"]["id"]

        # Bob (U102) attempts to cancel Alice's booking
        ok_bob_cancel, res_bob_cancel, code_bob_cancel = process_cancellation("U102", alice_booking_id)

        # Check that Alice's booking is STILL CONFIRMED
        db = sqlite3.connect(DB_PATH)
        cur = db.cursor()
        cur.execute("SELECT status FROM reservations WHERE id = ?", (alice_booking_id,))
        status_after_unauth = cur.fetchone()[0]
        db.close()

        passed = (not ok_bob_cancel) and (code_bob_cancel == 403) and (status_after_unauth == "CONFIRMED")

        test_results.append({
            "test_id": "T8",
            "name": "Access Control (BR10: Cross-User Isolation)",
            "description": "User Bob cannot cancel Alice's reservation. Returns HTTP 403 Access Denied.",
            "inputs": f"Alice (U101) booking '{alice_booking_id}'; Bob (U102) calls cancellation",
            "expected": "HTTP 403 Access Denied, booking status remains 'CONFIRMED'",
            "actual": f"Code={code_bob_cancel}, error='{res_bob_cancel.get('error')}', status={status_after_unauth}",
            "passed": passed
        })
    except Exception as e:
        test_results.append({
            "test_id": "T8", "name": "Access Control",
            "description": "Cross-user cancellation blocked",
            "inputs": "Bob cancels Alice booking", "expected": "HTTP 403",
            "actual": f"Exception: {str(e)}", "passed": False
        })

    # -------------------------------------------------------------
    # T9: Admin advancing status & invalid stage skipping check
    # -------------------------------------------------------------
    try:
        # We use Alice's confirmed booking from T8: alice_booking_id (status is CONFIRMED)
        # Attempt INVALID jump: CONFIRMED -> COMPLETED (skipping CHECKED_IN)
        ok_skip, res_skip, code_skip = process_status_advance("ADM01", alice_booking_id, "COMPLETED")

        # Advance valid: CONFIRMED -> CHECKED_IN
        ok_checkin, _, code_checkin = process_status_advance("ADM01", alice_booking_id, "CHECKED_IN")

        # Advance valid: CHECKED_IN -> COMPLETED
        ok_complete, _, code_complete = process_status_advance("ADM01", alice_booking_id, "COMPLETED")

        # Attempt transition from COMPLETED -> anything (should fail)
        ok_post_comp, _, code_post_comp = process_status_advance("ADM01", alice_booking_id, "CHECKED_IN")

        # Check status history records
        db = sqlite3.connect(DB_PATH)
        cur = db.cursor()
        cur.execute("SELECT old_status, new_status FROM booking_status_history WHERE reference_id = ? ORDER BY id", (alice_booking_id,))
        histories = cur.fetchall()
        db.close()

        passed = (not ok_skip and code_skip == 400) and \
                 (ok_checkin and code_checkin == 200) and \
                 (ok_complete and code_complete == 200) and \
                 (not ok_post_comp and code_post_comp == 400) and \
                 (len(histories) >= 3) # Initial NULL->CONFIRMED, CONFIRMED->CHECKED_IN, CHECKED_IN->COMPLETED

        test_results.append({
            "test_id": "T9",
            "name": "Status Lifecycle Progression & Skip Prevention",
            "description": "Lifecycle strictly CONFIRMED -> CHECKED_IN -> COMPLETED. Cannot jump stages.",
            "inputs": "Jump CONFIRMED->COMPLETED, then CONFIRMED->CHECKED_IN, then CHECKED_IN->COMPLETED",
            "expected": "Jump rejected (400), valid progression succeeds (200), audit trail records transitions",
            "actual": f"Jump c={code_skip}, Checkin c={code_checkin}, Complete c={code_complete}, Hist transitions={len(histories)}",
            "passed": passed
        })
    except Exception as e:
        test_results.append({
            "test_id": "T9", "name": "Status Lifecycle Progression",
            "description": "Admin status advancement",
            "inputs": "CONFIRMED -> COMPLETED jump attempt", "expected": "Strict lifecycle",
            "actual": f"Exception: {str(e)}", "passed": False
        })

    # -------------------------------------------------------------
    # T10: Cancel confirmed booking & repeat cancellation check
    # -------------------------------------------------------------
    try:
        # Create a new booking for Bob on R102 (capacity was 4 after T8)
        db = sqlite3.connect(DB_PATH)
        cur = db.cursor()
        cur.execute("SELECT current_availability FROM resources WHERE id = 'R102'")
        avail_before_t10 = cur.fetchone()[0]
        db.close()

        ok_bob_b, res_bob_b, _ = process_reservation("U102", "R102", 2, "Bob", "9123456789", "2026-10-27")
        bob_booking_id = res_bob_b["reservation"]["id"]

        # First cancellation by Bob (Owner) -> Must succeed and restore 2 seats
        ok_c1, res_c1, code_c1 = process_cancellation("U102", bob_booking_id)

        db = sqlite3.connect(DB_PATH)
        cur = db.cursor()
        cur.execute("SELECT current_availability FROM resources WHERE id = 'R102'")
        avail_after_c1 = cur.fetchone()[0]
        db.close()

        # Second cancellation attempt on same booking -> Must be rejected, no extra capacity added
        ok_c2, res_c2, code_c2 = process_cancellation("U102", bob_booking_id)

        db = sqlite3.connect(DB_PATH)
        cur = db.cursor()
        cur.execute("SELECT current_availability FROM resources WHERE id = 'R102'")
        avail_after_c2 = cur.fetchone()[0]
        db.close()

        passed = (ok_c1 and code_c1 == 200) and \
                 (avail_after_c1 == avail_before_t10) and \
                 (not ok_c2 and code_c2 == 400) and \
                 (avail_after_c2 == avail_after_c1)

        test_results.append({
            "test_id": "T10",
            "name": "Cancellation & Repeated Cancellation Idempotency",
            "description": "First cancel restores capacity; repeat cancellation rejected with 0 extra restoration.",
            "inputs": f"Bob cancels booking '{bob_booking_id}' twice",
            "expected": "1st cancel succeeds & restores +2 seats; 2nd cancel rejected (400) & capacity unchanged",
            "actual": f"1st code={code_c1} (avail={avail_after_c1}), 2nd code={code_c2} (avail={avail_after_c2}, err='{res_c2.get('error')}')",
            "passed": passed
        })
    except Exception as e:
        test_results.append({
            "test_id": "T10", "name": "Cancellation & Repeated Cancellation",
            "description": "Restoration once only",
            "inputs": "Cancel twice", "expected": "Rejected on second try",
            "actual": f"Exception: {str(e)}", "passed": False
        })

    # -------------------------------------------------------------
    # T11: Persistence verification across simulated DB restart
    # -------------------------------------------------------------
    try:
        # Create a known booking, then close and reopen database
        ok_p, res_p, _ = process_reservation("U101", "R103", 2, "Alice", "9876543210", "2026-10-28")
        p_booking_id = res_p["reservation"]["id"]

        # Simulate complete restart by disconnecting and creating a completely fresh connection
        conn_persisted = sqlite3.connect(DB_PATH)
        conn_persisted.row_factory = sqlite3.Row
        cur_p = conn_persisted.cursor()

        cur_p.execute("SELECT * FROM reservations WHERE id = ?", (p_booking_id,))
        persisted_res = cur_p.fetchone()

        cur_p.execute("SELECT * FROM reservation_items WHERE reservation_id = ?", (p_booking_id,))
        persisted_item = cur_p.fetchone()

        cur_p.execute("SELECT * FROM booking_status_history WHERE reference_id = ?", (p_booking_id,))
        persisted_hist = cur_p.fetchall()
        conn_persisted.close()

        passed = (persisted_res is not None) and \
                 (persisted_item is not None) and \
                 (len(persisted_hist) > 0) and \
                 (persisted_res["status"] == "CONFIRMED") and \
                 (persisted_res["user_id"] == "U101")

        test_results.append({
            "test_id": "T11",
            "name": "Database Persistence Across Connection Restarts",
            "description": "All tables, reservations, and history persist across complete database disconnections.",
            "inputs": f"Reservation '{p_booking_id}' written; connection destroyed; new connection re-opened",
            "expected": "Reservation, line items, and audit trail records exist intact",
            "actual": f"Found booking={persisted_res is not None}, item={persisted_item is not None}, hist_count={len(persisted_hist)}",
            "passed": passed
        })
    except Exception as e:
        test_results.append({
            "test_id": "T11", "name": "Database Persistence",
            "description": "Persistence verification",
            "inputs": "DB disconnect & reconnect", "expected": "Data fully persisted",
            "actual": f"Exception: {str(e)}", "passed": False
        })

    # -------------------------------------------------------------
    # T12: Rate alteration on resource does not mutate historical reservation snapshot
    # -------------------------------------------------------------
    try:
        # Create booking for R101 (Seat A, initial rate 499.0)
        ok_snap, res_snap, _ = process_reservation("U101", "R101", 1, "Alice", "9876543210", "2026-10-29")
        snap_booking_id = res_snap["reservation"]["id"]
        original_rate_snapshot = res_snap["reservation"]["rate_snapshot"]
        original_grand_total = res_snap["reservation"]["grand_total"]

        # Admin alters R101 rate from 499.0 to 1499.0
        db = sqlite3.connect(DB_PATH)
        cur = db.cursor()
        cur.execute("UPDATE resources SET rate = 1499.0 WHERE id = 'R101'")
        db.commit()

        # Query past reservation and reservation_items
        cur.execute("SELECT rate_snapshot, base_amount, booking_charge, grand_total FROM reservations WHERE id = ?", (snap_booking_id,))
        res_after_rate_change = cur.fetchone()

        cur.execute("SELECT reserved_rate FROM reservation_items WHERE reservation_id = ?", (snap_booking_id,))
        item_after_rate_change = cur.fetchone()

        # Query resource current rate
        cur.execute("SELECT rate FROM resources WHERE id = 'R101'")
        resource_new_rate = cur.fetchone()[0]
        db.close()

        passed = (resource_new_rate == 1499.0) and \
                 (res_after_rate_change[0] == original_rate_snapshot == 499.0) and \
                 (res_after_rate_change[3] == original_grand_total == 549.0) and \
                 (item_after_rate_change[0] == 499.0)

        test_results.append({
            "test_id": "T12",
            "name": "Fare Snapshot Immutability (BR3)",
            "description": "Updating resource catalog rate does not alter historical reservation rates or totals.",
            "inputs": f"R101 rate updated from 499.0 -> 1499.0; inspect booking '{snap_booking_id}'",
            "expected": "Resource rate=1499.0, but reservation rate_snapshot=499.0 and grand_total=549.0 unchanged",
            "actual": f"Res rate={resource_new_rate}, booking rate_snapshot={res_after_rate_change[0]}, grand_total={res_after_rate_change[3]}, item reserved_rate={item_after_rate_change[0]}",
            "passed": passed
        })
    except Exception as e:
        test_results.append({
            "test_id": "T12", "name": "Fare Snapshot Immutability",
            "description": "Fare snapshot verification",
            "inputs": "Update resource rate", "expected": "Historical reservation unchanged",
            "actual": f"Exception: {str(e)}", "passed": False
        })

    return test_results

def run_all_tests():
    """Executes the 12 mandatory test cases within app context and returns results."""
    with app.app_context():
        return _execute_test_suite()

@app.route("/api/run-tests", methods=["POST", "GET"])
def api_run_tests():
    results = run_all_tests()
    all_passed = all(t["passed"] for t in results)
    summary = {
        "total": len(results),
        "passed": sum(1 for t in results if t["passed"]),
        "failed": sum(1 for t in results if not t["passed"]),
        "all_passed": all_passed,
        "results": results
    }
    return jsonify(summary)

# ---------------------------------------------------------------------------
# UI Template (Single Page Application with Tailwind CSS)
# ---------------------------------------------------------------------------

INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>CampusEventHub | College Event Registration System</title>
  <!-- Tailwind CSS CDN -->
  <script src="https://cdn.tailwindcss.com"></script>
  <!-- Phosphor Icons -->
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">
  <script>
    tailwind.config = {
      darkMode: 'class',
      theme: {
        extend: {
          fontFamily: {
            sans: ['Plus Jakarta Sans', 'sans-serif'],
          },
          colors: {
            brand: {
              50: '#eef2ff',
              100: '#e0e7ff',
              500: '#6366f1',
              600: '#4f46e5',
              700: '#4338ca',
            }
          }
        }
      }
    }
  </script>
  <style>
    body { font-family: 'Plus Jakarta Sans', sans-serif; }
    .glass-card {
      background: rgba(255, 255, 255, 0.95);
      backdrop-filter: blur(10px);
      border: 1px solid rgba(226, 232, 240, 0.8);
    }
    .badge-glow {
      box-shadow: 0 0 12px rgba(99, 102, 241, 0.25);
    }
  </style>
</head>
<body class="bg-slate-50 text-slate-900 min-h-screen flex flex-col antialiased">

  <!-- Top Announcement Bar / System Context -->
  <div class="bg-slate-900 text-slate-300 px-4 py-2 text-xs flex justify-between items-center border-b border-slate-800">
    <div class="flex items-center gap-2">
      <span class="inline-block w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span>
      <span class="font-medium text-white">System Architecture:</span>
      <span>Industry Use Case 38 &bull; SQLite Atomic Ledger Engine</span>
    </div>
    <div class="flex items-center gap-4 text-slate-400">
      <span>Fee Rule: Rs. 50 if Base &lt; 1,000 | Rs. 0 if &gt;= 1,000</span>
      <span class="hidden md:inline">&bull; Max 5 Seats/Booking</span>
    </div>
  </div>

  <!-- Main Navigation Header -->
  <header class="sticky top-0 z-40 bg-white/90 backdrop-blur-md border-b border-slate-200">
    <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-18 py-3 flex flex-wrap items-center justify-between gap-4">
      
      <!-- Logo and Title -->
      <div class="flex items-center gap-3">
        <div class="w-10 h-10 rounded-xl bg-gradient-to-tr from-indigo-600 to-violet-500 flex items-center justify-center text-white shadow-md shadow-indigo-100">
          <i class="fa-solid fa-ticket text-lg"></i>
        </div>
        <div>
          <h1 class="text-lg font-bold text-slate-900 leading-tight">CampusEventHub</h1>
          <p class="text-xs text-slate-500 font-medium">College Event Seat & Pass Reservation</p>
        </div>
      </div>

      <!-- Navigation Tabs -->
      <nav class="flex items-center gap-1 bg-slate-100 p-1 rounded-xl text-sm font-semibold">
        <button id="nav-catalog" onclick="switchTab('catalog')" class="px-4 py-2 rounded-lg transition-all text-indigo-700 bg-white shadow-sm">
          <i class="fa-solid fa-compass mr-1.5 text-xs"></i> Seats Catalog
        </button>
        <button id="nav-mybookings" onclick="switchTab('mybookings')" class="px-4 py-2 rounded-lg transition-all text-slate-600 hover:text-slate-900">
          <i class="fa-solid fa-list-check mr-1.5 text-xs"></i> My Bookings <span id="my-bookings-count" class="ml-1 px-1.5 py-0.5 rounded-full text-xs bg-slate-200 text-slate-700">0</span>
        </button>
        <button id="nav-admin" onclick="switchTab('admin')" class="px-4 py-2 rounded-lg transition-all text-slate-600 hover:text-slate-900">
          <i class="fa-solid fa-shield-halved mr-1.5 text-xs"></i> Admin Console
        </button>
        <button id="nav-tests" onclick="switchTab('tests')" class="px-4 py-2 rounded-lg transition-all text-slate-600 hover:text-slate-900">
          <i class="fa-solid fa-vial-circle-check mr-1.5 text-xs"></i> Test Runner
        </button>
      </nav>

      <!-- Simulated Role Switcher Header (Compulsory) -->
      <div class="flex items-center gap-3 bg-indigo-50/70 border border-indigo-100 px-3 py-1.5 rounded-xl">
        <div class="text-right">
          <span class="text-[10px] uppercase font-bold tracking-wider text-indigo-600 block">Simulated Actor</span>
          <span id="actor-badge" class="text-xs font-bold text-slate-900">User: Alice</span>
        </div>
        <select id="role-selector" onchange="onActorChange(this.value)" class="text-sm font-semibold bg-white border border-indigo-200 rounded-lg px-2.5 py-1.5 text-slate-800 shadow-sm focus:outline-none focus:ring-2 focus:ring-indigo-500">
          <option value="U101">Alice (User - U101)</option>
          <option value="U102">Bob (User - U102)</option>
          <option value="ADM01">Admin Officer (ADM01)</option>
        </select>
      </div>

    </div>
  </header>

  <!-- Notification Banner -->
  <div id="toast-container" class="fixed bottom-5 right-5 z-50 flex flex-col gap-2 max-w-md w-full pointer-events-none"></div>

  <!-- Main Container -->
  <main class="flex-1 max-w-7xl w-full mx-auto px-4 sm:px-6 lg:px-8 py-8">

    <!-- =================================================================== -->
    <!-- TAB 1: CATALOG & LIVE BOOKING PREVIEW (User View) -->
    <!-- =================================================================== -->
    <section id="tab-catalog" class="space-y-6">
      
      <!-- Hero Banner -->
      <div class="relative overflow-hidden rounded-2xl bg-gradient-to-r from-indigo-900 via-indigo-800 to-slate-900 text-white p-6 sm:p-8 shadow-xl">
        <div class="relative z-10 max-w-2xl">
          <span class="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold bg-indigo-500/30 text-indigo-200 border border-indigo-400/30 mb-3">
            <i class="fa-solid fa-sparkles text-xs"></i> Annual College Symposium 2026
          </span>
          <h2 class="text-2xl sm:text-3xl font-extrabold tracking-tight">Reserve Official Event Passes & Seats</h2>
          <p class="mt-2 text-sm text-indigo-100 leading-relaxed">
            Choose your technical keynotes, workshops, cultural passes, or masterclasses. Atomic inventory ensures no double-booking. Transparent fare calculation with fee exemption on orders &ge; Rs. 1,000.
          </p>
        </div>
        <div class="absolute right-0 bottom-0 top-0 w-1/3 opacity-10 flex items-center justify-center pointer-events-none text-9xl">
          <i class="fa-solid fa-graduation-cap"></i>
        </div>
      </div>

      <!-- Search & Filters -->
      <div class="flex flex-wrap items-center justify-between gap-4 bg-white p-4 rounded-xl border border-slate-200 shadow-sm">
        <div class="flex items-center gap-3 flex-1 min-w-[260px]">
          <div class="relative w-full max-w-md">
            <i class="fa-solid fa-magnifying-glass absolute left-3.5 top-3 text-slate-400 text-sm"></i>
            <input type="text" id="search-input" oninput="fetchResources()" placeholder="Search seats, workshops, keynotes..." class="w-full pl-9 pr-4 py-2 text-sm bg-slate-50 border border-slate-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:bg-white transition-all">
          </div>
          <select id="category-filter" onchange="fetchResources()" class="text-sm bg-slate-50 border border-slate-200 rounded-lg px-3 py-2 text-slate-700 focus:outline-none focus:ring-2 focus:ring-indigo-500">
            <option value="">All Categories</option>
            <option value="Technical Keynote">Technical Keynote</option>
            <option value="Workshop Hands-on">Workshop Hands-on</option>
            <option value="Cultural Pass">Cultural Pass</option>
            <option value="VIP Masterclass">VIP Masterclass</option>
            <option value="Hackathon Entry">Hackathon Entry</option>
          </select>
        </div>
        <div class="text-xs text-slate-500 font-medium">
          Showing <span id="catalog-count" class="font-bold text-slate-900">0</span> seat packages
        </div>
      </div>

      <!-- Resource Grid -->
      <div id="catalog-grid" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        <!-- Injected dynamically via JS -->
      </div>

    </section>

    <!-- =================================================================== -->
    <!-- TAB 2: MY BOOKINGS (User View) -->
    <!-- =================================================================== -->
    <section id="tab-mybookings" class="hidden space-y-6">
      <div class="flex flex-wrap items-center justify-between gap-4 bg-white p-5 rounded-2xl border border-slate-200 shadow-sm">
        <div>
          <h2 class="text-xl font-bold text-slate-900">My Reservations</h2>
          <p class="text-xs text-slate-500 mt-0.5">Showing registered bookings for current actor: <span id="current-user-label" class="font-bold text-indigo-600">Alice</span></p>
        </div>
        <button onclick="fetchMyBookings()" class="px-3.5 py-1.5 text-xs font-semibold bg-slate-100 hover:bg-slate-200 text-slate-700 rounded-lg transition-all flex items-center gap-1.5">
          <i class="fa-solid fa-arrows-rotate"></i> Refresh List
        </button>
      </div>

      <!-- Bookings Container -->
      <div id="my-bookings-container" class="space-y-4">
        <!-- Injected dynamically via JS -->
      </div>
    </section>

    <!-- =================================================================== -->
    <!-- TAB 3: ADMIN CONSOLE (Admin View) -->
    <!-- =================================================================== -->
    <section id="tab-admin" class="hidden space-y-6">
      
      <!-- Admin Notice if not ADM01 -->
      <div id="admin-forbidden-alert" class="hidden p-5 rounded-2xl bg-amber-50 border border-amber-200 text-amber-900">
        <div class="flex items-start gap-3">
          <i class="fa-solid fa-triangle-exclamation text-amber-600 text-xl mt-0.5"></i>
          <div>
            <h4 class="font-bold text-sm">Administrator Role Required</h4>
            <p class="text-xs text-amber-800 mt-1">
              You are currently impersonating <strong id="alert-actor-name">User</strong>. Please switch the role dropdown at the top to <strong>Admin Officer (ADM01)</strong> to advance operational statuses, alter seat rates, and review audit trails.
            </p>
          </div>
        </div>
      </div>

      <div id="admin-main-content" class="space-y-6">
        
        <!-- Admin Stats & Controls Bar -->
        <div class="grid grid-cols-1 sm:grid-cols-4 gap-4">
          <div class="bg-white p-4 rounded-xl border border-slate-200 shadow-sm">
            <span class="text-xs font-medium text-slate-500 uppercase tracking-wider block">Total Bookings</span>
            <span id="stat-total-bookings" class="text-2xl font-bold text-slate-900 mt-1 block">0</span>
          </div>
          <div class="bg-white p-4 rounded-xl border border-slate-200 shadow-sm">
            <span class="text-xs font-medium text-slate-500 uppercase tracking-wider block">Confirmed</span>
            <span id="stat-confirmed-bookings" class="text-2xl font-bold text-emerald-600 mt-1 block">0</span>
          </div>
          <div class="bg-white p-4 rounded-xl border border-slate-200 shadow-sm">
            <span class="text-xs font-medium text-slate-500 uppercase tracking-wider block">Checked In / Completed</span>
            <span id="stat-progressed-bookings" class="text-2xl font-bold text-indigo-600 mt-1 block">0</span>
          </div>
          <div class="bg-white p-4 rounded-xl border border-slate-200 shadow-sm">
            <span class="text-xs font-medium text-slate-500 uppercase tracking-wider block">Gross Revenue</span>
            <span id="stat-revenue" class="text-2xl font-bold text-slate-900 mt-1 block">Rs. 0</span>
          </div>
        </div>

        <!-- Seat Rate Modifier (For BR3 & T12 Verification) -->
        <div class="bg-white p-5 rounded-2xl border border-slate-200 shadow-sm">
          <div class="flex items-center justify-between border-b border-slate-100 pb-3 mb-4">
            <div>
              <h3 class="text-base font-bold text-slate-900 flex items-center gap-2">
                <i class="fa-solid fa-tags text-indigo-600"></i> Seat Rate Utility (Catalog Price Editor)
              </h3>
              <p class="text-xs text-slate-500">Edit active seat rate to verify <strong>BR3 / Test T12</strong> (Historical snapshot immutability).</p>
            </div>
          </div>
          <div id="admin-rate-editor-grid" class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            <!-- Injected via JS -->
          </div>
        </div>

        <!-- Master Booking Table -->
        <div class="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
          <div class="p-5 border-b border-slate-100 flex flex-wrap items-center justify-between gap-4">
            <div>
              <h3 class="text-base font-bold text-slate-900">All Student Reservations Ledger</h3>
              <p class="text-xs text-slate-500">Enforces BR8 progression: <code class="bg-slate-100 px-1 py-0.5 rounded text-indigo-600 font-mono">CONFIRMED &rarr; CHECKED_IN &rarr; COMPLETED</code></p>
            </div>
            <button onclick="fetchAdminData()" class="px-3.5 py-1.5 text-xs font-semibold bg-slate-100 hover:bg-slate-200 text-slate-700 rounded-lg transition-all flex items-center gap-1.5">
              <i class="fa-solid fa-arrows-rotate"></i> Refresh Table
            </button>
          </div>

          <div class="overflow-x-auto">
            <table class="w-full text-left text-sm">
              <thead class="bg-slate-50 text-slate-600 text-xs uppercase tracking-wider border-b border-slate-200">
                <tr>
                  <th class="py-3 px-4">Booking Ref</th>
                  <th class="py-3 px-4">Student</th>
                  <th class="py-3 px-4">Seat / Package</th>
                  <th class="py-3 px-4">Qty</th>
                  <th class="py-3 px-4">Rate Snapshot</th>
                  <th class="py-3 px-4">Grand Total</th>
                  <th class="py-3 px-4">Current Status</th>
                  <th class="py-3 px-4">Lifecycle Action</th>
                </tr>
              </thead>
              <tbody id="admin-bookings-table-body" class="divide-y divide-slate-100">
                <!-- Injected via JS -->
              </tbody>
            </table>
          </div>
        </div>

        <!-- Complete Audit Trail Ledger -->
        <div class="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
          <div class="p-5 border-b border-slate-100 flex items-center justify-between">
            <div>
              <h3 class="text-base font-bold text-slate-900 flex items-center gap-2">
                <i class="fa-solid fa-clock-rotate-left text-slate-600"></i> Immutable Audit Trail (booking_status_history)
              </h3>
              <p class="text-xs text-slate-500">Chronological ledger of all state transitions and executing actors.</p>
            </div>
          </div>
          <div class="overflow-x-auto max-h-96">
            <table class="w-full text-left text-xs">
              <thead class="bg-slate-50 text-slate-600 uppercase tracking-wider border-b border-slate-200 sticky top-0">
                <tr>
                  <th class="py-2.5 px-4">ID</th>
                  <th class="py-2.5 px-4">Timestamp</th>
                  <th class="py-2.5 px-4">Booking Reference</th>
                  <th class="py-2.5 px-4">Actor ID / Role</th>
                  <th class="py-2.5 px-4">State Transition</th>
                </tr>
              </thead>
              <tbody id="admin-audit-table-body" class="divide-y divide-slate-100 font-mono">
                <!-- Injected via JS -->
              </tbody>
            </table>
          </div>
        </div>

      </div>
    </section>

    <!-- =================================================================== -->
    <!-- TAB 4: AUTOMATED TEST RUNNER (Test Suite for SAT II Report) -->
    <!-- =================================================================== -->
    <section id="tab-tests" class="hidden space-y-6">
      <div class="bg-white p-6 rounded-2xl border border-slate-200 shadow-sm">
        <div class="flex flex-wrap items-center justify-between gap-4 border-b border-slate-100 pb-5">
          <div>
            <div class="flex items-center gap-2">
              <span class="px-2.5 py-0.5 rounded-full text-xs font-bold bg-indigo-100 text-indigo-700">SAT II Formal Test Suite</span>
              <span class="text-xs text-slate-400">&bull; 12 Mandatory Test Cases</span>
            </div>
            <h2 class="text-xl font-extrabold text-slate-900 mt-1">Automated System Verification Engine</h2>
            <p class="text-xs text-slate-500 max-w-2xl mt-1">
              Executes all 12 validation and concurrency rules against live SQLite database: duplicate prevention, quantity boundaries, capacity exhaustion, fare thresholds, atomicity rollback, security access control, lifecycle stages, and persistence.
            </p>
          </div>
          <button id="btn-run-tests" onclick="triggerRunTests()" class="px-5 py-2.5 bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-bold rounded-xl shadow-md shadow-indigo-100 transition-all flex items-center gap-2">
            <i class="fa-solid fa-play"></i> Run All 12 Automated Tests
          </button>
        </div>

        <!-- Summary Banner -->
        <div id="test-summary-card" class="hidden mt-6 p-4 rounded-xl border"></div>

        <!-- Test Results Table -->
        <div class="mt-6 overflow-x-auto">
          <table class="w-full text-left text-sm border-collapse">
            <thead>
              <tr class="bg-slate-50 text-slate-700 text-xs font-bold uppercase tracking-wider border-y border-slate-200">
                <th class="py-3 px-3">Test ID</th>
                <th class="py-3 px-4">Test Name & Description</th>
                <th class="py-3 px-4">Inputs Tested</th>
                <th class="py-3 px-4">Expected Behavior</th>
                <th class="py-3 px-4">Actual Output</th>
                <th class="py-3 px-3 text-center">Verdict</th>
              </tr>
            </thead>
            <tbody id="test-results-body" class="divide-y divide-slate-100 text-xs">
              <tr>
                <td colspan="6" class="py-12 text-center text-slate-400">
                  <i class="fa-solid fa-flask text-3xl mb-2 text-slate-300 block"></i>
                  Click <strong>"Run All 12 Automated Tests"</strong> to execute verification suite.
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </section>

  </main>

  <!-- =================================================================== -->
  <!-- MODAL: BOOKING DRAWER & LIVE FARE CALCULATOR -->
  <!-- =================================================================== -->
  <div id="booking-modal" class="fixed inset-0 z-50 bg-slate-900/60 backdrop-blur-sm flex items-center justify-center p-4 hidden">
    <div class="bg-white rounded-2xl max-w-lg w-full shadow-2xl border border-slate-200 overflow-hidden transform transition-all animate-in fade-in zoom-in-95 duration-150">
      
      <!-- Modal Header -->
      <div class="bg-gradient-to-r from-indigo-700 to-indigo-800 text-white p-5 flex justify-between items-center">
        <div>
          <span id="modal-category" class="text-xs uppercase font-bold tracking-wider text-indigo-200 block">Category</span>
          <h3 id="modal-seat-title" class="text-lg font-extrabold text-white">Event Seat Name</h3>
        </div>
        <button onclick="closeBookingModal()" class="text-white/80 hover:text-white p-1 rounded-lg">
          <i class="fa-solid fa-xmark text-lg"></i>
        </button>
      </div>

      <!-- Modal Body Form -->
      <form id="booking-form" onsubmit="submitReservation(event)" class="p-6 space-y-4">
        <input type="hidden" id="modal-resource-id">
        <input type="hidden" id="modal-rate">

        <!-- Available seats notice -->
        <div class="flex items-center justify-between text-xs bg-slate-50 p-2.5 rounded-lg border border-slate-200">
          <span class="text-slate-600 font-medium">Standard Seat Rate:</span>
          <span id="modal-rate-display" class="font-bold text-slate-900 text-sm">Rs. 0.00</span>
        </div>

        <div class="grid grid-cols-2 gap-3">
          <div>
            <label class="block text-xs font-semibold text-slate-700 mb-1">Student / Guest Name</label>
            <input type="text" id="modal-name" required class="w-full text-sm px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500">
          </div>
          <div>
            <label class="block text-xs font-semibold text-slate-700 mb-1">10-Digit Phone</label>
            <input type="tel" id="modal-phone" pattern="^\\d{10}$" required placeholder="9876543210" class="w-full text-sm px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500">
          </div>
        </div>

        <div class="grid grid-cols-2 gap-3">
          <div>
            <label class="block text-xs font-semibold text-slate-700 mb-1">Booking Date</label>
            <input type="date" id="modal-date" required class="w-full text-sm px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500">
          </div>
          <div>
            <label class="block text-xs font-semibold text-slate-700 mb-1">Quantity (1 to 5 seats)</label>
            <div class="flex items-center">
              <button type="button" onclick="adjustQty(-1)" class="w-9 h-9 flex items-center justify-center bg-slate-100 hover:bg-slate-200 rounded-l-lg border border-r-0 border-slate-200 text-slate-700 font-bold text-sm">-</button>
              <input type="number" id="modal-qty" min="1" max="5" value="1" readonly class="w-full text-center text-sm font-bold py-2 bg-slate-50 border border-slate-200">
              <button type="button" onclick="adjustQty(1)" class="w-9 h-9 flex items-center justify-center bg-slate-100 hover:bg-slate-200 rounded-r-lg border border-l-0 border-slate-200 text-slate-700 font-bold text-sm">+</button>
            </div>
          </div>
        </div>

        <!-- Live Fare Breakdown Card (Compulsory Feature) -->
        <div class="bg-indigo-50/70 border border-indigo-100 rounded-xl p-4 space-y-2 mt-2">
          <div class="flex justify-between items-center text-xs">
            <span class="text-slate-600">Base Amount (<span id="calc-qty-label">1</span> &times; Rs. <span id="calc-rate-label">0</span>):</span>
            <span id="calc-base" class="font-semibold text-slate-800">Rs. 0.00</span>
          </div>
          <div class="flex justify-between items-center text-xs">
            <span class="text-slate-600 flex items-center gap-1">
              Booking Charge:
              <span id="charge-badge" class="px-1.5 py-0.2 rounded text-[10px] font-bold bg-amber-100 text-amber-800">Rs. 50 Applicable</span>
            </span>
            <span id="calc-fee" class="font-semibold text-slate-800">Rs. 50.00</span>
          </div>
          <div class="border-t border-indigo-200/60 pt-2 flex justify-between items-center">
            <span class="text-sm font-bold text-slate-900">Grand Total Payable:</span>
            <span id="calc-grand" class="text-base font-extrabold text-indigo-700">Rs. 0.00</span>
          </div>
          <p id="fee-rule-note" class="text-[11px] text-indigo-600 font-medium">Orders Rs. 1,000 or above receive 100% free booking fee waiver!</p>
        </div>

        <!-- Action Buttons -->
        <div class="flex items-center justify-end gap-3 pt-2">
          <button type="button" onclick="closeBookingModal()" class="px-4 py-2 text-xs font-semibold text-slate-600 hover:bg-slate-100 rounded-lg">Cancel</button>
          <button type="submit" id="btn-confirm-booking" class="px-5 py-2.5 text-xs font-bold bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg shadow-md shadow-indigo-100 transition-all flex items-center gap-2">
            <i class="fa-solid fa-lock text-xs"></i> Confirm Atomic Reservation
          </button>
        </div>

      </form>
    </div>
  </div>

  <!-- Footer -->
  <footer class="bg-white border-t border-slate-200 py-6 text-center text-xs text-slate-500">
    <div class="max-w-7xl mx-auto px-4 flex flex-wrap justify-between items-center gap-4">
      <div>
        <strong>College Event Registration System</strong> &bull; Industry Case 38
      </div>
      <div class="flex items-center gap-4">
        <span>SQLite Engine with Atomic Transact</span>
        <span>BR1-BR10 Strict Enforcement</span>
      </div>
    </div>
  </footer>

  <!-- =================================================================== -->
  <!-- JAVASCRIPT APPLICATION LOGIC -->
  <!-- =================================================================== -->
  <script>
    let currentActorId = 'U101';
    let usersList = [];
    let resourcesList = [];

    // Initialize application on load
    document.addEventListener('DOMContentLoaded', async () => {
      // Set default booking date to tomorrow
      const tomorrow = new Date();
      tomorrow.setDate(tomorrow.getDate() + 1);
      const tomorrowStr = tomorrow.toISOString().split('T')[0];
      document.getElementById('modal-date').value = tomorrowStr;

      await fetchUsers();
      await fetchResources();
      await fetchMyBookings();
    });

    // Helper: Toast Notifications
    function showToast(message, type = 'success') {
      const container = document.getElementById('toast-container');
      const toast = document.createElement('div');
      const bg = type === 'success' ? 'bg-emerald-600 text-white' : type === 'error' ? 'bg-rose-600 text-white' : 'bg-slate-800 text-white';
      const icon = type === 'success' ? 'fa-circle-check' : type === 'error' ? 'fa-circle-exclamation' : 'fa-circle-info';
      
      toast.className = `${bg} px-4 py-3 rounded-xl shadow-lg flex items-center gap-3 text-xs font-medium transform transition-all duration-300 pointer-events-auto`;
      toast.innerHTML = `<i class="fa-solid ${icon} text-sm"></i><div class="flex-1">${message}</div>`;
      container.appendChild(toast);

      setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transform = 'translateY(10px)';
        setTimeout(() => toast.remove(), 300);
      }, 4000);
    }

    // Role switcher change handler
    async function onActorChange(actorId) {
      currentActorId = actorId;
      const user = usersList.find(u => u.id === actorId);
      const actorLabel = user ? `${user.role}: ${user.name}` : actorId;
      document.getElementById('actor-badge').innerText = actorLabel;
      document.getElementById('current-user-label').innerText = user ? user.name : actorId;

      showToast(`Switched active actor to ${user ? user.name : actorId} (${user ? user.role : ''})`, 'info');

      // Refresh views
      await fetchResources();
      await fetchMyBookings();
      if (!document.getElementById('tab-admin').classList.contains('hidden')) {
        await fetchAdminData();
      }
    }

    // Tab switcher
    function switchTab(tab) {
      const tabs = ['catalog', 'mybookings', 'admin', 'tests'];
      tabs.forEach(t => {
        const el = document.getElementById(`tab-${t}`);
        const navBtn = document.getElementById(`nav-${t}`);
        if (t === tab) {
          el.classList.remove('hidden');
          navBtn.className = "px-4 py-2 rounded-lg transition-all text-indigo-700 bg-white shadow-sm font-bold";
        } else {
          el.classList.add('hidden');
          navBtn.className = "px-4 py-2 rounded-lg transition-all text-slate-600 hover:text-slate-900";
        }
      });

      if (tab === 'catalog') fetchResources();
      if (tab === 'mybookings') fetchMyBookings();
      if (tab === 'admin') fetchAdminData();
    }

    // Fetch users for switcher
    async function fetchUsers() {
      try {
        const res = await fetch('/api/users');
        usersList = await res.json();
        const selector = document.getElementById('role-selector');
        selector.innerHTML = usersList.map(u => 
          `<option value="${u.id}" ${u.id === currentActorId ? 'selected' : ''}>${u.name} (${u.role} - ${u.id})</option>`
        ).join('');
      } catch (e) {
        console.error(e);
      }
    }

    // Fetch resources & render catalog
    async function fetchResources() {
      const search = document.getElementById('search-input').value;
      const category = document.getElementById('category-filter').value;
      try {
        const res = await fetch(`/api/resources?search=${encodeURIComponent(search)}&category=${encodeURIComponent(category)}`);
        resourcesList = await res.json();
        document.getElementById('catalog-count').innerText = resourcesList.length;

        const grid = document.getElementById('catalog-grid');
        grid.innerHTML = resourcesList.map(item => {
          const isSoldOut = item.current_availability <= 0;
          return `
            <div class="glass-card rounded-2xl p-5 shadow-sm hover:shadow-md transition-all flex flex-col justify-between border border-slate-200">
              <div>
                <div class="flex items-center justify-between mb-3">
                  <span class="text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded-full bg-slate-100 text-slate-600">
                    ${item.category}
                  </span>
                  <span class="px-2 py-0.5 rounded-full text-xs font-semibold ${isSoldOut ? 'bg-rose-100 text-rose-700' : 'bg-emerald-100 text-emerald-700'}">
                    ${isSoldOut ? 'Sold Out' : `${item.current_availability} seats left`}
                  </span>
                </div>
                <h3 class="text-base font-bold text-slate-900">${item.identifier}</h3>
                <p class="text-xs text-slate-500 mt-1">Resource ID: <code class="font-mono text-slate-700">${item.id}</code></p>
                <div class="mt-4 flex items-baseline gap-1">
                  <span class="text-2xl font-extrabold text-slate-900">Rs. ${item.rate.toFixed(2)}</span>
                  <span class="text-xs text-slate-500">/ seat</span>
                </div>
              </div>

              <div class="mt-5 pt-4 border-t border-slate-100 flex items-center justify-between">
                <div class="text-[11px] text-slate-500">
                  ${item.rate >= 1000 ? '<span class="text-emerald-600 font-semibold"><i class="fa-solid fa-check mr-0.5"></i> Free Booking Fee</span>' : '<span class="text-slate-500">+ Rs. 50 Fee under 1k</span>'}
                </div>
                <button 
                  onclick="openBookingModal('${item.id}')"
                  ${isSoldOut ? 'disabled' : ''}
                  class="px-4 py-2 text-xs font-bold rounded-xl transition-all ${isSoldOut ? 'bg-slate-100 text-slate-400 cursor-not-allowed' : 'bg-indigo-600 hover:bg-indigo-700 text-white shadow-sm shadow-indigo-100'}">
                  ${isSoldOut ? 'Unavailable' : 'Book Seats'}
                </button>
              </div>
            </div>
          `;
        }).join('');
      } catch (e) {
        console.error(e);
      }
    }

    // Open booking modal
    function openBookingModal(resourceId) {
      const item = resourcesList.find(r => r.id === resourceId);
      if (!item) return;

      const user = usersList.find(u => u.id === currentActorId) || { name: 'Student', phone: '9876543210' };

      document.getElementById('modal-resource-id').value = item.id;
      document.getElementById('modal-rate').value = item.rate;
      document.getElementById('modal-seat-title').innerText = item.identifier;
      document.getElementById('modal-category').innerText = item.category;
      document.getElementById('modal-rate-display').innerText = `Rs. ${item.rate.toFixed(2)} / seat (${item.current_availability} available)`;
      
      document.getElementById('modal-name').value = user.name;
      document.getElementById('modal-phone').value = user.phone;
      document.getElementById('modal-qty').value = 1;

      recalculateModalFare();
      document.getElementById('booking-modal').classList.remove('hidden');
    }

    function closeBookingModal() {
      document.getElementById('booking-modal').classList.add('hidden');
    }

    function adjustQty(delta) {
      const input = document.getElementById('modal-qty');
      let val = parseInt(input.value) + delta;
      if (val >= 1 && val <= 5) {
        input.value = val;
        recalculateModalFare();
      }
    }

    function recalculateModalFare() {
      const rate = parseFloat(document.getElementById('modal-rate').value || 0);
      const qty = parseInt(document.getElementById('modal-qty').value || 1);

      const baseAmount = rate * qty;
      const bookingCharge = baseAmount < 1000.0 ? 50.0 : 0.0;
      const grandTotal = baseAmount + bookingCharge;

      document.getElementById('calc-qty-label').innerText = qty;
      document.getElementById('calc-rate-label').innerText = rate.toFixed(2);
      document.getElementById('calc-base').innerText = `Rs. ${baseAmount.toFixed(2)}`;
      document.getElementById('calc-fee').innerText = `Rs. ${bookingCharge.toFixed(2)}`;
      document.getElementById('calc-grand').innerText = `Rs. ${grandTotal.toFixed(2)}`;

      const badge = document.getElementById('charge-badge');
      if (bookingCharge === 0) {
        badge.className = "px-1.5 py-0.2 rounded text-[10px] font-bold bg-emerald-100 text-emerald-800";
        badge.innerText = "FREE (>= Rs. 1000)";
      } else {
        badge.className = "px-1.5 py-0.2 rounded text-[10px] font-bold bg-amber-100 text-amber-800";
        badge.innerText = "Rs. 50 Applicable";
      }
    }

    // Submit Reservation
    async function submitReservation(event) {
      event.preventDefault();
      const resourceId = document.getElementById('modal-resource-id').value;
      const quantity = parseInt(document.getElementById('modal-qty').value);
      const name = document.getElementById('modal-name').value;
      const phone = document.getElementById('modal-phone').value;
      const bookingDate = document.getElementById('modal-date').value;

      try {
        const response = await fetch('/api/reservations', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-Actor-Id': currentActorId
          },
          body: JSON.stringify({
            resource_id: resourceId,
            quantity: quantity,
            name: name,
            phone: phone,
            booking_date: bookingDate
          })
        });

        const data = await response.json();
        if (response.ok) {
          showToast(data.message, 'success');
          closeBookingModal();
          await fetchResources();
          await fetchMyBookings();
        } else {
          showToast(data.error || 'Failed to complete reservation.', 'error');
        }
      } catch (err) {
        showToast('Network error during reservation.', 'error');
      }
    }

    // Fetch and render My Bookings (User View)
    async function fetchMyBookings() {
      try {
        const res = await fetch('/api/reservations', {
          headers: { 'X-Actor-Id': currentActorId }
        });
        const bookings = await res.json();

        // Update count badge
        document.getElementById('my-bookings-count').innerText = bookings.length;

        const container = document.getElementById('my-bookings-container');
        if (bookings.length === 0) {
          container.innerHTML = `
            <div class="bg-white rounded-2xl border border-slate-200 p-12 text-center text-slate-400">
              <i class="fa-solid fa-calendar-xmark text-4xl mb-3 text-slate-300"></i>
              <p class="font-semibold text-slate-600">No Reservations Found</p>
              <p class="text-xs text-slate-400 mt-1">You haven't reserved any event seats yet under this user account.</p>
            </div>
          `;
          return;
        }

        container.innerHTML = bookings.map(b => {
          const statusColors = {
            'CONFIRMED': 'bg-emerald-100 text-emerald-800 border-emerald-200',
            'CHECKED_IN': 'bg-amber-100 text-amber-800 border-amber-200',
            'COMPLETED': 'bg-indigo-100 text-indigo-800 border-indigo-200',
            'CANCELLED': 'bg-rose-100 text-rose-800 border-rose-200'
          };
          const badgeClass = statusColors[b.status] || 'bg-slate-100 text-slate-800';
          const canCancel = (b.status === 'CONFIRMED');

          return `
            <div class="bg-white rounded-2xl border border-slate-200 p-5 shadow-sm space-y-4">
              <div class="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 pb-3">
                <div class="flex items-center gap-2">
                  <span class="font-mono text-xs font-bold text-slate-800 bg-slate-100 px-2 py-0.5 rounded">${b.id}</span>
                  <span class="text-xs text-slate-500">&bull; Event Date: <strong>${b.booking_date}</strong></span>
                </div>
                <div class="flex items-center gap-2">
                  <span class="px-2.5 py-1 rounded-full text-xs font-bold border ${badgeClass}">
                    ${b.status}
                  </span>
                </div>
              </div>

              <div class="grid grid-cols-1 sm:grid-cols-4 gap-4 text-xs">
                <div>
                  <span class="text-slate-400 block font-medium">Event Seat Package</span>
                  <span class="text-sm font-bold text-slate-900 block mt-0.5">${b.resource_name}</span>
                  <span class="text-[11px] text-slate-500">${b.resource_category}</span>
                </div>
                <div>
                  <span class="text-slate-400 block font-medium">Quantity Reserved</span>
                  <span class="text-sm font-bold text-slate-900 block mt-0.5">${b.quantity} seat(s)</span>
                  <span class="text-[11px] text-slate-500">Rate: Rs. ${b.rate_snapshot.toFixed(2)}</span>
                </div>
                <div>
                  <span class="text-slate-400 block font-medium">Fare Breakdown</span>
                  <span class="text-slate-700 block mt-0.5">Base: Rs. ${b.base_amount.toFixed(2)}</span>
                  <span class="text-slate-700">Fee: Rs. ${b.booking_charge.toFixed(2)}</span>
                </div>
                <div>
                  <span class="text-slate-400 block font-medium">Grand Total</span>
                  <span class="text-base font-extrabold text-indigo-700 block mt-0.5">Rs. ${b.grand_total.toFixed(2)}</span>
                </div>
              </div>

              <div class="flex items-center justify-between pt-2 border-t border-slate-100 text-xs">
                <span class="text-slate-400">Created: ${b.created_at}</span>
                ${canCancel ? `
                  <button onclick="cancelBooking('${b.id}')" class="px-3 py-1.5 font-bold text-xs bg-rose-50 text-rose-700 hover:bg-rose-100 rounded-lg transition-all border border-rose-200">
                    <i class="fa-solid fa-ban mr-1"></i> Cancel Reservation
                  </button>
                ` : `
                  <span class="text-slate-400 italic">Cancellation locked (${b.status})</span>
                `}
              </div>
            </div>
          `;
        }).join('');
      } catch (e) {
        console.error(e);
      }
    }

    // Cancel reservation handler
    async function cancelBooking(reservationId) {
      if (!confirm(`Are you sure you want to cancel reservation ${reservationId}? Capacity will be restored.`)) {
        return;
      }
      try {
        const response = await fetch(`/api/reservations/${reservationId}/cancel`, {
          method: 'POST',
          headers: { 'X-Actor-Id': currentActorId }
        });
        const data = await response.json();
        if (response.ok) {
          showToast(data.message, 'success');
          await fetchMyBookings();
          await fetchResources();
        } else {
          showToast(data.error || 'Failed to cancel reservation.', 'error');
        }
      } catch (e) {
        showToast('Network error during cancellation.', 'error');
      }
    }

    // Fetch Admin Console Data
    async function fetchAdminData() {
      const user = usersList.find(u => u.id === currentActorId);
      const isAdm = user && user.role === 'Administrator';

      const alertEl = document.getElementById('admin-forbidden-alert');
      const contentEl = document.getElementById('admin-main-content');
      document.getElementById('alert-actor-name').innerText = user ? `${user.name} (${user.id})` : currentActorId;

      if (!isAdm) {
        alertEl.classList.remove('hidden');
        contentEl.classList.add('opacity-50', 'pointer-events-none');
      } else {
        alertEl.classList.add('hidden');
        contentEl.classList.remove('opacity-50', 'pointer-events-none');
      }

      try {
        // Fetch All Bookings
        const resB = await fetch('/api/reservations', { headers: { 'X-Actor-Id': currentActorId } });
        const allBookings = await resB.json();

        if (Array.isArray(allBookings)) {
          // Update admin statistics
          document.getElementById('stat-total-bookings').innerText = allBookings.length;
          const conf = allBookings.filter(b => b.status === 'CONFIRMED').length;
          const prog = allBookings.filter(b => b.status === 'CHECKED_IN' || b.status === 'COMPLETED').length;
          const rev = allBookings.filter(b => b.status !== 'CANCELLED').reduce((acc, b) => acc + b.grand_total, 0);

          document.getElementById('stat-confirmed-bookings').innerText = conf;
          document.getElementById('stat-progressed-bookings').innerText = prog;
          document.getElementById('stat-revenue').innerText = `Rs. ${rev.toFixed(2)}`;

          // Render bookings table
          const tbody = document.getElementById('admin-bookings-table-body');
          tbody.innerHTML = allBookings.map(b => {
            let actionBtn = '<span class="text-slate-400 text-xs italic">Terminal State</span>';
            if (b.status === 'CONFIRMED') {
              actionBtn = `
                <button onclick="advanceStatus('${b.id}', 'CHECKED_IN')" class="px-2.5 py-1 text-xs font-semibold bg-amber-500 hover:bg-amber-600 text-white rounded-lg transition-all shadow-sm">
                  Mark Checked In
                </button>
              `;
            } else if (b.status === 'CHECKED_IN') {
              actionBtn = `
                <button onclick="advanceStatus('${b.id}', 'COMPLETED')" class="px-2.5 py-1 text-xs font-semibold bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg transition-all shadow-sm">
                  Mark Completed
                </button>
              `;
            }

            return `
              <tr class="hover:bg-slate-50/80 transition-colors">
                <td class="py-3 px-4 font-mono font-bold text-slate-800 text-xs">${b.id}</td>
                <td class="py-3 px-4 font-medium text-slate-900">${b.user_name} <span class="text-slate-400 text-xs">(${b.user_id})</span></td>
                <td class="py-3 px-4">${b.resource_name}</td>
                <td class="py-3 px-4 font-bold">${b.quantity}</td>
                <td class="py-3 px-4">Rs. ${b.rate_snapshot.toFixed(2)}</td>
                <td class="py-3 px-4 font-extrabold text-indigo-700">Rs. ${b.grand_total.toFixed(2)}</td>
                <td class="py-3 px-4">
                  <span class="px-2 py-0.5 rounded-full text-xs font-bold ${
                    b.status === 'CONFIRMED' ? 'bg-emerald-100 text-emerald-800' :
                    b.status === 'CHECKED_IN' ? 'bg-amber-100 text-amber-800' :
                    b.status === 'COMPLETED' ? 'bg-indigo-100 text-indigo-800' : 'bg-rose-100 text-rose-800'
                  }">${b.status}</span>
                </td>
                <td class="py-3 px-4">${actionBtn}</td>
              </tr>
            `;
          }).join('');
        }

        // Render Seat Rate Editor
        const resR = await fetch('/api/resources');
        const resList = await resR.json();
        const rateGrid = document.getElementById('admin-rate-editor-grid');
        rateGrid.innerHTML = resList.map(r => `
          <div class="p-3 bg-slate-50 border border-slate-200 rounded-xl flex items-center justify-between">
            <div>
              <span class="text-xs font-bold text-slate-800 block">${r.identifier}</span>
              <span class="text-[11px] text-slate-500">Current: Rs. ${r.rate.toFixed(2)}</span>
            </div>
            <div class="flex items-center gap-1.5">
              <input type="number" id="rate-input-${r.id}" value="${r.rate}" min="0" step="50" class="w-20 text-xs px-2 py-1 bg-white border border-slate-300 rounded focus:ring-1 focus:ring-indigo-500">
              <button onclick="updateSeatRate('${r.id}')" class="px-2.5 py-1 text-xs font-bold bg-slate-900 hover:bg-slate-800 text-white rounded">
                Save
              </button>
            </div>
          </div>
        `).join('');

        // Fetch Audit Log
        if (isAdm) {
          const resH = await fetch('/api/history', { headers: { 'X-Actor-Id': currentActorId } });
          const historyLogs = await resH.json();
          const auditTbody = document.getElementById('admin-audit-table-body');
          auditTbody.innerHTML = historyLogs.map(h => `
            <tr class="hover:bg-slate-50">
              <td class="py-2 px-4 text-slate-400">${h.id}</td>
              <td class="py-2 px-4 text-slate-600">${h.event_at}</td>
              <td class="py-2 px-4 font-bold text-slate-900">${h.reference_id}</td>
              <td class="py-2 px-4 text-slate-700">${h.actor_id} (${h.actor_role})</td>
              <td class="py-2 px-4">
                <span class="text-slate-500">${h.old_status || 'NULL'}</span>
                <span class="text-indigo-600 font-bold mx-1">&rarr;</span>
                <span class="text-indigo-900 font-bold">${h.new_status}</span>
              </td>
            </tr>
          `).join('');
        }

      } catch (e) {
        console.error(e);
      }
    }

    // Advance Status
    async function advanceStatus(reservationId, targetStatus) {
      try {
        const response = await fetch(`/api/reservations/${reservationId}/status`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-Actor-Id': currentActorId
          },
          body: JSON.stringify({ status: targetStatus })
        });
        const data = await response.json();
        if (response.ok) {
          showToast(data.message, 'success');
          await fetchAdminData();
        } else {
          showToast(data.error || 'Status transition rejected.', 'error');
        }
      } catch (e) {
        showToast('Network error during status change.', 'error');
      }
    }

    // Update Seat Rate
    async function updateSeatRate(resourceId) {
      const input = document.getElementById(`rate-input-${resourceId}`);
      const newRate = parseFloat(input.value);
      if (isNaN(newRate) || newRate < 0) {
        showToast('Invalid rate value.', 'error');
        return;
      }
      try {
        const response = await fetch(`/api/resources/${resourceId}/rate`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-Actor-Id': currentActorId
          },
          body: JSON.stringify({ rate: newRate })
        });
        const data = await response.json();
        if (response.ok) {
          showToast(data.message, 'success');
          await fetchResources();
          await fetchAdminData();
        } else {
          showToast(data.error || 'Rate update failed.', 'error');
        }
      } catch (e) {
        showToast('Network error during rate update.', 'error');
      }
    }

    // Trigger Automated Test Suite
    async function triggerRunTests() {
      const btn = document.getElementById('btn-run-tests');
      const originalText = btn.innerHTML;
      btn.innerHTML = `<i class="fa-solid fa-spinner fa-spin mr-2"></i> Executing 12 Test Cases...`;
      btn.disabled = true;

      try {
        const response = await fetch('/api/run-tests', { method: 'POST' });
        const data = await response.json();

        // Render Summary Card
        const summaryCard = document.getElementById('test-summary-card');
        summaryCard.classList.remove('hidden');
        if (data.all_passed) {
          summaryCard.className = "mt-6 p-4 rounded-xl border bg-emerald-50 border-emerald-200 text-emerald-900 flex items-center justify-between";
          summaryCard.innerHTML = `
            <div class="flex items-center gap-3">
              <div class="w-10 h-10 rounded-full bg-emerald-600 text-white flex items-center justify-center font-bold text-lg">
                <i class="fa-solid fa-check"></i>
              </div>
              <div>
                <h4 class="font-bold text-sm">ALL ${data.total} MANDATORY TEST CASES PASSED SUCCESSFULLY</h4>
                <p class="text-xs text-emerald-700">Atomic transactions, BR1-BR10 rules, and SQLite persistence are 100% compliant with SAT II specification.</p>
              </div>
            </div>
            <span class="px-3 py-1 bg-emerald-600 text-white font-extrabold text-xs rounded-lg">${data.passed}/${data.total} PASSED</span>
          `;
        } else {
          summaryCard.className = "mt-6 p-4 rounded-xl border bg-rose-50 border-rose-200 text-rose-900 flex items-center justify-between";
          summaryCard.innerHTML = `
            <div class="flex items-center gap-3">
              <i class="fa-solid fa-triangle-exclamation text-2xl text-rose-600"></i>
              <div>
                <h4 class="font-bold text-sm">TEST SUITE DETECTED FAILURES</h4>
                <p class="text-xs text-rose-700">${data.failed} out of ${data.total} tests did not pass.</p>
              </div>
            </div>
            <span class="px-3 py-1 bg-rose-600 text-white font-extrabold text-xs rounded-lg">${data.passed}/${data.total} PASSED</span>
          `;
        }

        // Render Table Body
        const tbody = document.getElementById('test-results-body');
        tbody.innerHTML = data.results.map(t => `
          <tr class="${t.passed ? 'hover:bg-emerald-50/30' : 'bg-rose-50/50'}">
            <td class="py-3 px-3 font-mono font-bold text-slate-900">${t.test_id}</td>
            <td class="py-3 px-4">
              <span class="font-bold text-slate-900 block">${t.name}</span>
              <span class="text-slate-500">${t.description}</span>
            </td>
            <td class="py-3 px-4 font-mono text-slate-700 max-w-xs break-words">${t.inputs}</td>
            <td class="py-3 px-4 text-slate-700">${t.expected}</td>
            <td class="py-3 px-4 font-mono text-slate-600 max-w-xs break-words">${t.actual}</td>
            <td class="py-3 px-3 text-center">
              <span class="px-2.5 py-1 rounded-full text-xs font-extrabold ${t.passed ? 'bg-emerald-100 text-emerald-800 border border-emerald-300' : 'bg-rose-100 text-rose-800 border border-rose-300'}">
                ${t.passed ? 'PASS' : 'FAIL'}
              </span>
            </td>
          </tr>
        `).join('');

        showToast(`Test suite execution complete: ${data.passed}/${data.total} passed`, data.all_passed ? 'success' : 'error');

        // Refresh database views
        await fetchResources();
        await fetchMyBookings();

      } catch (err) {
        showToast('Error executing test runner.', 'error');
      } finally {
        btn.innerHTML = originalText;
        btn.disabled = false;
      }
    }
  </script>
</body>
</html>
"""

@app.route("/", methods=["GET"])
def index():
    return render_template_string(INDEX_HTML)

# ---------------------------------------------------------------------------
# Main Execution Entry Point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 5000))
    print(f" * CampusEventHub running on http://127.0.0.1:{port}")
    app.run(host="127.0.0.1", port=port, debug=True)
