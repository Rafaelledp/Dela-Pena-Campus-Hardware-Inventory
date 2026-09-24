import csv
import logging
import os
import re
import sqlite3
import time
from pathlib import Path

import bcrypt
import tkinter as tk
from tkinter import messagebox, filedialog, simpledialog, ttk

from pydantic import BaseModel, Field, ValidationError, field_validator


# ============================================================
# CONFIGURATION
# ============================================================

APP_NAME = "Campus Hardware Asset Management System"
DB_NAME = "hardware_inventory.db"

CONDITIONS = [
    "New",
    "Good",
    "Fair",
    "Damaged",
    "Under Repair",
]


# ============================================================
# LOGGER
# ============================================================

def setup_logger():
    log_dir = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "app_logging"
    )

    os.makedirs(log_dir, exist_ok=True)

    logging.basicConfig(
        filename=os.path.join(log_dir, "app.log"),
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    return logging.getLogger("HardwareInventory")


logger = setup_logger()


# ============================================================
# DATABASE PATH
# ============================================================

def get_db_path(db_name=DB_NAME):
    if Path(db_name).is_absolute():
        return Path(db_name)

    return Path(__file__).parent / db_name


# ============================================================
# DATABASE INITIALIZATION
# ============================================================

def init_db(db_name=DB_NAME):
    db_path = get_db_path(db_name)

    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()

        # ----------------------------------------------------
        # USERS
        # ----------------------------------------------------

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                failed_attempts INTEGER NOT NULL DEFAULT 0,
                lockout_until REAL NOT NULL DEFAULT 0,
                role TEXT NOT NULL DEFAULT 'USER'
            )
            """
        )

        cursor.execute("PRAGMA table_info(users)")
        user_columns = {row[1] for row in cursor.fetchall()}

        if "role" not in user_columns:
            cursor.execute(
                "ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'USER'"
            )

        # ----------------------------------------------------
        # HARDWARE
        # ----------------------------------------------------

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS hardware (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                category TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                unit_price REAL NOT NULL,
                status TEXT NOT NULL,
                condition TEXT NOT NULL DEFAULT 'Good',
                last_returned REAL DEFAULT 0,
                remarks TEXT DEFAULT ''
            )
            """
        )

        # Upgrade an older hardware table
        cursor.execute("PRAGMA table_info(hardware)")
        hardware_columns = {row[1] for row in cursor.fetchall()}

        if "condition" not in hardware_columns:
            cursor.execute(
                """
                ALTER TABLE hardware
                ADD COLUMN condition TEXT NOT NULL DEFAULT 'Good'
                """
            )

        if "last_returned" not in hardware_columns:
            cursor.execute(
                """
                ALTER TABLE hardware
                ADD COLUMN last_returned REAL DEFAULT 0
                """
            )

        if "remarks" not in hardware_columns:
            cursor.execute(
                """
                ALTER TABLE hardware
                ADD COLUMN remarks TEXT DEFAULT ''
                """
            )

        # ----------------------------------------------------
        # PASSWORD RESET REQUESTS
        # ----------------------------------------------------

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS password_reset_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                email TEXT NOT NULL,
                requested_at REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'PENDING',
                admin_comment TEXT DEFAULT '',
                approved_by TEXT DEFAULT ''
            )
            """
        )

        # ----------------------------------------------------
        # EQUIPMENT RETURNS
        # ----------------------------------------------------

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS equipment_returns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                hardware_id INTEGER NOT NULL,
                item_name TEXT NOT NULL,
                returned_by TEXT NOT NULL,
                returned_quantity INTEGER NOT NULL,
                return_condition TEXT NOT NULL,
                remarks TEXT DEFAULT '',
                returned_at REAL NOT NULL,
                FOREIGN KEY (hardware_id) REFERENCES hardware(id)
            )
            """
        )

        # ----------------------------------------------------
        # DEFAULT ADMIN ACCOUNT
        # ----------------------------------------------------

        cursor.execute(
            "SELECT 1 FROM users WHERE username = ?",
            ("admin",)
        )

        if cursor.fetchone() is None:
            admin_hash = bcrypt.hashpw(
                "Admin@123".encode("utf-8"),
                bcrypt.gensalt()
            ).decode("utf-8")

            cursor.execute(
                """
                INSERT INTO users
                (
                    username,
                    email,
                    password_hash,
                    role,
                    failed_attempts,
                    lockout_until
                )
                VALUES (?, ?, ?, 'ADMIN', 0, 0)
                """,
                (
                    "admin",
                    "admin@campus.local",
                    admin_hash,
                )
            )

            logger.info(
                "Default administrator account created."
            )

        conn.commit()
        conn.close()

        logger.info(
            "Database initialized successfully: %s",
            db_path
        )

    except sqlite3.Error as exc:
        logger.error(
            "Database initialization error: %s",
            exc
        )


# ============================================================
# PYDANTIC MODELS
# ============================================================

class UserRegisterSchema(BaseModel):
    username: str = Field(
        ...,
        min_length=3,
        max_length=20
    )

    email: str = Field(...)

    password: str = Field(
        ...,
        min_length=8
    )

    role: str = "USER"

    @field_validator("username")
    @classmethod
    def validate_username(cls, value):
        value = value.strip()

        if not re.match(
            r"^[a-zA-Z0-9_]+$",
            value
        ):
            raise ValueError(
                "Username must contain only letters, numbers, and underscores."
            )

        return value

    @field_validator("email")
    @classmethod
    def validate_email(cls, value):
        value = value.strip()

        if not re.match(
            r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
            value
        ):
            raise ValueError(
                "Please enter a valid email address."
            )

        return value

    @field_validator("password")
    @classmethod
    def validate_password(cls, value):
        if not re.search(r"[A-Z]", value):
            raise ValueError(
                "Password must contain at least one uppercase letter."
            )

        if not re.search(r"[0-9]", value):
            raise ValueError(
                "Password must contain at least one number."
            )

        if not re.search(r"[@#$%^&*]", value):
            raise ValueError(
                "Password must contain at least one special character."
            )

        return value

    @field_validator("role")
    @classmethod
    def validate_role(cls, value):
        value = (value or "USER").strip().upper()

        if value not in {"USER", "ADMIN"}:
            raise ValueError(
                "Role must be USER or ADMIN."
            )

        return value


class HardwareSchema(BaseModel):
    item_name: str = Field(
        ...,
        min_length=2,
        max_length=100
    )

    category: str = Field(
        ...,
        min_length=2,
        max_length=50
    )

    quantity: int = Field(
        ...,
        ge=0
    )

    unit_price: float = Field(
        ...,
        ge=0
    )

    condition: str = "Good"

    remarks: str = ""

    @field_validator("item_name", "category")
    @classmethod
    def clean_text(cls, value):
        value = value.strip()

        if not value:
            raise ValueError(
                "This field cannot be empty."
            )

        return value

    @field_validator("condition")
    @classmethod
    def validate_condition(cls, value):
        if value not in CONDITIONS:
            raise ValueError(
                "Invalid equipment condition."
            )

        return value


class ReturnSchema(BaseModel):
    hardware_id: int
    returned_quantity: int = Field(
        ...,
        ge=1
    )
    return_condition: str
    remarks: str = ""

    @field_validator("return_condition")
    @classmethod
    def validate_condition(cls, value):
        if value not in CONDITIONS:
            raise ValueError(
                "Invalid return condition."
            )

        return value


# ============================================================
# AUTH CONTROLLER
# ============================================================

