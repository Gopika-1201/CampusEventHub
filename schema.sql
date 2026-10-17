-- Industry Use Case 38: College Event Registration System
-- SQLite Schema Definition

PRAGMA foreign_keys = ON;

-- Users table
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('User', 'Administrator')),
    status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK(status IN ('ACTIVE', 'INACTIVE')),
    phone TEXT NOT NULL
);

-- Resources (Event Seats) table
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

-- Reservations table
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

-- Reservation Items table (snapshot line item)
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

-- Booking Status History audit trail table
CREATE TABLE IF NOT EXISTS booking_status_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reference_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    old_status TEXT,
    new_status TEXT NOT NULL,
    event_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (reference_id) REFERENCES reservations(id) ON DELETE CASCADE
);

-- Seed Data (Initial inserts only if not existing)
INSERT OR IGNORE INTO users (id, name, role, status, phone) VALUES
('U101', 'Alice', 'User', 'ACTIVE', '9876543210'),
('U102', 'Bob', 'User', 'ACTIVE', '9123456789'),
('ADM01', 'Admin Officer', 'Administrator', 'ACTIVE', '9999999999');

INSERT OR IGNORE INTO resources (id, identifier, category, rate, initial_availability, current_availability, status) VALUES
('R101', 'Event Seat A', 'Technical Keynote', 499.0, 10, 10, 'ACTIVE'),
('R102', 'Event Seat B', 'Workshop Hands-on', 799.0, 5, 5, 'ACTIVE'),
('R103', 'Event Seat C', 'Cultural Pass', 199.0, 20, 20, 'ACTIVE'),
('R104', 'Event Seat D', 'VIP Masterclass', 1299.0, 0, 0, 'ACTIVE'),
('R105', 'Event Seat E', 'Hackathon Entry', 1000.0, 3, 3, 'ACTIVE');
