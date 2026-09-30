import csv
import logging
import os
import re
import sqlite3
import time
from pathlib import Path

import bcrypt
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


# Render/Flask web-safe backend. Tkinter desktop GUI is intentionally excluded.