class AuthController:

    LOCKOUT_THRESHOLD = 3

    def __init__(self, db_name=DB_NAME):
        self.db_name = str(
            get_db_path(db_name)
        )

    def _connect(self):
        conn = sqlite3.connect(
            self.db_name,
            timeout=10
        )

        conn.execute(
            "PRAGMA busy_timeout = 5000"
        )

        return conn

    @staticmethod
    def friendly_validation_message(messages):
        combined = " ".join(
            str(x or "")
            for x in messages
        ).lower()

        if "email" in combined:
            return "Please enter a valid email address."

        if "username" in combined:
            return (
                "Username can only use letters, numbers, "
                "and underscores."
            )

        if "uppercase" in combined:
            return (
                "Password needs at least one uppercase letter."
            )

        if "number" in combined:
            return (
                "Password needs at least one number."
            )

        if "special" in combined:
            return (
                "Password needs at least one special character."
            )

        if "at least 8" in combined:
            return (
                "Password must be at least 8 characters long."
            )

        return "Please check your details and try again."

    # --------------------------------------------------------
    # REGISTER
    # --------------------------------------------------------

    def register_user(
        self,
        username,
        email,
        password,
        role="USER"
    ):
        # Public registration is intentionally USER only.
        role = "USER"

        try:
            validated = UserRegisterSchema(
                username=username,
                email=email,
                password=password,
                role=role
            )
        except ValidationError as exc:
            messages = [
                error.get("msg", "")
                for error in exc.errors()
            ]

            return False, self.friendly_validation_message(
                messages
            )

        password_hash = bcrypt.hashpw(
            validated.password.encode("utf-8"),
            bcrypt.gensalt()
        ).decode("utf-8")

        try:
            conn = self._connect()
            cursor = conn.cursor()

            cursor.execute(
                "SELECT 1 FROM users WHERE username = ?",
                (validated.username,)
            )

            if cursor.fetchone():
                conn.close()
                return False, "Username already taken."

            cursor.execute(
                "SELECT 1 FROM users WHERE email = ?",
                (validated.email,)
            )

            if cursor.fetchone():
                conn.close()
                return False, "Email already taken."

            cursor.execute(
                """
                INSERT INTO users
                (
                    username,
                    email,
                    password_hash,
                    role
                )
                VALUES (?, ?, ?, 'USER')
                """,
                (
                    validated.username,
                    validated.email,
                    password_hash
                )
            )

            conn.commit()
            conn.close()

            logger.info(
                "New user registered: %s",
                validated.username
            )

            return True, (
                "Registration successful. "
                "You may now log in."
            )

        except sqlite3.Error as exc:
            logger.error(
                "Registration error: %s",
                exc
            )

            return False, (
                "Unable to register account right now."
            )

    # --------------------------------------------------------
    # LOGIN
    # --------------------------------------------------------

    def login_user(
        self,
        username,
        password
    ):
        if not username or not password:
            return False, (
                "Please enter both username and password."
            )

        try:
            conn = self._connect()
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT
                    password_hash,
                    failed_attempts,
                    lockout_until,
                    role
                FROM users
                WHERE username = ?
                """,
                (username,)
            )

            row = cursor.fetchone()

            if not row:
                conn.close()

                return False, (
                    "Username not found."
                )

            password_hash, failed, lockout, role = row

            now = time.time()

            if lockout and now < lockout:
                conn.close()

                return False, (
                    "Account locked after repeated failed "
                    "login attempts. Please request an unlock."
                )

            if bcrypt.checkpw(
                password.encode("utf-8"),
                password_hash.encode("utf-8")
            ):
                cursor.execute(
                    """
                    UPDATE users
                    SET failed_attempts = 0,
                        lockout_until = 0
                    WHERE username = ?
                    """,
                    (username,)
                )

                conn.commit()
                conn.close()

                logger.info(
                    "Successful login: %s (%s)",
                    username,
                    role
                )

                return True, {
                    "username": username,
                    "role": role
                }

            failed += 1

            if failed >= self.LOCKOUT_THRESHOLD:
                cursor.execute(
                    """
                    UPDATE users
                    SET failed_attempts = ?,
                        lockout_until = ?
                    WHERE username = ?
                    """,
                    (
                        failed,
                        1.0,
                        username
                    )
                )

                conn.commit()
                conn.close()

                logger.warning(
                    "Account locked: %s",
                    username
                )

                return False, (
                    "Account locked after 3 unsuccessful "
                    "login attempts. Please request an unlock."
                )

            cursor.execute(
                """
                UPDATE users
                SET failed_attempts = ?
                WHERE username = ?
                """,
                (
                    failed,
                    username
                )
            )

            conn.commit()
            conn.close()

            return False, (
                "Password is incorrect."
            )

        except sqlite3.Error as exc:
            logger.error(
                "Login database error: %s",
                exc
            )

            return False, (
                "The system is busy. Please try again."
            )

    # --------------------------------------------------------
    # PROFILE
    # --------------------------------------------------------

    def get_user_profile(self, username):
        try:
            conn = self._connect()
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT username, email, role
                FROM users
                WHERE username = ?
                """,
                (username,)
            )

            row = cursor.fetchone()
            conn.close()

            if not row:
                return None

            return {
                "username": row[0],
                "email": row[1],
                "role": row[2]
            }

        except sqlite3.Error:
            return None

    # --------------------------------------------------------
    # CHANGE PASSWORD
    # --------------------------------------------------------

    def change_password(
        self,
        username,
        new_password
    ):
        try:
            UserRegisterSchema(
                username=username,
                email="placeholder@example.com",
                password=new_password,
                role="USER"
            )
        except ValidationError as exc:
            messages = [
                error.get("msg", "")
                for error in exc.errors()
            ]

            return False, self.friendly_validation_message(
                messages
            )

        password_hash = bcrypt.hashpw(
            new_password.encode("utf-8"),
            bcrypt.gensalt()
        ).decode("utf-8")

        try:
            conn = self._connect()

            conn.execute(
                """
                UPDATE users
                SET password_hash = ?,
                    failed_attempts = 0,
                    lockout_until = 0
                WHERE username = ?
                """,
                (
                    password_hash,
                    username
                )
            )

            conn.commit()
            conn.close()

            return True, (
                "Password updated successfully."
            )

        except sqlite3.Error:
            return False, (
                "Unable to update password."
            )

    # --------------------------------------------------------
    # RESET REQUEST
    # --------------------------------------------------------

    def request_password_reset(
        self,
        username,
        email
    ):
        try:
            conn = self._connect()
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT 1
                FROM users
                WHERE username = ?
                AND email = ?
                """,
                (
                    username,
                    email
                )
            )

            if not cursor.fetchone():
                conn.close()

                return False, (
                    "Username and email do not match."
                )

            cursor.execute(
                """
                INSERT INTO password_reset_requests
                (
                    username,
                    email,
                    requested_at,
                    status
                )
                VALUES (?, ?, ?, 'PENDING')
                """,
                (
                    username,
                    email,
                    time.time()
                )
            )

            conn.commit()
            conn.close()

            return True, (
                "Reset request submitted. "
                "Please wait for administrator approval."
            )

        except sqlite3.Error:
            return False, (
                "Unable to submit reset request."
            )

    # --------------------------------------------------------
    # ADMIN RESET REQUESTS
    # --------------------------------------------------------

    def get_pending_reset_requests(self):
        try:
            conn = self._connect()
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT
                    id,
                    username,
                    email,
                    requested_at,
                    status
                FROM password_reset_requests
                WHERE status = 'PENDING'
                ORDER BY requested_at DESC
                """
            )

            rows = cursor.fetchall()
            conn.close()

            return rows

        except sqlite3.Error:
            return []

    def approve_reset_request(
        self,
        request_id,
        admin_username,
        decision
    ):
        try:
            conn = self._connect()
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT username
                FROM password_reset_requests
                WHERE id = ?
                """,
                (request_id,)
            )

            row = cursor.fetchone()

            if not row:
                conn.close()

                return False, (
                    "Reset request not found."
                )

            username = row[0]

            if decision.lower() == "approve":

                cursor.execute(
                    """
                    UPDATE password_reset_requests
                    SET status = 'APPROVED',
                        approved_by = ?,
                        admin_comment = ?
                    WHERE id = ?
                    """,
                    (
                        admin_username,
                        f"Approved by {admin_username}",
                        request_id
                    )
                )

                cursor.execute(
                    """
                    UPDATE users
                    SET failed_attempts = 0,
                        lockout_until = 0
                    WHERE username = ?
                    """,
                    (username,)
                )

                conn.commit()
                conn.close()

                logger.info(
                    "Reset approved for %s by %s",
                    username,
                    admin_username
                )

                return True, (
                    f"Request approved for {username}. "
                    "Their account has been unlocked."
                )

            cursor.execute(
                """
                UPDATE password_reset_requests
                SET status = 'REJECTED',
                    approved_by = ?,
                    admin_comment = ?
                WHERE id = ?
                """,
                (
                    admin_username,
                    f"Rejected by {admin_username}",
                    request_id
                )
            )

            conn.commit()
            conn.close()

            return True, (
                f"Request rejected for {username}."
            )

        except sqlite3.Error as exc:
            logger.error(
                "Reset approval error: %s",
                exc
            )

            return False, (
                "Unable to process reset request."
            )

    # --------------------------------------------------------
    # USERS
    # --------------------------------------------------------

    def get_all_users(self):
        try:
            conn = self._connect()
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT
                    username,
                    email,
                    role,
                    failed_attempts,
                    lockout_until
                FROM users
                ORDER BY username
                """
            )

            rows = cursor.fetchall()
            conn.close()

            return rows

        except sqlite3.Error:
            return []

    # --------------------------------------------------------
    # UNLOCK USER
    # --------------------------------------------------------

    def unlock_user_account(
        self,
        username
    ):
        try:
            conn = self._connect()

            conn.execute(
                """
                UPDATE users
                SET failed_attempts = 0,
                    lockout_until = 0
                WHERE username = ?
                """,
                (username,)
            )

            conn.commit()
            conn.close()

            return True, (
                f"Account unlocked for {username}."
            )

        except sqlite3.Error:
            return False, (
                "Unable to unlock account."
            )


# ============================================================
# TRACKER CONTROLLER
# ============================================================

class TrackerController:

    def __init__(self, db_name=DB_NAME):
        self.db_name = str(
            get_db_path(db_name)
        )

    def _connect(self):
        conn = sqlite3.connect(
            self.db_name,
            timeout=10
        )

        conn.execute(
            "PRAGMA busy_timeout = 5000"
        )

        return conn

    # --------------------------------------------------------
    # STOCK STATUS
    # --------------------------------------------------------

    @staticmethod
    def stock_status(quantity):
        if quantity > 5:
            return "In Stock"

        if quantity >= 1:
            return "Low Stock"

        return "Out of Stock"

    # --------------------------------------------------------
    # GET INVENTORY
    # --------------------------------------------------------

    def fetch_all_items(self):
        try:
            conn = self._connect()
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT
                    id,
                    name,
                    category,
                    quantity,
                    unit_price,
                    status,
                    condition,
                    last_returned,
                    remarks
                FROM hardware
                ORDER BY id
                """
            )

            rows = cursor.fetchall()
            conn.close()

            return rows

        except sqlite3.Error as exc:
            logger.error(
                "Fetch inventory error: %s",
                exc
            )

            return []

    # --------------------------------------------------------
    # ADD ITEM
    # --------------------------------------------------------

    def add_item(
        self,
        item_name,
        category,
        quantity,
        unit_price,
        condition,
        remarks
    ):
        try:
            validated = HardwareSchema(
                item_name=item_name,
                category=category,
                quantity=quantity,
                unit_price=unit_price,
                condition=condition,
                remarks=remarks
            )
        except ValidationError as exc:
            return False, (
                "Please enter valid hardware information."
            )

        status = self.stock_status(
            validated.quantity
        )

        try:
            conn = self._connect()

            conn.execute(
                """
                INSERT INTO hardware
                (
                    name,
                    category,
                    quantity,
                    unit_price,
                    status,
                    condition,
                    last_returned,
                    remarks
                )
                VALUES (?, ?, ?, ?, ?, ?, 0, ?)
                """,
                (
                    validated.item_name,
                    validated.category,
                    validated.quantity,
                    validated.unit_price,
                    status,
                    validated.condition,
                    validated.remarks
                )
            )

            conn.commit()
            conn.close()

            logger.info(
                "Added hardware item: %s",
                validated.item_name
            )

            return True, (
                f"'{validated.item_name}' added successfully."
            )

        except sqlite3.IntegrityError:
            return False, (
                "An item with that name already exists."
            )

        except sqlite3.Error:
            return False, (
                "Unable to save inventory item."
            )

    # --------------------------------------------------------
    # UPDATE ITEM
    # --------------------------------------------------------

    def update_item(
        self,
        item_id,
        quantity,
        unit_price,
        condition,
        remarks
    ):
        try:
            quantity = int(quantity)
            unit_price = float(unit_price)

            if quantity < 0:
                raise ValueError

            if unit_price < 0:
                raise ValueError

            if condition not in CONDITIONS:
                raise ValueError

        except (ValueError, TypeError):
            return False, (
                "Please enter valid quantity, price, "
                "and condition."
            )

        status = self.stock_status(quantity)

        try:
            conn = self._connect()

            conn.execute(
                """
                UPDATE hardware
                SET quantity = ?,
                    unit_price = ?,
                    status = ?,
                    condition = ?,
                    remarks = ?
                WHERE id = ?
                """,
                (
                    quantity,
                    unit_price,
                    status,
                    condition,
                    remarks,
                    item_id
                )
            )

            conn.commit()
            conn.close()

            return True, (
                f"Item ID {item_id} updated successfully."
            )

        except sqlite3.Error:
            return False, (
                "Unable to update item."
            )

    # --------------------------------------------------------
    # DELETE
    # --------------------------------------------------------

    def delete_item(self, item_id):
        try:
            conn = self._connect()

            cursor = conn.cursor()

            cursor.execute(
                "DELETE FROM hardware WHERE id = ?",
                (item_id,)
            )

            if cursor.rowcount == 0:
                conn.close()

                return False, (
                    "Item was not found."
                )

            conn.commit()
            conn.close()

            return True, (
                "Inventory item deleted successfully."
            )

        except sqlite3.Error:
            return False, (
                "Unable to delete item."
            )

    # --------------------------------------------------------
    # INVENTORY VALUE
    # --------------------------------------------------------

    def get_total_inventory_value(self):
        try:
            conn = self._connect()

            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT COALESCE(
                    SUM(quantity * unit_price),
                    0
                )
                FROM hardware
                """
            )

            value = cursor.fetchone()[0]

            conn.close()

            return float(value or 0)

        except sqlite3.Error:
            return 0.0

    # --------------------------------------------------------
    # DASHBOARD STATISTICS
    # --------------------------------------------------------

    def get_dashboard_stats(self):
        try:
            conn = self._connect()
            cursor = conn.cursor()

            cursor.execute(
                "SELECT COUNT(*) FROM hardware"
            )
            total_items = cursor.fetchone()[0]

            cursor.execute(
                """
                SELECT COALESCE(SUM(quantity), 0)
                FROM hardware
                """
            )
            total_quantity = cursor.fetchone()[0]

            cursor.execute(
                """
                SELECT COUNT(*)
                FROM hardware
                WHERE status = 'Low Stock'
                """
            )
            low_stock = cursor.fetchone()[0]

            cursor.execute(
                """
                SELECT COUNT(*)
                FROM hardware
                WHERE condition IN ('Damaged', 'Under Repair')
                """
            )
            repair_count = cursor.fetchone()[0]

            cursor.execute(
                """
                SELECT COUNT(*)
                FROM hardware
                WHERE condition = 'Good'
                """
            )
            good_count = cursor.fetchone()[0]

            conn.close()

            return {
                "items": total_items,
                "quantity": total_quantity,
                "low_stock": low_stock,
                "repair": repair_count,
                "good": good_count,
            }

        except sqlite3.Error:
            return {
                "items": 0,
                "quantity": 0,
                "low_stock": 0,
                "repair": 0,
                "good": 0,
            }

    # ========================================================
    # RETURN / INSPECTION FEATURE
    # ========================================================

    def record_return(
        self,
        hardware_id,
        returned_quantity,
        return_condition,
        remarks,
        returned_by
    ):
        try:
            validated = ReturnSchema(
                hardware_id=int(hardware_id),
                returned_quantity=int(returned_quantity),
                return_condition=return_condition,
                remarks=remarks.strip()
            )
        except (ValidationError, ValueError):
            return False, (
                "Please enter a valid return quantity "
                "and condition."
            )

        try:
            conn = self._connect()
            cursor = conn.cursor()

            # Get hardware
            cursor.execute(
                """
                SELECT
                    name,
                    quantity,
                    unit_price
                FROM hardware
                WHERE id = ?
                """,
                (validated.hardware_id,)
            )

            item = cursor.fetchone()

            if not item:
                conn.close()

                return False, (
                    "Selected hardware item was not found."
                )

            item_name, current_quantity, unit_price = item

            new_quantity = (
                current_quantity +
                validated.returned_quantity
            )

            new_status = self.stock_status(
                new_quantity
            )

            # Update inventory
            cursor.execute(
                """
                UPDATE hardware
                SET quantity = ?,
                    status = ?,
                    condition = ?,
                    last_returned = ?,
                    remarks = ?
                WHERE id = ?
                """,
                (
                    new_quantity,
                    new_status,
                    validated.return_condition,
                    time.time(),
                    validated.remarks,
                    validated.hardware_id
                )
            )

            # Create return history
            cursor.execute(
                """
                INSERT INTO equipment_returns
                (
                    hardware_id,
                    item_name,
                    returned_by,
                    returned_quantity,
                    return_condition,
                    remarks,
                    returned_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    validated.hardware_id,
                    item_name,
                    returned_by,
                    validated.returned_quantity,
                    validated.return_condition,
                    validated.remarks,
                    time.time()
                )
            )

            conn.commit()
            conn.close()

            logger.info(
                "Return recorded: %s | Qty: %s | Condition: %s | By: %s",
                item_name,
                validated.returned_quantity,
                validated.return_condition,
                returned_by
            )

            return True, (
                f"Return recorded for '{item_name}'. "
                f"Inventory increased by "
                f"{validated.returned_quantity}."
            )

        except sqlite3.Error as exc:
            logger.error(
                "Return recording error: %s",
                exc
            )

            return False, (
                "Unable to record equipment return."
            )

    # --------------------------------------------------------
    # RETURN HISTORY
    # --------------------------------------------------------

    def fetch_return_history(self):
        try:
            conn = self._connect()
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT
                    id,
                    item_name,
                    returned_by,
                    returned_quantity,
                    return_condition,
                    remarks,
                    returned_at
                FROM equipment_returns
                ORDER BY returned_at DESC
                """
            )

            rows = cursor.fetchall()
            conn.close()

            return rows

        except sqlite3.Error:
            return []

    # --------------------------------------------------------
    # EXPORT INVENTORY
    # --------------------------------------------------------

    def export_inventory_csv(self, path):
        rows = self.fetch_all_items()

        try:
            with open(
                path,
                "w",
                newline="",
                encoding="utf-8"
            ) as csvfile:

                writer = csv.writer(csvfile)

                writer.writerow(
                    [
                        "ID",
                        "Name",
                        "Category",
                        "Quantity",
                        "Unit Price",
                        "Status",
                        "Condition",
                        "Remarks"
                    ]
                )

                for row in rows:
                    writer.writerow(
                        [
                            row[0],
                            row[1],
                            row[2],
                            row[3],
                            row[4],
                            row[5],
                            row[6],
                            row[8],
                        ]
                    )

            return True, (
                "Inventory report exported successfully."
            )

        except OSError:
            return False, (
                "Unable to export inventory report."
            )

    # --------------------------------------------------------
    # EXPORT RETURNS
    # --------------------------------------------------------

    def export_returns_csv(self, path):
        rows = self.fetch_return_history()

        try:
            with open(
                path,
                "w",
                newline="",
                encoding="utf-8"
            ) as csvfile:

                writer = csv.writer(csvfile)

                writer.writerow(
                    [
                        "Return ID",
                        "Item",
                        "Returned By",
                        "Quantity",
                        "Condition",
                        "Remarks",
                        "Date"
                    ]
                )

                for row in rows:
                    date_text = time.strftime(
                        "%Y-%m-%d %H:%M:%S",
                        time.localtime(row[6])
                    )

                    writer.writerow(
                        [
                            row[0],
                            row[1],
                            row[2],
                            row[3],
                            row[4],
                            row[5],
                            date_text
                        ]
                    )

            return True, (
                "Return report exported successfully."
            )

        except OSError:
            return False, (
                "Unable to export return report."
            )


# ============================================================
# LOGIN WINDOW
# ============================================================

class LoginWindow:

    def __init__(
        self,
        root,
        on_login_success
    ):
        self.root = root
        self.on_login_success = on_login_success
        self.auth = AuthController()

        self.root.title(
            f"{APP_NAME} - Login"
        )

        self.root.geometry(
            "520x600"
        )

        self.root.minsize(
            480,
            560
        )

        self.build_style()
        self.build_gui()

    def build_style(self):
        style = ttk.Style()

        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure(
            "TNotebook",
            tabposition="n"
        )

        style.configure(
            "TNotebook.Tab",
            padding=[20, 10]
        )

    def build_gui(self):

        header = tk.Frame(
            self.root,
            bg="#17365D",
            height=100
        )

        header.pack(
            fill="x"
        )

        tk.Label(
            header,
            text="CAMPUS HARDWARE",
            bg="#17365D",
            fg="white",
            font=("Segoe UI", 20, "bold")
        ).pack(
            pady=(18, 2)
        )

        tk.Label(
            header,
            text="Asset Management System",
            bg="#17365D",
            fg="#DCE6F1",
            font=("Segoe UI", 10)
        ).pack()

        self.notebook = ttk.Notebook(
            self.root
        )

        self.notebook.pack(
            fill="both",
            expand=True,
            padx=25,
            pady=25
        )

        self.login_tab = ttk.Frame(
            self.notebook
        )

        self.register_tab = ttk.Frame(
            self.notebook
        )

        self.notebook.add(
            self.login_tab,
            text="  Login  "
        )

        self.notebook.add(
            self.register_tab,
            text="  Register  "
        )

        self.build_login()
        self.build_register()

    # --------------------------------------------------------
    # LOGIN
    # --------------------------------------------------------

    def build_login(self):

        frame = ttk.Frame(
            self.login_tab,
            padding=30
        )

        frame.pack(
            fill="both",
            expand=True
        )

        ttk.Label(
            frame,
            text="Welcome Back",
            font=("Segoe UI", 18, "bold")
        ).pack(
            pady=(20, 5)
        )

        ttk.Label(
            frame,
            text="Sign in to access the inventory system."
        ).pack(
            pady=(0, 25)
        )

        ttk.Label(
            frame,
            text="Username"
        ).pack(
            anchor="w"
        )

        self.entry_user = ttk.Entry(
            frame,
            width=40
        )

        self.entry_user.pack(
            fill="x",
            pady=(5, 15)
        )

        ttk.Label(
            frame,
            text="Password"
        ).pack(
            anchor="w"
        )

        self.entry_pass = ttk.Entry(
            frame,
            show="*",
            width=40
        )

        self.entry_pass.pack(
            fill="x",
            pady=(5, 5)
        )

        self.show_login = tk.BooleanVar(
            value=False
        )

        ttk.Checkbutton(
            frame,
            text="Show password",
            variable=self.show_login,
            command=self.toggle_login_password
        ).pack(
            anchor="w",
            pady=(0, 20)
        )

        self.login_button = tk.Button(
            frame,
            text="LOGIN",
            command=self.handle_login,
            bg="#2E7D32",
            fg="white",
            activebackground="#1B5E20",
            activeforeground="white",
            font=("Segoe UI", 10, "bold"),
            relief="flat",
            padx=20,
            pady=10,
            cursor="hand2"
        )

        self.login_button.pack(
            fill="x",
            pady=5
        )

        tk.Button(
            frame,
            text="Reset / Unlock Password",
            command=self.handle_reset,
            bg="#F4A261",
            fg="white",
            activebackground="#E76F51",
            activeforeground="white",
            font=("Segoe UI", 10, "bold"),
            relief="flat",
            pady=8,
            cursor="hand2"
        ).pack(
            fill="x",
            pady=5
        )

    def toggle_login_password(self):
        self.entry_pass.config(
            show="" if self.show_login.get() else "*"
        )

    def handle_login(self):

        username = self.entry_user.get().strip()
        password = self.entry_pass.get()

        success, result = self.auth.login_user(
            username,
            password
        )

        if success:
            messagebox.showinfo(
                "Login Successful",
                f"Welcome, {result['username']}!\n\n"
                f"Role: {result['role']}"
            )

            self.on_login_success(result)

        else:
            messagebox.showerror(
                "Login Failed",
                result
            )

    # --------------------------------------------------------
    # REGISTER
    # --------------------------------------------------------

    def build_register(self):

        frame = ttk.Frame(
            self.register_tab,
            padding=30
        )

        frame.pack(
            fill="both",
            expand=True
        )

        ttk.Label(
            frame,
            text="Create Account",
            font=("Segoe UI", 18, "bold")
        ).pack(
            pady=(15, 5)
        )

        ttk.Label(
            frame,
            text="New accounts are registered as USER accounts."
        ).pack(
            pady=(0, 20)
        )

        ttk.Label(
            frame,
            text="Username"
        ).pack(
            anchor="w"
        )

        self.entry_reg_user = ttk.Entry(
            frame
        )

        self.entry_reg_user.pack(
            fill="x",
            pady=(5, 12)
        )

        ttk.Label(
            frame,
            text="Email"
        ).pack(
            anchor="w"
        )

        self.entry_email = ttk.Entry(
            frame
        )

        self.entry_email.pack(
            fill="x",
            pady=(5, 12)
        )

        ttk.Label(
            frame,
            text="Password"
        ).pack(
            anchor="w"
        )

        self.entry_reg_pass = ttk.Entry(
            frame,
            show="*"
        )

        self.entry_reg_pass.pack(
            fill="x",
            pady=(5, 5)
        )

        self.show_register = tk.BooleanVar(
            value=False
        )

        ttk.Checkbutton(
            frame,
            text="Show password",
            variable=self.show_register,
            command=self.toggle_register_password
        ).pack(
            anchor="w",
            pady=(0, 15)
        )

        tk.Button(
            frame,
            text="CREATE ACCOUNT",
            command=self.handle_register,
            bg="#1976D2",
            fg="white",
            activebackground="#0D47A1",
            activeforeground="white",
            font=("Segoe UI", 10, "bold"),
            relief="flat",
            pady=10,
            cursor="hand2"
        ).pack(
            fill="x"
        )

    def toggle_register_password(self):
        self.entry_reg_pass.config(
            show="" if self.show_register.get() else "*"
        )

    def handle_register(self):

        username = self.entry_reg_user.get().strip()
        email = self.entry_email.get().strip()
        password = self.entry_reg_pass.get()

        success, message = self.auth.register_user(
            username,
            email,
            password
        )

        if success:
            messagebox.showinfo(
                "Account Created",
                message
            )

            self.entry_reg_user.delete(
                0,
                tk.END
            )

            self.entry_email.delete(
                0,
                tk.END
            )

            self.entry_reg_pass.delete(
                0,
                tk.END
            )

            self.notebook.select(0)

            self.entry_user.delete(
                0,
                tk.END
            )

            self.entry_user.insert(
                0,
                username
            )

        else:
            messagebox.showwarning(
                "Registration Problem",
                message
            )

    # --------------------------------------------------------
    # RESET
    # --------------------------------------------------------

    def handle_reset(self):

        username = self.entry_user.get().strip()

        if not username:
            messagebox.showwarning(
                "Username Required",
                "Enter your username first."
            )

            return

        email = simpledialog.askstring(
            "Password Reset",
            "Enter the email registered to this account:"
        )

        if not email:
            return

        success, message = (
            self.auth.request_password_reset(
                username,
                email
            )
        )

        if success:
            messagebox.showinfo(
                "Reset Request",
                message
            )

        else:
            messagebox.showerror(
                "Reset Request",
                message
            )


# ============================================================
# MAIN APPLICATION
# ============================================================

class TrackerWindow:

    def __init__(
        self,
        root,
        current_user,
        on_logout
    ):
        self.root = root
        self.current_user = current_user
        self.on_logout = on_logout

        self.controller = TrackerController()
        self.auth = AuthController()

        self.root.title(
            APP_NAME
        )

        # IMPORTANT:
        # The application can now be maximized.
        self.root.geometry(
            "1250x800"
        )

        self.root.minsize(
            950,
            650
        )

        self.root.resizable(
            True,
            True
        )

        self.setup_style()
        self.build_header()
        self.build_tabs()

        self.refresh_all()

    # ========================================================
    # STYLE
    # ========================================================

    def setup_style(self):

        style = ttk.Style()

        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure(
            "TNotebook.Tab",
            font=("Segoe UI", 10, "bold"),
            padding=[18, 10]
        )

        style.configure(
            "Treeview",
            rowheight=30,
            font=("Segoe UI", 9)
        )

        style.configure(
            "Treeview.Heading",
            font=("Segoe UI", 9, "bold")
        )

        style.configure(
            "TButton",
            padding=7
        )

        style.configure(
            "TLabel",
            font=("Segoe UI", 9)
        )

    # ========================================================
    # HEADER
    # ========================================================

    def build_header(self):

        header = tk.Frame(
            self.root,
            bg="#17365D"
        )

        header.pack(
            fill="x"
        )

        title_frame = tk.Frame(
            header,
            bg="#17365D"
        )

        title_frame.pack(
            side="left",
            padx=20,
            pady=12
        )

        tk.Label(
            title_frame,
            text="CAMPUS HARDWARE",
            bg="#17365D",
            fg="white",
            font=("Segoe UI", 17, "bold")
        ).pack(
            anchor="w"
        )

        tk.Label(
            title_frame,
            text="Asset Management System",
            bg="#17365D",
            fg="#DCE6F1",
            font=("Segoe UI", 9)
        ).pack(
            anchor="w"
        )

        user_text = (
            f"{self.current_user['username']}  |  "
            f"{self.current_user['role']}"
        )

        tk.Label(
            header,
            text=user_text,
            bg="#17365D",
            fg="white",
            font=("Segoe UI", 10, "bold")
        ).pack(
            side="right",
            padx=20
        )

    # ========================================================
    # TABS
    # ========================================================

    def build_tabs(self):

        self.notebook = ttk.Notebook(
            self.root
        )

        self.notebook.pack(
            fill="both",
            expand=True,
            padx=12,
            pady=12
        )

        self.dashboard_tab = ttk.Frame(
            self.notebook
        )

        self.inventory_tab = ttk.Frame(
            self.notebook
        )

        self.returns_tab = ttk.Frame(
            self.notebook
        )

        self.reports_tab = ttk.Frame(
            self.notebook
        )

        self.account_tab = ttk.Frame(
            self.notebook
        )

        self.notebook.add(
            self.dashboard_tab,
            text="  Dashboard  "
        )

        self.notebook.add(
            self.inventory_tab,
            text="  Inventory  "
        )

        self.notebook.add(
            self.returns_tab,
            text="  Returns / Inspection  "
        )

        self.notebook.add(
            self.reports_tab,
            text="  Reports  "
        )

        self.notebook.add(
            self.account_tab,
            text="  Account  "
        )

        self.build_dashboard()
        self.build_inventory()
        self.build_returns()
        self.build_reports()
        self.build_account()

        if self.current_user["role"].upper() == "ADMIN":
            self.admin_tab = ttk.Frame(
                self.notebook
            )

            self.notebook.add(
                self.admin_tab,
                text="  Admin  "
            )

            self.build_admin()

    # ========================================================
    # DASHBOARD
    # ========================================================

    def build_dashboard(self):

        outer = ttk.Frame(
            self.dashboard_tab,
            padding=20
        )

        outer.pack(
            fill="both",
            expand=True
        )

        ttk.Label(
            outer,
            text="Dashboard",
            font=("Segoe UI", 22, "bold")
        ).pack(
            anchor="w"
        )

        ttk.Label(
            outer,
            text="Quick overview of campus hardware resources."
        ).pack(
            anchor="w",
            pady=(0, 20)
        )

        self.dashboard_cards = {}

        cards = [
            ("items", "ITEM TYPES", "#1976D2"),
            ("quantity", "TOTAL QUANTITY", "#2E7D32"),
            ("low_stock", "LOW STOCK", "#F57C00"),
            ("repair", "NEEDS ATTENTION", "#C62828"),
        ]

        card_frame = tk.Frame(
            outer,
            bg="#F5F7FA"
        )

        card_frame.pack(
            fill="x"
        )

        for key, title, color in cards:

            card = tk.Frame(
                card_frame,
                bg=color,
                height=130
            )

            card.pack(
                side="left",
                fill="both",
                expand=True,
                padx=6,
                pady=5
            )

            value_label = tk.Label(
                card,
                text="0",
                bg=color,
                fg="white",
                font=("Segoe UI", 26, "bold")
            )

            value_label.pack(
                pady=(20, 0)
            )

            tk.Label(
                card,
                text=title,
                bg=color,
                fg="white",
                font=("Segoe UI", 9, "bold")
            ).pack()

            self.dashboard_cards[key] = value_label

        value_frame = ttk.LabelFrame(
            outer,
            text="Inventory Value",
            padding=20
        )

        value_frame.pack(
            fill="x",
            pady=20
        )

        self.dashboard_value = tk.Label(
            value_frame,
            text="$0.00",
            fg="#17365D",
            font=("Segoe UI", 28, "bold")
        )

        self.dashboard_value.pack(
            anchor="w"
        )

        self.dashboard_message = ttk.Label(
            outer,
            text="",
            font=("Segoe UI", 11)
        )

        self.dashboard_message.pack(
            anchor="w"
        )

    # ========================================================
    # INVENTORY
    # ========================================================

    def build_inventory(self):

        main = ttk.Frame(
            self.inventory_tab,
            padding=15
        )

        main.pack(
            fill="both",
            expand=True
        )

        title_frame = ttk.Frame(main)

        title_frame.pack(
            fill="x"
        )

        ttk.Label(
            title_frame,
            text="Hardware Inventory",
            font=("Segoe UI", 20, "bold")
        ).pack(
            side="left"
        )

        self.search_var = tk.StringVar()

        ttk.Label(
            title_frame,
            text="Search:"
        ).pack(
            side="right",
            padx=(10, 5)
        )

        search_entry = ttk.Entry(
            title_frame,
            textvariable=self.search_var,
            width=30
        )

        search_entry.pack(
            side="right"
        )

        self.search_var.trace_add(
            "write",
            lambda *_: self.refresh_inventory()
        )

        # ----------------------------------------------------
        # ADD / EDIT FORM
        # ----------------------------------------------------

        form = ttk.LabelFrame(
            main,
            text="Add / Edit Hardware Item",
            padding=15
        )

        form.pack(
            fill="x",
            pady=12
        )

        # Row 1
        ttk.Label(
            form,
            text="Item Name:"
        ).grid(
            row=0,
            column=0,
            sticky="w",
            padx=5,
            pady=5
        )

        self.item_name_entry = ttk.Entry(
            form,
            width=25
        )

        self.item_name_entry.grid(
            row=0,
            column=1,
            sticky="ew",
            padx=5,
            pady=5
        )

        ttk.Label(
            form,
            text="Category:"
        ).grid(
            row=0,
            column=2,
            sticky="w",
            padx=5,
            pady=5
        )

        self.category_entry = ttk.Entry(
            form,
            width=22
        )

        self.category_entry.grid(
            row=0,
            column=3,
            sticky="ew",
            padx=5,
            pady=5
        )

        ttk.Label(
            form,
            text="Quantity:"
        ).grid(
            row=0,
            column=4,
            sticky="w",
            padx=5,
            pady=5
        )

        self.quantity_entry = ttk.Entry(
            form,
            width=12
        )

        self.quantity_entry.grid(
            row=0,
            column=5,
            sticky="ew",
            padx=5,
            pady=5
        )

        # Row 2
        ttk.Label(
            form,
            text="Unit Price:"
        ).grid(
            row=1,
            column=0,
            sticky="w",
            padx=5,
            pady=5
        )

        self.price_entry = ttk.Entry(
            form,
            width=25
        )

        self.price_entry.grid(
            row=1,
            column=1,
            sticky="ew",
            padx=5,
            pady=5
        )

        ttk.Label(
            form,
            text="Condition:"
        ).grid(
            row=1,
            column=2,
            sticky="w",
            padx=5,
            pady=5
        )

        self.condition_var = tk.StringVar(
            value="Good"
        )

        self.condition_combo = ttk.Combobox(
            form,
            textvariable=self.condition_var,
            values=CONDITIONS,
            state="readonly",
            width=20
        )

        self.condition_combo.grid(
            row=1,
            column=3,
            sticky="ew",
            padx=5,
            pady=5
        )

        ttk.Label(
            form,
            text="Remarks:"
        ).grid(
            row=1,
            column=4,
            sticky="w",
            padx=5,
            pady=5
        )

        self.remarks_entry = ttk.Entry(
            form
        )

        self.remarks_entry.grid(
            row=1,
            column=5,
            sticky="ew",
            padx=5,
            pady=5
        )

        for column in range(6):
            form.columnconfigure(
                column,
                weight=1
            )

        # Buttons
        button_frame = ttk.Frame(form)

        button_frame.grid(
            row=2,
            column=0,
            columnspan=6,
            sticky="ew",
            pady=(10, 0)
        )

        self.save_item_button = tk.Button(
            button_frame,
            text="SAVE NEW ITEM",
            command=self.add_item,
            bg="#2E7D32",
            fg="white",
            relief="flat",
            padx=15,
            pady=7
        )

        self.save_item_button.pack(
            side="left",
            padx=5
        )

        self.update_item_button = tk.Button(
            button_frame,
            text="UPDATE SELECTED",
            command=self.update_item,
            bg="#1976D2",
            fg="white",
            relief="flat",
            padx=15,
            pady=7
        )

        self.update_item_button.pack(
            side="left",
            padx=5
        )

        tk.Button(
            button_frame,
            text="CLEAR FORM",
            command=self.clear_inventory_form,
            bg="#757575",
            fg="white",
            relief="flat",
            padx=15,
            pady=7
        ).pack(
            side="left",
            padx=5
        )

        self.delete_item_button = tk.Button(
            button_frame,
            text="DELETE SELECTED",
            command=self.delete_item,
            bg="#C62828",
            fg="white",
            relief="flat",
            padx=15,
            pady=7
        )

        self.delete_item_button.pack(
            side="right",
            padx=5
        )

        # ----------------------------------------------------
        # INVENTORY TABLE
        # ----------------------------------------------------

        table_frame = ttk.Frame(main)

        table_frame.pack(
            fill="both",
            expand=True
        )

        columns = (
            "id",
            "name",
            "category",
            "quantity",
            "price",
            "status",
            "condition",
            "remarks"
        )

        self.inventory_tree = ttk.Treeview(
            table_frame,
            columns=columns,
            show="headings"
        )

        headings = {
            "id": "ID",
            "name": "Item Name",
            "category": "Category",
            "quantity": "Qty",
            "price": "Unit Price",
            "status": "Stock Status",
            "condition": "Condition",
            "remarks": "Remarks",
        }

        widths = {
            "id": 60,
            "name": 200,
            "category": 150,
            "quantity": 70,
            "price": 100,
            "status": 120,
            "condition": 120,
            "remarks": 220,
        }

        for column in columns:
            self.inventory_tree.heading(
                column,
                text=headings[column]
            )

            self.inventory_tree.column(
                column,
                width=widths[column],
                anchor="center"
                if column in {
                    "id",
                    "quantity",
                    "price",
                    "status",
                    "condition"
                }
                else "w"
            )

        self.inventory_tree.tag_configure(
            "Out of Stock",
            background="#FDE2E2"
        )

        self.inventory_tree.tag_configure(
            "Low Stock",
            background="#FFF3CD"
        )

        self.inventory_tree.tag_configure(
            "In Stock",
            background="#E2F0D9"
        )

        self.inventory_tree.pack(
            side="left",
            fill="both",
            expand=True
        )

        scrollbar = ttk.Scrollbar(
            table_frame,
            orient="vertical",
            command=self.inventory_tree.yview
        )

        scrollbar.pack(
            side="right",
            fill="y"
        )

        self.inventory_tree.configure(
            yscrollcommand=scrollbar.set
        )

        self.inventory_tree.bind(
            "<<TreeviewSelect>>",
            self.load_selected_item
        )

        self.apply_inventory_permissions()

    # ========================================================
    # INVENTORY PERMISSIONS
    # ========================================================

    def apply_inventory_permissions(self):

        is_admin = (
            self.current_user["role"].upper()
            == "ADMIN"
        )

        state = "normal" if is_admin else "disabled"

        self.item_name_entry.config(
            state=state
        )

        self.category_entry.config(
            state=state
        )

        self.quantity_entry.config(
            state=state
        )

        self.price_entry.config(
            state=state
        )

        self.condition_combo.config(
            state="readonly" if is_admin else "disabled"
        )

        self.remarks_entry.config(
            state=state
        )

        self.save_item_button.config(
            state=state
        )

        self.update_item_button.config(
            state=state
        )

        self.delete_item_button.config(
            state=state
        )

    # ========================================================
    # INVENTORY FUNCTIONS
    # ========================================================

    def clear_inventory_form(self):

        for entry in [
            self.item_name_entry,
            self.category_entry,
            self.quantity_entry,
            self.price_entry,
            self.remarks_entry
        ]:
            entry.delete(
                0,
                tk.END
            )

        self.condition_var.set(
            "Good"
        )

        for item in self.inventory_tree.selection():
            self.inventory_tree.selection_remove(
                item
            )

    def load_selected_item(self, event=None):

        selected = self.inventory_tree.selection()

        if not selected:
            return

        values = self.inventory_tree.item(
            selected[0],
            "values"
        )

        if not values:
            return

        self.item_name_entry.config(
            state="normal"
        )

        self.category_entry.config(
            state="normal"
        )

        self.quantity_entry.config(
            state="normal"
        )

        self.price_entry.config(
            state="normal"
        )

        self.remarks_entry.config(
            state="normal"
        )

        self.item_name_entry.delete(
            0,
            tk.END
        )

        self.item_name_entry.insert(
            0,
            values[1]
        )

        self.category_entry.delete(
            0,
            tk.END
        )

        self.category_entry.insert(
            0,
            values[2]
        )

        self.quantity_entry.delete(
            0,
            tk.END
        )

        self.quantity_entry.insert(
            0,
            values[3]
        )

        self.price_entry.delete(
            0,
            tk.END
        )

        self.price_entry.insert(
            0,
            values[4]
        )

        self.condition_var.set(
            values[6]
        )

        self.remarks_entry.delete(
            0,
            tk.END
        )

        self.remarks_entry.insert(
            0,
            values[7]
        )

        self.apply_inventory_permissions()

    def add_item(self):

        try:
            quantity = int(
                self.quantity_entry.get()
            )

            price = float(
                self.price_entry.get()
            )

        except ValueError:
            messagebox.showerror(
                "Invalid Input",
                "Quantity must be a whole number and "
                "price must be numeric."
            )

            return

        success, message = self.controller.add_item(
            self.item_name_entry.get(),
            self.category_entry.get(),
            quantity,
            price,
            self.condition_var.get(),
            self.remarks_entry.get()
        )

        if success:
            self.clear_inventory_form()
            self.refresh_all()

            messagebox.showinfo(
                "Item Saved",
                message
            )

        else:
            messagebox.showerror(
                "Save Failed",
                message
            )

    def update_item(self):

        selected = self.inventory_tree.selection()

        if not selected:
            messagebox.showwarning(
                "No Selection",
                "Select an inventory item first."
            )

            return

        item_id = self.inventory_tree.item(
            selected[0],
            "values"
        )[0]

        try:
            quantity = int(
                self.quantity_entry.get()
            )

            price = float(
                self.price_entry.get()
            )

        except ValueError:
            messagebox.showerror(
                "Invalid Input",
                "Quantity must be a whole number and "
                "price must be numeric."
            )

            return

        success, message = self.controller.update_item(
            item_id,
            quantity,
            price,
            self.condition_var.get(),
            self.remarks_entry.get()
        )

        if success:
            self.refresh_all()

            messagebox.showinfo(
                "Updated",
                message
            )

        else:
            messagebox.showerror(
                "Update Failed",
                message
            )

    def delete_item(self):

        selected = self.inventory_tree.selection()

        if not selected:
            messagebox.showwarning(
                "No Selection",
                "Select an item to delete."
            )

            return

        values = self.inventory_tree.item(
            selected[0],
            "values"
        )

        item_id = values[0]
        item_name = values[1]

        confirm = messagebox.askyesno(
            "Confirm Delete",
            f"Delete '{item_name}'?"
        )

        if not confirm:
            return

        success, message = (
            self.controller.delete_item(
                item_id
            )
        )

        if success:
            self.clear_inventory_form()
            self.refresh_all()

            messagebox.showinfo(
                "Deleted",
                message
            )

        else:
            messagebox.showerror(
                "Delete Failed",
                message
            )

    def refresh_inventory(self):

        search = self.search_var.get().lower()

        for item in self.inventory_tree.get_children():
            self.inventory_tree.delete(
                item
            )

        rows = self.controller.fetch_all_items()

        for row in rows:

            item_id = row[0]
            name = row[1]
            category = row[2]
            quantity = row[3]
            price = row[4]
            status = row[5]
            condition = row[6]
            remarks = row[8]

            searchable = (
                f"{name} {category} "
                f"{status} {condition} "
                f"{remarks}"
            ).lower()

            if search and search not in searchable:
                continue

            self.inventory_tree.insert(
                "",
                "end",
                values=(
                    item_id,
                    name,
                    category,
                    quantity,
                    f"{price:,.2f}",
                    status,
                    condition,
                    remarks
                ),
                tags=(status,)
            )

    # ========================================================
    # RETURNS / INSPECTION
    # ========================================================

    def build_returns(self):

        main = ttk.Frame(
            self.returns_tab,
            padding=15
        )

        main.pack(
            fill="both",
            expand=True
        )

        ttk.Label(
            main,
            text="Equipment Return & Condition Inspection",
            font=("Segoe UI", 20, "bold")
        ).pack(
            anchor="w"
        )

        ttk.Label(
            main,
            text=(
                "Record equipment when it is submitted back "
                "and inspect its condition."
            )
        ).pack(
            anchor="w",
            pady=(0, 15)
        )

        # ----------------------------------------------------
        # RETURN FORM
        # ----------------------------------------------------

        form = ttk.LabelFrame(
            main,
            text="Submit Returned Equipment",
            padding=15
        )

        form.pack(
            fill="x",
            pady=(0, 15)
        )

        ttk.Label(
            form,
            text="Equipment:"
        ).grid(
            row=0,
            column=0,
            sticky="w",
            padx=5,
            pady=5
        )

        self.return_item_var = tk.StringVar()

        self.return_item_combo = ttk.Combobox(
            form,
            textvariable=self.return_item_var,
            state="readonly",
            width=40
        )

        self.return_item_combo.grid(
            row=0,
            column=1,
            sticky="ew",
            padx=5,
            pady=5
        )

        ttk.Label(
            form,
            text="Returned Qty:"
        ).grid(
            row=0,
            column=2,
            sticky="w",
            padx=5,
            pady=5
        )

        self.return_quantity_entry = ttk.Entry(
            form,
            width=12
        )

        self.return_quantity_entry.grid(
            row=0,
            column=3,
            padx=5,
            pady=5
        )

        ttk.Label(
            form,
            text="Condition:"
        ).grid(
            row=1,
            column=0,
            sticky="w",
            padx=5,
            pady=5
        )

        self.return_condition_var = tk.StringVar(
            value="Good"
        )

        ttk.Combobox(
            form,
            textvariable=self.return_condition_var,
            values=CONDITIONS,
            state="readonly",
            width=20
        ).grid(
            row=1,
            column=1,
            sticky="ew",
            padx=5,
            pady=5
        )

        ttk.Label(
            form,
            text="Remarks:"
        ).grid(
            row=1,
            column=2,
            sticky="w",
            padx=5,
            pady=5
        )

        self.return_remarks_entry = ttk.Entry(
            form
        )

        self.return_remarks_entry.grid(
            row=1,
            column=3,
            sticky="ew",
            padx=5,
            pady=5
        )

        form.columnconfigure(
            1,
            weight=1
        )

        form.columnconfigure(
            3,
            weight=1
        )

        tk.Button(
            form,
            text="RECORD RETURN",
            command=self.record_return,
            bg="#2E7D32",
            fg="white",
            relief="flat",
            padx=20,
            pady=8
        ).grid(
            row=2,
            column=0,
            columnspan=4,
            sticky="ew",
            padx=5,
            pady=(10, 0)
        )

        # ----------------------------------------------------
        # RETURN HISTORY
        # ----------------------------------------------------

        ttk.Label(
            main,
            text="Return History",
            font=("Segoe UI", 13, "bold")
        ).pack(
            anchor="w",
            pady=(5, 5)
        )

        history_frame = ttk.Frame(
            main
        )

        history_frame.pack(
            fill="both",
            expand=True
        )

        columns = (
            "id",
            "item",
            "returned_by",
            "quantity",
            "condition",
            "remarks",
            "date"
        )

        self.return_tree = ttk.Treeview(
            history_frame,
            columns=columns,
            show="headings"
        )

        headings = [
            ("id", "ID", 60),
            ("item", "Equipment", 200),
            ("returned_by", "Returned By", 130),
            ("quantity", "Qty", 70),
            ("condition", "Condition", 130),
            ("remarks", "Remarks", 280),
            ("date", "Return Date", 160),
        ]

        for key, title, width in headings:
            self.return_tree.heading(
                key,
                text=title
            )

            self.return_tree.column(
                key,
                width=width
            )

        self.return_tree.pack(
            side="left",
            fill="both",
            expand=True
        )

        scrollbar = ttk.Scrollbar(
            history_frame,
            orient="vertical",
            command=self.return_tree.yview
        )

        scrollbar.pack(
            side="right",
            fill="y"
        )

        self.return_tree.configure(
            yscrollcommand=scrollbar.set
        )

        self.refresh_return_items()
        self.refresh_return_history()

    def refresh_return_items(self):

        rows = self.controller.fetch_all_items()

        values = []

        for row in rows:
            values.append(
                f"{row[0]} - {row[1]}"
            )

        self.return_item_combo["values"] = values

        if values:
            self.return_item_combo.current(0)

    def record_return(self):

        selected = self.return_item_var.get()

        if not selected:
            messagebox.showwarning(
                "No Equipment",
                "Select the returned equipment."
            )

            return

        try:
            hardware_id = int(
                selected.split(" - ", 1)[0]
            )

            returned_quantity = int(
                self.return_quantity_entry.get()
            )

        except ValueError:
            messagebox.showerror(
                "Invalid Quantity",
                "Returned quantity must be a whole number."
            )

            return

        success, message = (
            self.controller.record_return(
                hardware_id,
                returned_quantity,
                self.return_condition_var.get(),
                self.return_remarks_entry.get(),
                self.current_user["username"]
            )
        )

        if success:

            self.return_quantity_entry.delete(
                0,
                tk.END
            )

            self.return_remarks_entry.delete(
                0,
                tk.END
            )

            self.refresh_all()

            messagebox.showinfo(
                "Return Recorded",
                message
            )

        else:
            messagebox.showerror(
                "Return Failed",
                message
            )

    def refresh_return_history(self):

        for item in self.return_tree.get_children():
            self.return_tree.delete(
                item
            )

        rows = self.controller.fetch_return_history()

        for row in rows:

            date_text = time.strftime(
                "%Y-%m-%d %H:%M",
                time.localtime(row[6])
            )

            self.return_tree.insert(
                "",
                "end",
                values=(
                    row[0],
                    row[1],
                    row[2],
                    row[3],
                    row[4],
                    row[5],
                    date_text
                )
            )

    # ========================================================
    # REPORTS
    # ========================================================

    def build_reports(self):

        main = ttk.Frame(
            self.reports_tab,
            padding=25
        )

        main.pack(
            fill="both",
            expand=True
        )

        ttk.Label(
            main,
            text="Reports & Export",
            font=("Segoe UI", 20, "bold")
        ).pack(
            anchor="w"
        )

        ttk.Label(
            main,
            text="Generate CSV reports for inventory and equipment returns."
        ).pack(
            anchor="w",
            pady=(0, 25)
        )

        report_frame = ttk.LabelFrame(
            main,
            text="Available Reports",
            padding=20
        )

        report_frame.pack(
            fill="x"
        )

        tk.Button(
            report_frame,
            text="EXPORT INVENTORY CSV",
            command=self.export_inventory,
            bg="#1976D2",
            fg="white",
            relief="flat",
            padx=25,
            pady=12
        ).pack(
            fill="x",
            pady=5
        )

        tk.Button(
            report_frame,
            text="EXPORT RETURN / INSPECTION CSV",
            command=self.export_returns,
            bg="#6A1B9A",
            fg="white",
            relief="flat",
            padx=25,
            pady=12
        ).pack(
            fill="x",
            pady=5
        )

        ttk.Label(
            main,
            text=(
                "CSV files can be opened using Microsoft Excel, "
                "LibreOffice Calc, or Google Sheets."
            ),
            foreground="#666666"
        ).pack(
            anchor="w",
            pady=20
        )

    def export_inventory(self):

        path = filedialog.asksaveasfilename(
            title="Save Inventory Report",
            defaultextension=".csv",
            filetypes=[
                ("CSV Files", "*.csv")
            ]
        )

        if not path:
            return

        success, message = (
            self.controller.export_inventory_csv(
                path
            )
        )

        if success:
            messagebox.showinfo(
                "Export Complete",
                message
            )

        else:
            messagebox.showerror(
                "Export Failed",
                message
            )

    def export_returns(self):

        path = filedialog.asksaveasfilename(
            title="Save Return Report",
            defaultextension=".csv",
            filetypes=[
                ("CSV Files", "*.csv")
            ]
        )

        if not path:
            return

        success, message = (
            self.controller.export_returns_csv(
                path
            )
        )

        if success:
            messagebox.showinfo(
                "Export Complete",
                message
            )

        else:
            messagebox.showerror(
                "Export Failed",
                message
            )

    # ========================================================
    # ACCOUNT
    # ========================================================

    def build_account(self):

        main = ttk.Frame(
            self.account_tab,
            padding=25
        )

        main.pack(
            fill="both",
            expand=True
        )

        ttk.Label(
            main,
            text="Account & Security",
            font=("Segoe UI", 20, "bold")
        ).pack(
            anchor="w"
        )

        self.profile_label = ttk.Label(
            main,
            text="Loading profile...",
            font=("Segoe UI", 11)
        )

        self.profile_label.pack(
            anchor="w",
            pady=(10, 25)
        )

        security = ttk.LabelFrame(
            main,
            text="Change Password",
            padding=20
        )

        security.pack(
            fill="x"
        )

        ttk.Label(
            security,
            text="New Password:"
        ).pack(
            anchor="w"
        )

        self.new_password_entry = ttk.Entry(
            security,
            show="*",
            width=35
        )

        self.new_password_entry.pack(
            anchor="w",
            pady=7
        )

        tk.Button(
            security,
            text="CHANGE PASSWORD",
            command=self.change_password,
            bg="#2E7D32",
            fg="white",
            relief="flat",
            padx=20,
            pady=8
        ).pack(
            anchor="w"
        )

        reset_frame = ttk.LabelFrame(
            main,
            text="Password Recovery",
            padding=20
        )

        reset_frame.pack(
            fill="x",
            pady=20
        )

        ttk.Label(
            reset_frame,
            text=(
                "If your account is locked, submit a reset request "
                "for administrator approval."
            )
        ).pack(
            anchor="w",
            pady=(0, 10)
        )

        tk.Button(
            reset_frame,
            text="REQUEST PASSWORD RESET / UNLOCK",
            command=self.request_reset,
            bg="#F57C00",
            fg="white",
            relief="flat",
            padx=20,
            pady=8
        ).pack(
            anchor="w"
        )

        tk.Button(
            main,
            text="LOGOUT",
            command=self.logout,
            bg="#555555",
            fg="white",
            relief="flat",
            padx=30,
            pady=10
        ).pack(
            anchor="w",
            pady=20
        )

    def load_profile(self):

        profile = self.auth.get_user_profile(
            self.current_user["username"]
        )

        if profile:
            self.profile_label.config(
                text=(
                    f"Username: {profile['username']}\n"
                    f"Email: {profile['email']}\n"
                    f"Role: {profile['role']}"
                )
            )

    def change_password(self):

        new_password = (
            self.new_password_entry.get()
        )

        if not new_password:
            messagebox.showwarning(
                "Password Required",
                "Enter a new password."
            )

            return

        success, message = (
            self.auth.change_password(
                self.current_user["username"],
                new_password
            )
        )

        if success:

            self.new_password_entry.delete(
                0,
                tk.END
            )

            messagebox.showinfo(
                "Password Updated",
                message
            )

        else:
            messagebox.showerror(
                "Password Update Failed",
                message
            )

    def request_reset(self):

        profile = self.auth.get_user_profile(
            self.current_user["username"]
        )

        if not profile:
            return

        success, message = (
            self.auth.request_password_reset(
                profile["username"],
                profile["email"]
            )
        )

        if success:
            messagebox.showinfo(
                "Reset Request",
                message
            )

        else:
            messagebox.showerror(
                "Reset Request",
                message
            )

    # ========================================================
    # ADMIN TAB
    # ========================================================

    def build_admin(self):

        main = ttk.Frame(
            self.admin_tab,
            padding=15
        )

        main.pack(
            fill="both",
            expand=True
        )

        ttk.Label(
            main,
            text="Administrator Panel",
            font=("Segoe UI", 20, "bold")
        ).pack(
            anchor="w"
        )

        # ----------------------------------------------------
        # RESET REQUESTS
        # ----------------------------------------------------

        reset_frame = ttk.LabelFrame(
            main,
            text="Pending Password Reset / Unlock Requests",
            padding=10
        )

        reset_frame.pack(
            fill="both",
            expand=True,
            pady=(15, 10)
        )

        self.admin_reset_tree = ttk.Treeview(
            reset_frame,
            columns=(
                "id",
                "username",
                "email",
                "date"
            ),
            show="headings",
            height=7
        )

        for column, title, width in [
            ("id", "ID", 60),
            ("username", "Username", 150),
            ("email", "Email", 250),
            ("date", "Requested", 180),
        ]:
            self.admin_reset_tree.heading(
                column,
                text=title
            )

            self.admin_reset_tree.column(
                column,
                width=width
            )

        self.admin_reset_tree.pack(
            fill="both",
            expand=True
        )

        reset_buttons = ttk.Frame(
            reset_frame
        )

        reset_buttons.pack(
            fill="x",
            pady=8
        )

        tk.Button(
            reset_buttons,
            text="APPROVE / UNLOCK",
            command=self.approve_request,
            bg="#2E7D32",
            fg="white",
            relief="flat",
            padx=15,
            pady=7
        ).pack(
            side="left",
            padx=5
        )

        tk.Button(
            reset_buttons,
            text="REJECT",
            command=self.reject_request,
            bg="#C62828",
            fg="white",
            relief="flat",
            padx=15,
            pady=7
        ).pack(
            side="left",
            padx=5
        )

        # ----------------------------------------------------
        # USER MANAGEMENT
        # ----------------------------------------------------

        users_frame = ttk.LabelFrame(
            main,
            text="Registered Users",
            padding=10
        )

        users_frame.pack(
            fill="both",
            expand=True
        )

        self.users_tree = ttk.Treeview(
            users_frame,
            columns=(
                "username",
                "email",
                "role",
                "failed",
                "locked"
            ),
            show="headings"
        )

        for column, title, width in [
            ("username", "Username", 150),
            ("email", "Email", 250),
            ("role", "Role", 100),
            ("failed", "Failed Attempts", 120),
            ("locked", "Account Status", 150),
        ]:
            self.users_tree.heading(
                column,
                text=title
            )

            self.users_tree.column(
                column,
                width=width
            )

        self.users_tree.pack(
            fill="both",
            expand=True
        )

        tk.Button(
            users_frame,
            text="UNLOCK SELECTED USER",
            command=self.unlock_selected_user,
            bg="#1976D2",
            fg="white",
            relief="flat",
            padx=15,
            pady=7
        ).pack(
            anchor="w",
            pady=8
        )

        self.refresh_admin()

    def refresh_admin(self):

        if not hasattr(
            self,
            "admin_reset_tree"
        ):
            return

        # Reset requests
        for item in self.admin_reset_tree.get_children():
            self.admin_reset_tree.delete(
                item
            )

        requests = (
            self.auth.get_pending_reset_requests()
        )

        for row in requests:

            date_text = time.strftime(
                "%Y-%m-%d %H:%M",
                time.localtime(row[3])
            )

            self.admin_reset_tree.insert(
                "",
                "end",
                values=(
                    row[0],
                    row[1],
                    row[2],
                    date_text
                )
            )

        # Users
        for item in self.users_tree.get_children():
            self.users_tree.delete(
                item
            )

        users = self.auth.get_all_users()

        for row in users:

            username = row[0]
            email = row[1]
            role = row[2]
            failed = row[3]
            lockout = row[4]

            status = (
                "LOCKED"
                if lockout and time.time() < lockout
                else "ACTIVE"
            )

            self.users_tree.insert(
                "",
                "end",
                values=(
                    username,
                    email,
                    role,
                    failed,
                    status
                )
            )

    def approve_request(self):

        selected = (
            self.admin_reset_tree.selection()
        )

        if not selected:
            messagebox.showwarning(
                "No Request",
                "Select a reset request."
            )

            return

        request_id = self.admin_reset_tree.item(
            selected[0],
            "values"
        )[0]

        success, message = (
            self.auth.approve_reset_request(
                request_id,
                self.current_user["username"],
                "approve"
            )
        )

        if success:
            self.refresh_admin()

            messagebox.showinfo(
                "Approved",
                message
            )

        else:
            messagebox.showerror(
                "Approval Failed",
                message
            )

    def reject_request(self):

        selected = (
            self.admin_reset_tree.selection()
        )

        if not selected:
            messagebox.showwarning(
                "No Request",
                "Select a reset request."
            )

            return

        request_id = self.admin_reset_tree.item(
            selected[0],
            "values"
        )[0]

        success, message = (
            self.auth.approve_reset_request(
                request_id,
                self.current_user["username"],
                "reject"
            )
        )

        if success:
            self.refresh_admin()

            messagebox.showinfo(
                "Rejected",
                message
            )

        else:
            messagebox.showerror(
                "Rejection Failed",
                message
            )

    def unlock_selected_user(self):

        selected = (
            self.users_tree.selection()
        )

        if not selected:
            messagebox.showwarning(
                "No User",
                "Select a user first."
            )

            return

        username = self.users_tree.item(
            selected[0],
            "values"
        )[0]

        if username == self.current_user["username"]:
            messagebox.showinfo(
                "Account",
                "Your account is already active."
            )

            return

        success, message = (
            self.auth.unlock_user_account(
                username
            )
        )

        if success:
            self.refresh_admin()

            messagebox.showinfo(
                "Account Unlocked",
                message
            )

    # ========================================================
    # REFRESH
    # ========================================================

    def refresh_dashboard(self):

        stats = (
            self.controller.get_dashboard_stats()
        )

        for key, label in self.dashboard_cards.items():
            label.config(
                text=str(stats.get(key, 0))
            )

        total_value = (
            self.controller.get_total_inventory_value()
        )

        self.dashboard_value.config(
            text=f"${total_value:,.2f}"
        )

        if stats["repair"] > 0:
            self.dashboard_message.config(
                text=(
                    f"Attention required: "
                    f"{stats['repair']} equipment item(s) "
                    f"are damaged or under repair."
                ),
                foreground="#C62828"
            )

        elif stats["low_stock"] > 0:
            self.dashboard_message.config(
                text=(
                    f"{stats['low_stock']} item(s) "
                    f"are currently low in stock."
                ),
                foreground="#F57C00"
            )

        else:
            self.dashboard_message.config(
                text="Inventory is currently in good condition.",
                foreground="#2E7D32"
            )

    def refresh_all(self):

        self.refresh_inventory()
        self.refresh_dashboard()
        self.refresh_return_items()
        self.refresh_return_history()
        self.load_profile()

        if self.current_user["role"].upper() == "ADMIN":
            self.refresh_admin()

    # ========================================================
    # LOGOUT
    # ========================================================

    def logout(self):

        confirm = messagebox.askyesno(
            "Logout",
            "Are you sure you want to logout?"
        )

        if confirm:
            self.on_logout()


# ============================================================
# APPLICATION LAUNCH
# ============================================================

def clear_window(root):

    for widget in root.winfo_children():
        widget.destroy()


def launch_main_app(
    root,
    user
):

    clear_window(root)

    TrackerWindow(
        root,
        current_user=user,
        on_logout=lambda: launch_login_screen(root)
    )


def launch_login_screen(root):

    clear_window(root)

    root.title(
        f"{APP_NAME} - Login"
    )

    root.geometry(
        "520x600"
    )

    root.minsize(
        480,
        560
    )

    LoginWindow(
        root,
        on_login_success=lambda user:
            launch_main_app(
                root,
                user
            )
    )


# ============================================================
# MAIN
# ============================================================

def main():

    init_db()

    root = tk.Tk()

    # --------------------------------------------------------
    # Windows maximize button
    # --------------------------------------------------------

    try:
        root.state("zoomed")
    except tk.TclError:
        pass

    launch_login_screen(root)

    root.mainloop()


if __name__ == "__main__":
    main()
