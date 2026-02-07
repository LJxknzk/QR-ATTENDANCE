import os
from flask import Flask, render_template, request, jsonify, redirect, url_for, session, send_file
from flask_sqlalchemy import SQLAlchemy

# Optional third-party extensions: import safely so the module can still
# run in bundled environments if a package isn't present. We guard
# usage below and provide graceful fallbacks when necessary.
try:
    from flask_cors import CORS
    _HAS_FLASK_CORS = True
except Exception:
    CORS = None
    _HAS_FLASK_CORS = False

from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask_bcrypt import Bcrypt

try:
    from flask_mail import Mail, Message
    _HAS_FLASK_MAIL = True
except Exception:
    Mail = None
    Message = None
    _HAS_FLASK_MAIL = False
from sqlalchemy.orm import DeclarativeBase
from datetime import datetime, time, timedelta
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
import pytz
import qrcode
from io import BytesIO
try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter, landscape
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet
    _HAS_REPORTLAB = True
except Exception:
    _HAS_REPORTLAB = False
import subprocess
import sys
import threading
from pathlib import Path

# Import multi-database manager
from db_manager import (
    create_teacher_database, 
    get_teacher_db_session, 
    delete_teacher_database,
    find_student_by_email,
    get_available_sections,
    TeacherStudent,
    TeacherAttendance,
    get_philippine_time as db_get_philippine_time
)

# Philippine timezone
PHILIPPINE_TZ = pytz.timezone('Asia/Manila')

def get_philippine_time():
    """Get current time in Philippine timezone"""
    return datetime.now(PHILIPPINE_TZ)

class Base(DeclarativeBase):
    pass

db = SQLAlchemy(model_class=Base)
app = Flask(__name__, static_folder='.', static_url_path='')

# Apply ProxyFix for Railway/Render/Heroku - this ensures Flask correctly detects
# HTTPS when behind a reverse proxy (required for secure session cookies)
try:
    from werkzeug.middleware.proxy_fix import ProxyFix
    # Trust the X-Forwarded-Proto, X-Forwarded-Host, X-Forwarded-For headers from 1 proxy
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1, x_for=1)
    print("✓ ProxyFix middleware applied for reverse proxy support")
except Exception as e:
    print(f"⚠ Could not apply ProxyFix: {e}")

app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-key-change-in-production")  # Use a fixed value for local dev

# Enable CORS for API routes (adjust origins in production). Allow credentialed requests.
cors_origins = os.environ.get('CORS_ORIGINS', '*')
if _HAS_FLASK_CORS and CORS is not None:
    CORS(app, resources={r"/api/*": {"origins": cors_origins}}, supports_credentials=True)
else:
    # If flask_cors isn't available, we still proceed but the
    # `apply_cors` after_request handler will set headers for API
    # responses. Log a warning for diagnostics.
    print("⚠ flask_cors not installed or bundled; CORS middleware disabled.")
# Signer for short-lived QR tokens
QR_SIGNING_SECRET = os.environ.get('QR_SIGNING_SECRET', app.secret_key)
QR_SIGNER = URLSafeTimedSerializer(QR_SIGNING_SECRET)

# Admin bootstrap password (do NOT hard-code in production). Set in env:
# ADMIN_BOOTSTRAP_PASSWORD (defaults to 'system123' for legacy compatibility).
ADMIN_BOOTSTRAP_PASSWORD = os.environ.get('ADMIN_BOOTSTRAP_PASSWORD', 'system123')

# Database configuration

# Require DATABASE_URL for production (Railway / Render / Heroku)
database_url = os.environ.get("DATABASE_URL")
if database_url:
    # Modern SQLAlchemy expects the scheme 'postgresql://' not 'postgres://'
    # Some providers (Railway/Heroku) provide a DATABASE_URL with 'postgres://',
    # so normalize it to avoid compatibility warnings/errors.
    if database_url.startswith('postgres://'):
        database_url = database_url.replace('postgres://', 'postgresql://', 1)
        print("⚠ Normalized DATABASE_URL scheme to 'postgresql://' for SQLAlchemy compatibility")
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
else:
    # Default to a local SQLite database for desktop / development runs
    db_path = os.environ.get('SQLITE_DB_PATH', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'attendance.db'))
    app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
    # Log a friendly warning when running without DATABASE_URL
    print(f"⚠ Using local SQLite DB for desktop/development: {db_path}")

app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_recycle": 300,
    "pool_pre_ping": True,
}
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# Flask-Mail configuration for Gmail SMTP (free for lifetime)
# Use environment variables for security: MAIL_USERNAME, MAIL_PASSWORD
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME', 'your-email@gmail.com')
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD', 'your-app-password')  # Use App Password for Gmail
app.config['MAIL_DEFAULT_SENDER'] = os.environ.get('MAIL_DEFAULT_SENDER', 'QR Attendance System <noreply@qrattendance.com>')

# Session cookie settings for cross-origin mobile app access (Capacitor WebView)
# SameSite=None + Secure is REQUIRED for cookies to work in mobile WebViews making cross-origin requests
_is_production = bool(os.environ.get('DATABASE_URL') or os.environ.get('RAILWAY_ENVIRONMENT'))

# For mobile app: Always use SameSite=None with Secure=True (required for cross-origin cookies)
# This works for both web browser and mobile app access
app.config['SESSION_COOKIE_SAMESITE'] = os.environ.get('SESSION_COOKIE_SAMESITE', 'None')
app.config['SESSION_COOKIE_SECURE'] = True  # Required when SameSite=None
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_DOMAIN'] = os.environ.get('SESSION_COOKIE_DOMAIN', None)

# Make sessions permanent by default (survive browser close) and set reasonable lifetime
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=7)

print(f"✓ Session cookie config: SameSite={app.config['SESSION_COOKIE_SAMESITE']}, Secure={app.config['SESSION_COOKIE_SECURE']}, Production={_is_production}")

db.init_app(app)
bcrypt = Bcrypt(app)
try:
    from flask_migrate import Migrate
    migrate = Migrate(app, db)
except Exception:
    migrate = None
# Instantiate Flask-Mail only when available to avoid import-time errors
if _HAS_FLASK_MAIL and Mail is not None:
    try:
        mail = Mail(app)
    except Exception as e:
        print(f"⚠ Could not initialize Flask-Mail: {e}")
        mail = None
else:
    mail = None
    print("⚠ flask_mail not installed or bundled; email features disabled.")

login_manager = LoginManager(app)
login_manager.login_view = 'serve_index'
# Use 'basic' session protection for mobile app compatibility (Capacitor WebView)
# 'strong' can invalidate sessions unexpectedly in mobile WebViews due to user agent changes
login_manager.session_protection = 'basic'


@app.after_request
def apply_cors(response):
    # Ensure credentialed CORS responses echo the requesting Origin when appropriate
    try:
        if request.path.startswith('/api/'):
            origin = request.headers.get('Origin')
            if origin:
                response.headers['Access-Control-Allow-Origin'] = origin
                response.headers['Vary'] = 'Origin'
                response.headers['Access-Control-Allow-Credentials'] = 'true'
                # Allow necessary methods/headers for clients
                response.headers.setdefault('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS')
                response.headers.setdefault('Access-Control-Allow-Headers', 'Content-Type, Authorization')
    except Exception:
        pass
    return response

class Student(UserMixin, db.Model):
    __tablename__ = 'students'
    
    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(200), nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    section = db.Column(db.String(50), nullable=True)
    grade_level = db.Column(db.String(10), nullable=True)
    qr_code = db.Column(db.LargeBinary)
    teacher_id = db.Column(db.Integer, db.ForeignKey('teachers.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: get_philippine_time())
    
    # Guardian information
    guardian_name = db.Column(db.String(200), nullable=True)
    guardian_email = db.Column(db.String(150), nullable=True)
    guardian_phone = db.Column(db.String(20), nullable=True)
    notify_on_checkin = db.Column(db.Boolean, default=True)
    notify_on_checkout = db.Column(db.Boolean, default=True)
    
    attendances = db.relationship('Attendance', backref='student', lazy=True, cascade='all, delete-orphan')
    
    def get_id(self):
        return f'student_{self.id}'
    
    def generate_qr_code(self):
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=4,
        )
        qr_data = f'STUDENT_{self.id}_{self.email}'
        qr.add_data(qr_data)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        
        buffer = BytesIO()
        img.save(buffer, format='PNG')
        self.qr_code = buffer.getvalue()
        buffer.close()
    
    def __repr__(self):
        return f'<Student {self.full_name}>'

class Teacher(UserMixin, db.Model):
    __tablename__ = 'teachers'
    
    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(200), nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    section = db.Column(db.String(50), nullable=True)  # Custom section name
    grade_level = db.Column(db.String(10), nullable=True)  # '11' or '12'
    db_name = db.Column(db.String(100), nullable=True)  # Teacher's database name
    created_at = db.Column(db.DateTime, default=lambda: get_philippine_time())
    
    def get_id(self):
        return f'teacher_{self.id}'
    
    def __repr__(self):
        return f'<Teacher {self.full_name} - Grade {self.grade_level} {self.section}>'

class Attendance(db.Model):
    __tablename__ = 'attendance'
    
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('students.id'), nullable=False)
    timestamp = db.Column(db.DateTime, default=lambda: get_philippine_time())
    status = db.Column(db.String(20), default='check_in')  # 'check_in' or 'check_out'
    
    def __repr__(self):
        return f'<Attendance {self.student_id} at {self.timestamp} - {self.status}>'

class AdminConfig(db.Model):
    """Admin configuration for attendance times and settings"""
    __tablename__ = 'admin_config'
    
    id = db.Column(db.Integer, primary_key=True)
    check_in_start_time = db.Column(db.String(5), default='07:00')  # HH:MM format
    check_in_end_time = db.Column(db.String(5), default='08:00')  # HH:MM format - students are LATE after this
    check_out_start_time = db.Column(db.String(5), default='16:00')  # HH:MM format
    check_out_end_time = db.Column(db.String(5), default='17:00')  # HH:MM format - CUTTING if not checked out after this
    
    # Auto-mark settings
    auto_mark_absent_enabled = db.Column(db.Boolean, default=True)
    auto_mark_cutting_enabled = db.Column(db.Boolean, default=True)
    
    # Notification settings
    email_notifications_enabled = db.Column(db.Boolean, default=True)
    notify_on_present = db.Column(db.Boolean, default=True)
    notify_on_absent = db.Column(db.Boolean, default=True)
    notify_on_late = db.Column(db.Boolean, default=True)
    notify_on_cutting = db.Column(db.Boolean, default=True)
    # Notify on EXCUSED status
    notify_on_excused = db.Column(db.Boolean, default=True)
    
    # Email SMTP settings (stored in database for portability)
    smtp_email = db.Column(db.String(150), default='lj.xnkzk@gmail.com')
    smtp_password = db.Column(db.String(255), default='qkxe lmgl gazz khil')
    smtp_server = db.Column(db.String(100), default='smtp.gmail.com')
    smtp_port = db.Column(db.Integer, default=587)
    # Afternoon shift times (defaults chosen to typical afternoon schedule)
    afternoon_check_in_start_time = db.Column(db.String(5), default='13:00')
    afternoon_check_in_end_time = db.Column(db.String(5), default='14:00')
    afternoon_check_out_start_time = db.Column(db.String(5), default='17:00')
    afternoon_check_out_end_time = db.Column(db.String(5), default='18:00')
    
    updated_at = db.Column(db.DateTime, default=lambda: get_philippine_time(), onupdate=lambda: get_philippine_time())
    
    def __repr__(self):
        return f'<AdminConfig check_in:{self.check_in_start_time}-{self.check_in_end_time}>'

def is_teacher(user):
    return isinstance(user, Teacher)

def teacher_required(f):
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            return jsonify({'success': False, 'error': 'Authentication required'}), 401
        if not is_teacher(current_user):
            return jsonify({'success': False, 'error': 'Teacher access required'}), 403
        return f(*args, **kwargs)
    return decorated_function

# ==================== EMAIL NOTIFICATION FUNCTIONS ====================

def get_email_config():
    """Get email configuration from database"""
    config = AdminConfig.query.first()
    if config and config.smtp_email and config.smtp_password:
        return {
            'email': config.smtp_email,
            'password': config.smtp_password,
            'server': config.smtp_server or 'smtp.gmail.com',
            'port': config.smtp_port or 587
        }
    # Fallback to environment variables
    return {
        'email': os.environ.get('MAIL_USERNAME', ''),
        'password': os.environ.get('MAIL_PASSWORD', ''),
        'server': 'smtp.gmail.com',
        'port': 587
    }


def select_shift(config, now_time):
    """Choose 'morning' or 'afternoon' based on configured windows and current time.

    Falls back to 'morning' when uncertain.
    """
    from datetime import datetime

    def to_time(s):
        try:
            return datetime.strptime(s, '%H:%M').time()
        except Exception:
            return None

    # Morning window uses existing check_* fields
    m_start = to_time(getattr(config, 'check_in_start_time', '07:00'))
    m_end = to_time(getattr(config, 'check_out_end_time', '17:00'))

    # Afternoon window uses afternoon_* fields
    a_start = to_time(getattr(config, 'afternoon_check_in_start_time', '13:00'))
    a_end = to_time(getattr(config, 'afternoon_check_out_end_time', '18:00'))

    if m_start and m_end and m_start <= now_time <= m_end:
        return 'morning'
    if a_start and a_end and a_start <= now_time <= a_end:
        return 'afternoon'
    # default
    return 'morning'

def send_email_async(subject, recipients, body, html_body=None):
    """Send email asynchronously using database config"""
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    from email.header import Header
    from email import policy
    
    # Get email config BEFORE starting thread (while still in Flask context)
    email_config = get_email_config()
    
    def send_mail():
        try:
            if not email_config['email'] or not email_config['password']:
                print("✗ Email not configured - skipping notification")
                return
            
            # Create message with UTF-8 encoding and SMTPUTF8 policy
            msg = MIMEMultipart('alternative', policy=policy.SMTP)
            msg['Subject'] = str(Header(subject, 'utf-8'))
            msg['From'] = f"QR Attendance <{email_config['email']}>"
            msg['To'] = ', '.join(recipients)

            # Attach both plain text and HTML with UTF-8 encoding
            msg.attach(MIMEText(body, 'plain', 'utf-8'))
            if html_body:
                msg.attach(MIMEText(html_body, 'html', 'utf-8'))

            # Send via SMTP using as_bytes() for full Unicode support
            with smtplib.SMTP(email_config['server'], email_config['port']) as server:
                server.starttls()
                # Remove spaces from password (for app passwords)
                password = email_config['password'].replace(' ', '')
                server.login(email_config['email'], password)
                server.sendmail(email_config['email'], recipients, msg.as_bytes())

            print(f"✓ Email sent successfully to {recipients}: {subject}")
        except Exception as e:
            print(f"✗ Error sending email: {str(e)}")
    
    thread = threading.Thread(target=send_mail)
    thread.daemon = True
    thread.start()

def send_attendance_notification(guardian_email, guardian_name, student_name, status, timestamp, check_in_end_time, check_out_end_time):
    """
    Send attendance notification email to guardian
    Status: PRESENT, ABSENT, LATE, CUTTING (or with "(Checked Out)" suffix)
    """
    if not guardian_email:
        print(f"✗ No guardian email for {student_name}")
        return
    
    # Get admin config to check if notifications are enabled
    config = AdminConfig.query.first()
    if not config or not config.email_notifications_enabled:
        print(f"✗ Email notifications disabled or no config")
        return
    
    # Extract base status (remove "(Checked Out)" suffix if present)
    base_status = status.replace(' (Checked Out)', '') if isinstance(status, str) else status
    
    # Check if this status notification is enabled
    if 'PRESENT' in str(base_status) and not config.notify_on_present:
        print(f"✗ PRESENT notifications disabled")
        return
    elif 'ABSENT' in str(base_status) and not config.notify_on_absent:
        print(f"✗ ABSENT notifications disabled")
        return
    elif 'LATE' in str(base_status) and not config.notify_on_late:
        print(f"✗ LATE notifications disabled")
        return
    elif 'CUTTING' in str(base_status) and not config.notify_on_cutting:
        print(f"✗ CUTTING notifications disabled")
        return
    elif 'EXCUSED' in str(base_status) and not getattr(config, 'notify_on_excused', True):
        print(f"✗ EXCUSED notifications disabled")
        return
    
    print(f"✓ Sending {status} notification for {student_name} to {guardian_email}")
    
    timestamp_str = timestamp.strftime('%I:%M %p') if isinstance(timestamp, datetime) else str(timestamp)
    date_str = timestamp.strftime('%B %d, %Y') if isinstance(timestamp, datetime) else str(timestamp.date())
    
    # Check if this is a checkout notification
    is_checkout = '(Checked Out)' in str(status)
    
    # Status-specific message
    if 'PRESENT' in str(status):
        if is_checkout:
            status_msg = f"✓ PRESENT - checked out"
            subject = f"✓ {student_name} has Checked Out"
            message_detail = f"Your child {student_name} has successfully checked out at {timestamp_str} on {date_str}."
        else:
            status_msg = f"✓ PRESENT - checked in on time"
            subject = f"✓ {student_name} is Present at Class"
            message_detail = f"Your child {student_name} has successfully checked in at {timestamp_str} on {date_str}."
    elif 'ABSENT' in str(status):
        status_msg = f"✗ ABSENT - did not check in by {check_in_end_time}"
        subject = f"✗ {student_name} is Marked ABSENT"
        message_detail = f"Your child {student_name} did not check in by {check_in_end_time} on {date_str}. They have been marked ABSENT."
    elif 'LATE' in str(status):
        if is_checkout:
            status_msg = f"⏱ LATE - checked out"
            subject = f"⏱ {student_name} (Late) has Checked Out"
            message_detail = f"Your child {student_name} has checked out at {timestamp_str} on {date_str}. They arrived late today."
        else:
            status_msg = f"⏱ LATE - checked in after {check_in_end_time}"
            subject = f"⏱ {student_name} Arrived LATE to Class"
            message_detail = f"Your child {student_name} checked in at {timestamp_str} on {date_str}, which is after the class start time ({check_in_end_time}). They have been marked LATE."
    elif 'CUTTING' in str(status):
        status_msg = f"⚠ CUTTING - did not check out by {check_out_end_time}"
        subject = f"⚠ {student_name} is Marked CUTTING"
        message_detail = f"Your child {student_name} did not check out by {check_out_end_time} on {date_str}. They have been marked CUTTING (did not complete the class)."
    elif 'EXCUSED' in str(status):
        status_msg = f"ℹ EXCUSED - teacher provided an excuse"
        subject = f"ℹ {student_name} is Marked EXCUSED"
        message_detail = f"Your child {student_name} has been marked as EXCUSED on {date_str}. Please contact the school for details."
    else:
        status_msg = f"{status}"
        subject = f"Attendance Update: {student_name}"
        message_detail = f"Attendance status for {student_name} has been updated to {status}."
    
    html_body = f"""
    <html>
        <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
            <div style="max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #ddd; border-radius: 5px;">
                <h2 style="color: #0066cc;">Attendance Notification</h2>
                <p>Dear {guardian_name},</p>
                <p>{message_detail}</p>
                <div style="background-color: #f5f5f5; padding: 15px; border-radius: 5px; margin: 20px 0;">
                    <p><strong>Student Name:</strong> {student_name}</p>
                    <p><strong>Status:</strong> <span style="color: {'green' if 'PRESENT' in str(status) else 'red'}; font-weight: bold;">{status_msg}</span></p>
                    <p><strong>Time:</strong> {timestamp_str}</p>
                    <p><strong>Date:</strong> {date_str}</p>
                </div>
                <p>If you have any questions or concerns, please contact the school administration.</p>
                <hr style="border: none; border-top: 1px solid #ddd; margin: 20px 0;">
                <p style="font-size: 12px; color: #666;">This is an automated notification from the QR Attendance System. Please do not reply to this email.</p>
            </div>
        </body>
    </html>
    """
    
    body = f"""
    Dear {guardian_name},
    
    {message_detail}
    
    Student Name: {student_name}
    Status: {status_msg}
    Time: {timestamp_str}
    Date: {date_str}
    
    If you have any questions or concerns, please contact the school administration.
    
    This is an automated notification from the QR Attendance System.
    """
    
    send_email_async(subject, [guardian_email], body, html_body)

def determine_attendance_status(check_in_time, check_in_start, check_in_end, check_out_time, check_out_end):
    """
    Determine attendance status based on check-in and check-out times
    
    Returns: (status, message)
    status: PRESENT, ABSENT, LATE, CUTTING
    """
    check_in_start_time = datetime.strptime(check_in_start, '%H:%M').time()
    check_in_end_time = datetime.strptime(check_in_end, '%H:%M').time()
    check_out_end_time = datetime.strptime(check_out_end, '%H:%M').time()
    
    # No check-in recorded
    if not check_in_time:
        return 'ABSENT', 'Student did not check in'
    
    check_in_actual_time = check_in_time.time() if isinstance(check_in_time, datetime) else check_in_time
    
    # Determine if check-in was on time or late
    if check_in_actual_time <= check_in_end_time:
        status = 'PRESENT'
    else:
        status = 'LATE'
    
    # Check if student didn't check out (CUTTING)
    if check_out_time is None:
        # Get current time
        current_time = get_philippine_time().time()
        if current_time > check_out_end_time:
            # It's past checkout time and student hasn't checked out
            status = 'CUTTING'
    
    return status, f'Status: {status}'

@login_manager.user_loader
def load_user(user_id):
    user_type, user_id = user_id.split('_')
    if user_type == 'student':
        return db.session.get(Student, int(user_id))
    elif user_type == 'teacher':
        return db.session.get(Teacher, int(user_id))
    return None

def migrate_database():
    """Add missing columns to existing tables for database upgrades"""
    from sqlalchemy import inspect, text
    
    inspector = inspect(db.engine)
    
    # Check AdminConfig table for missing columns
    if 'admin_config' in inspector.get_table_names():
        existing_columns = [col['name'] for col in inspector.get_columns('admin_config')]
        
        # Define new columns that might be missing (column_name, type, default)
        new_columns = [
            ('smtp_email', 'VARCHAR(150)', "'lj.xnkzk@gmail.com'"),
            ('smtp_password', 'VARCHAR(255)', "'qkxe lmgl gazz khil'"),
            ('smtp_server', 'VARCHAR(100)', "'smtp.gmail.com'"),
            ('smtp_port', 'INTEGER', '587'),
            ('afternoon_check_in_start_time', 'VARCHAR(5)', "'13:00'"),
            ('afternoon_check_in_end_time', 'VARCHAR(5)', "'14:00'"),
            ('afternoon_check_out_start_time', 'VARCHAR(5)', "'17:00'"),
            ('afternoon_check_out_end_time', 'VARCHAR(5)', "'18:00'"),
        ]
        
        for col_name, col_type, default_value in new_columns:
            if col_name not in existing_columns:
                try:
                    # SQLite syntax for adding column with default
                    db.session.execute(text(f'ALTER TABLE admin_config ADD COLUMN {col_name} {col_type} DEFAULT {default_value}'))
                    db.session.commit()
                    print(f"Added missing column: admin_config.{col_name}")
                except Exception as e:
                    print(f"Could not add column {col_name}: {e}")
                    db.session.rollback()
        # Add notify_on_excused if missing
        if 'notify_on_excused' not in existing_columns:
            try:
                db.session.execute(text('ALTER TABLE admin_config ADD COLUMN notify_on_excused BOOLEAN DEFAULT 1'))
                db.session.commit()
                print("Added missing column: admin_config.notify_on_excused")
            except Exception as e:
                print(f"Could not add column notify_on_excused: {e}")
                db.session.rollback()
    
    # Check students table for missing columns (guardian info)
    if 'students' in inspector.get_table_names():
        existing_columns = [col['name'] for col in inspector.get_columns('students')]
        
        student_columns = [
            ('grade_level', 'VARCHAR(10)', "NULL"),
            ('teacher_id', 'INTEGER', 'NULL'),
            ('guardian_name', 'VARCHAR(200)', 'NULL'),
            ('guardian_email', 'VARCHAR(150)', 'NULL'),
            ('guardian_phone', 'VARCHAR(20)', 'NULL'),
            ('notify_on_checkin', 'BOOLEAN', '1'),
            ('notify_on_checkout', 'BOOLEAN', '1'),
        ]
        
        for col_name, col_type, default_value in student_columns:
            if col_name not in existing_columns:
                try:
                    db.session.execute(text(f'ALTER TABLE students ADD COLUMN {col_name} {col_type} DEFAULT {default_value}'))
                    db.session.commit()
                    print(f"Added missing column: students.{col_name}")
                except Exception as e:
                    print(f"Could not add column {col_name}: {e}")
                    db.session.rollback()

with app.app_context():
    db.create_all()
    # Migrate existing database to add any missing columns
    migrate_database()
    # Create default admin config if it doesn't exist
    if AdminConfig.query.first() is None:
        default_config = AdminConfig()
        db.session.add(default_config)
        db.session.commit()

def get_db():
    """Return the database object and model classes for interactive use and tests.

    Returns a tuple: (db, Student, Teacher, Attendance)
    """
    return db, Student, Teacher, Attendance
@app.route('/')
def serve_index():
    return send_file('index.html')

@app.route('/index.html')
def index():
    return send_file('index.html')


@app.route('/api/admin/config', methods=['GET', 'PUT'])
@login_required
def admin_config_api():
    if not is_teacher(current_user) or current_user.email != 'admin@teacher':
        return jsonify({'success': False, 'error': 'Admin access required'}), 403

    if request.method == 'GET':
        config = AdminConfig.query.first()
        if not config:
            config = AdminConfig()
            db.session.add(config)
            db.session.commit()
        return jsonify({
            'success': True,
            'config': {
                'check_in_start_time': config.check_in_start_time,
                'check_in_end_time': config.check_in_end_time,
                'check_out_start_time': config.check_out_start_time,
                'check_out_end_time': config.check_out_end_time,
                'afternoon_check_in_start_time': getattr(config, 'afternoon_check_in_start_time', '13:00'),
                'afternoon_check_in_end_time': getattr(config, 'afternoon_check_in_end_time', '14:00'),
                'afternoon_check_out_start_time': getattr(config, 'afternoon_check_out_start_time', '17:00'),
                'afternoon_check_out_end_time': getattr(config, 'afternoon_check_out_end_time', '18:00'),
                'auto_mark_absent_enabled': config.auto_mark_absent_enabled,
                'auto_mark_cutting_enabled': config.auto_mark_cutting_enabled,
                'email_notifications_enabled': config.email_notifications_enabled,
                'notify_on_present': config.notify_on_present,
                'notify_on_absent': config.notify_on_absent,
                'notify_on_late': config.notify_on_late,
                'notify_on_cutting': config.notify_on_cutting,
                'notify_on_excused': getattr(config, 'notify_on_excused', True),
                'smtp_email': config.smtp_email,
                'smtp_password': config.smtp_password,
                'smtp_server': config.smtp_server,
                'smtp_port': config.smtp_port
            }
        }), 200

    # PUT - update config
    data = request.get_json() if request.is_json else request.form
    config = AdminConfig.query.first()
    if not config:
        config = AdminConfig()
        db.session.add(config)

    # Basic time fields
    for field in ['check_in_start_time','check_in_end_time','check_out_start_time','check_out_end_time',
                  'afternoon_check_in_start_time','afternoon_check_in_end_time','afternoon_check_out_start_time','afternoon_check_out_end_time']:
        if field in data:
            setattr(config, field, data.get(field))

    # Flags
    for flag in ['auto_mark_absent_enabled','auto_mark_cutting_enabled','email_notifications_enabled',
                 'notify_on_present','notify_on_absent','notify_on_late','notify_on_cutting','notify_on_excused']:
        if flag in data:
            val = data.get(flag)
            # handle checkbox true/false or 'on'
            if isinstance(val, str):
                val = val.lower() in ('true','1','on','yes')
            setattr(config, flag, bool(val))

    # SMTP
    for s in ['smtp_email','smtp_password','smtp_server','smtp_port']:
        if s in data:
            setattr(config, s, data.get(s))

    db.session.commit()
    return jsonify({'success': True, 'message': 'Admin config updated'}), 200


@app.route('/accountcreate.html')
def accountcreate():
    return send_file('accountcreate.html')

@app.route('/admin.html')
@login_required
def admin():
    if not is_teacher(current_user):
        return redirect('/')
    return send_file('admin.html')

@app.route('/teacher.html')
@login_required
def teacher():
    print(f"[TEACHER PAGE] current_user authenticated: {current_user.is_authenticated if current_user else False}")
    print(f"[TEACHER PAGE] session keys: {list(session.keys())}")
    if not is_teacher(current_user):
        print(f"[TEACHER PAGE] Redirecting - user is not a teacher")
        return redirect('/')
    return send_file('teacher.html')

@app.route('/student.html')
def student():
    # Allow session-based student authentication
    print(f"[STUDENT PAGE] session keys: {list(session.keys())}")
    print(f"[STUDENT PAGE] logged_in: {session.get('logged_in')}, user_type: {session.get('user_type')}")
    print(f"[STUDENT PAGE] request.is_secure: {request.is_secure}, scheme: {request.scheme}")
    print(f"[STUDENT PAGE] cookies received: {list(request.cookies.keys())}")
    if session.get('logged_in') and session.get('user_type') == 'student':
        return send_file('student.html')
    print(f"[STUDENT PAGE] Redirecting - not logged in as student")
    return redirect('/')

@app.route('/api/signup', methods=['POST'])
def signup():
    """Student signup - creates student in the assigned teacher's database"""
    try:
        data = request.get_json() if request.is_json else request.form
        full_name = data.get('full_name') or data.get('fullname')
        email = data.get('email')
        password = data.get('password')
        confirm_password = data.get('confirm_password') or data.get('cpwd')
        grade_level = data.get('grade_level')
        section = data.get('section')

        if not all([full_name, email, password, confirm_password, grade_level, section]):
            return jsonify({'success': False, 'error': 'All fields are required (including grade level and section)'}), 400

        if password != confirm_password:
            return jsonify({'success': False, 'error': 'Passwords do not match'}), 400

        # Validate grade level
        if grade_level not in ['11', '12']:
            return jsonify({'success': False, 'error': 'Invalid grade level. Must be 11 or 12.'}), 400

        # Find teacher with matching section and grade level
        teacher = Teacher.query.filter_by(section=section, grade_level=grade_level).first()
        if not teacher:
            return jsonify({
                'success': False,
                'error': f'No teacher assigned to Grade {grade_level} - {section}. Please contact admin.'
            }), 400

        # Check if email already exists in main Student table
        existing = Student.query.filter_by(email=email).first()
        if existing:
            return jsonify({'success': False, 'error': 'Email already registered in this section'}), 400

        hashed_password = bcrypt.generate_password_hash(password).decode('utf-8')
        guardian_name = data.get('guardian_name', '')
        guardian_email = data.get('guardian_email', '')
        guardian_phone = data.get('guardian_phone', '')

        new_student = Student(
            full_name=full_name,
            email=email,
            password_hash=hashed_password,
            section=section,
            grade_level=grade_level,
            teacher_id=teacher.id,
            guardian_name=guardian_name if guardian_name else None,
            guardian_email=guardian_email if guardian_email else None,
            guardian_phone=guardian_phone if guardian_phone else None,
            notify_on_checkin=True,
            notify_on_checkout=True
        )
        new_student.generate_qr_code()
        db.session.add(new_student)
        db.session.commit()
        print(f"[SIGNUP] Student {email} added to main Student table.")

        # Also add the student to the teacher's per-teacher database so
        # student-facing APIs (which read from the teacher DB) can find them.
        try:
            if teacher and teacher.db_name:
                Session = get_teacher_db_session(teacher.db_name)
                sess = Session()
                try:
                    teacher_student = TeacherStudent(
                        full_name=new_student.full_name,
                        email=new_student.email,
                        password_hash=new_student.password_hash,
                        section=new_student.section or section,
                        grade_level=new_student.grade_level or grade_level,
                        teacher_id=teacher.id,
                        guardian_name=new_student.guardian_name,
                        guardian_email=new_student.guardian_email,
                        guardian_phone=new_student.guardian_phone,
                        notify_on_checkin=1 if new_student.notify_on_checkin else 0,
                        notify_on_checkout=1 if new_student.notify_on_checkout else 0
                    )
                    sess.add(teacher_student)
                    sess.commit()
                    # Generate QR code now that teacher_student.id exists
                    try:
                        teacher_student.generate_qr_code()
                        sess.commit()
                    except Exception:
                        sess.rollback()
                finally:
                    sess.close()
        except Exception as e:
            print(f"[SIGNUP] Warning: failed to add student to teacher DB: {e}")

        return jsonify({
            'success': True,
            'message': f'Account created successfully for Grade {grade_level} - {section}',
            'redirect': '/?signup=success'
        }), 201


    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/admin/student-stats.csv', methods=['GET'])
@login_required
def download_student_stats_csv():
    """Return student statistics as CSV for a date range (same params as student-stats)."""
    if not is_teacher(current_user) or current_user.email != 'admin@teacher':
        return jsonify({'success': False, 'error': 'Admin access required'}), 403
    try:
        # Re-use the JSON generator logic by calling the function internals
        resp = get_admin_student_stats()
        if resp[1] != 200:
            return resp
        data = resp[0].get_json()
        students = data.get('students', [])

        import csv
        output = BytesIO()
        writer = csv.writer(output)
        # Header
        writer.writerow(['student_id','name','email','teacher_name','section','grade_level','total_days','present','absent','late','cutting','excused','attendance_percentage','last_checkin'])
        for s in students:
            writer.writerow([
                s.get('student_id'),
                s.get('name'),
                s.get('email'),
                s.get('teacher_name'),
                s.get('section'),
                s.get('grade_level'),
                s.get('total_days'),
                s.get('present'),
                s.get('absent'),
                s.get('late'),
                s.get('cutting'),
                s.get('excused'),
                s.get('attendance_percentage'),
                s.get('last_checkin')
            ])
        output.seek(0)
        filename = f"student-stats-{data.get('start')}_to_{data.get('end')}.csv"
        return send_file(output, mimetype='text/csv', as_attachment=True, download_name=filename)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/admin/student-stats.pdf', methods=['GET'])
@login_required
def download_student_stats_pdf():
    """Generate a simple tabular PDF of student statistics."""
    if not is_teacher(current_user) or current_user.email != 'admin@teacher':
        return jsonify({'success': False, 'error': 'Admin access required'}), 403
    if not _HAS_REPORTLAB:
        return jsonify({'success': False, 'error': 'PDF generation requires reportlab (not installed)'}), 501

    try:
        resp = get_admin_student_stats()
        if resp[1] != 200:
            return resp
        data = resp[0].get_json()
        students = data.get('students', [])

        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=landscape(letter), leftMargin=18, rightMargin=18, topMargin=18, bottomMargin=18)
        elements = []
        styles = getSampleStyleSheet()
        title = Paragraph(f"Student Statistics: {data.get('start')} to {data.get('end')}", styles['Heading2'])
        elements.append(title)
        elements.append(Spacer(1,12))

        # Table header + rows
        table_data = [[
            'ID','Name','Email','Teacher','Section','Present','Absent','Late','Cutting','%','Last Check-in'
        ]]
        for s in students:
            table_data.append([
                str(s.get('student_id') or ''),
                s.get('name') or '',
                s.get('email') or '',
                s.get('teacher_name') or '',
                s.get('section') or '',
                str(s.get('present') or 0),
                str(s.get('absent') or 0),
                str(s.get('late') or 0),
                str(s.get('cutting') or 0),
                (str(s.get('attendance_percentage')) + '%') if s.get('attendance_percentage') is not None else '-',
                s.get('last_checkin') or '-'
            ])

        table = Table(table_data, repeatRows=1)
        table.setStyle(TableStyle([
            ('BACKGROUND',(0,0),(-1,0),colors.HexColor('#f4f4f4')),
            ('GRID',(0,0),(-1,-1),0.25,colors.grey),
            ('ALIGN',(0,0),(-1,-1),'LEFT'),
            ('VALIGN',(0,0),(-1,-1),'MIDDLE'),
        ]))
        elements.append(table)
        doc.build(elements)
        buffer.seek(0)
        filename = f"student-stats-{data.get('start')}_to_{data.get('end')}.pdf"
        return send_file(buffer, mimetype='application/pdf', as_attachment=True, download_name=filename)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/admin/dashboard-stats.csv', methods=['GET'])
@login_required
def download_dashboard_stats_csv():
    """Download aggregated dashboard stats as CSV (summary + by-section)."""
    if not is_teacher(current_user) or current_user.email != 'admin@teacher':
        return jsonify({'success': False, 'error': 'Admin access required'}), 403
    try:
        resp = get_dashboard_stats()
        if resp[1] != 200:
            return resp
        data = resp[0].get_json()
        stats = data.get('stats', {})
        output = BytesIO()
        import csv
        writer = csv.writer(output)
        # Summary
        writer.writerow(['date','total_students','present','absent','late','cutting','excused'])
        writer.writerow([data.get('date'), stats.get('total_students'), stats.get('present'), stats.get('absent'), stats.get('late'), stats.get('cutting'), stats.get('excused')])
        writer.writerow([])
        # By-section header
        writer.writerow(['section','total','present','absent','late','cutting','excused'])
        for section, s in stats.get('by_section', {}).items():
            writer.writerow([section, s.get('total'), s.get('present'), s.get('absent'), s.get('late'), s.get('cutting'), s.get('excused')])
        output.seek(0)
        filename = f"attendance-summary-{data.get('date')}.csv"
        return send_file(output, mimetype='text/csv', as_attachment=True, download_name=filename)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/admin/dashboard-stats.pdf', methods=['GET'])
@login_required
def download_dashboard_stats_pdf():
    """Generate PDF for aggregated dashboard stats."""
    if not is_teacher(current_user) or current_user.email != 'admin@teacher':
        return jsonify({'success': False, 'error': 'Admin access required'}), 403
    if not _HAS_REPORTLAB:
        return jsonify({'success': False, 'error': 'PDF generation requires reportlab (not installed)'}), 501
    try:
        resp = get_dashboard_stats()
        if resp[1] != 200:
            return resp
        data = resp[0].get_json()
        stats = data.get('stats', {})

        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=landscape(letter), leftMargin=18, rightMargin=18, topMargin=18, bottomMargin=18)
        elements = []
        styles = getSampleStyleSheet()
        title = Paragraph(f"Attendance Summary - {data.get('date')}", styles['Heading2'])
        elements.append(title)
        elements.append(Spacer(1,12))

        # Summary table
        summary = [[ 'Metric', 'Value' ],
                   ['Total Students', stats.get('total_students')],
                   ['Present', stats.get('present')],
                   ['Absent', stats.get('absent')],
                   ['Late', stats.get('late')],
                   ['Cutting', stats.get('cutting')],
                   ['Excused', stats.get('excused')]]
        t = Table(summary, hAlign='LEFT')
        t.setStyle(TableStyle([
            ('BACKGROUND',(0,0),(1,0),colors.HexColor('#f4f4f4')),
            ('GRID',(0,0),(-1,-1),0.25,colors.grey),
            ('VALIGN',(0,0),(-1,-1),'MIDDLE')
        ]))
        elements.append(t)
        elements.append(Spacer(1,12))

        # By-section table
        section_data = [[ 'Section','Total','Present','Absent','Late','Cutting','Excused' ]]
        for section, s in stats.get('by_section', {}).items():
            section_data.append([section, s.get('total'), s.get('present'), s.get('absent'), s.get('late'), s.get('cutting'), s.get('excused')])

        st = Table(section_data, repeatRows=1)
        st.setStyle(TableStyle([
            ('BACKGROUND',(0,0),(-1,0),colors.HexColor('#f4f4f4')),
            ('GRID',(0,0),(-1,-1),0.25,colors.grey),
            ('VALIGN',(0,0),(-1,-1),'MIDDLE')
        ]))
        elements.append(st)
        doc.build(elements)
        buffer.seek(0)
        filename = f"attendance-summary-{data.get('date')}.pdf"
        return send_file(buffer, mimetype='application/pdf', as_attachment=True, download_name=filename)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/login', methods=['POST'])
def login():
    try:
        data = request.get_json() if request.is_json else request.form
        email = data.get('email')
        password = data.get('password') or data.get('pwd')
        user_type = data.get('user_type', 'student')

        if not all([email, password]):
            return jsonify({'success': False, 'error': 'Email and password are required'}), 400

        # Special admin credentials (bootstrap). Password comes from env var.
        if email == 'admin@teacher' and password == ADMIN_BOOTSTRAP_PASSWORD:
            # Check if admin teacher exists, if not create it
            admin_teacher = Teacher.query.filter_by(email='admin@teacher').first()
            if not admin_teacher:
                hashed_password = bcrypt.generate_password_hash(ADMIN_BOOTSTRAP_PASSWORD).decode('utf-8')
                admin_teacher = Teacher(
                    full_name='Admin',
                    email='admin@teacher',
                    password_hash=hashed_password
                )
                db.session.add(admin_teacher)
                db.session.commit()
            
            login_user(admin_teacher)
            # mark session explicitly for debugging and ensure permanence
            session['user_type'] = 'teacher'
            session.permanent = True
            session.modified = True
            print(f"[LOGIN] Origin: {request.headers.get('Origin')}")
            return jsonify({
                'success': True,
                'message': 'Login successful',
                'redirect': '/admin.html',
                'user_type': 'teacher'
            }), 200

        # Teacher login
        if user_type == 'teacher':
            teacher = Teacher.query.filter_by(email=email).first()
            if teacher and bcrypt.check_password_hash(teacher.password_hash, password):
                login_user(teacher)
                # mark session explicitly for debugging and ensure permanence
                session['user_type'] = 'teacher'
                session.permanent = True
                session.modified = True
                print(f"[LOGIN] Origin: {request.headers.get('Origin')}")
                print(f"[LOGIN] Teacher {email} logged in. Authenticated: {teacher.is_authenticated}")
                print(f"[LOGIN] Session keys after login: {list(session.keys())}")
                # Admin goes to admin panel, regular teachers to teacher panel
                if teacher.email == 'admin@teacher':
                    redirect_url = '/admin.html'
                else:
                    redirect_url = '/teacher.html'
                return jsonify({
                    'success': True,
                    'message': 'Login successful',
                    'redirect': redirect_url,
                    'user_type': 'teacher'
                }), 200
            else:
                print(f"[LOGIN] Teacher login failed for {email}")
                return jsonify({'success': False, 'error': 'Invalid email or password'}), 401

        if user_type == 'student':
            student = Student.query.filter_by(email=email).first()
            if student and bcrypt.check_password_hash(student.password_hash, password):
                login_user(student)
                # mark session explicitly for debugging and ensure permanence
                session['user_type'] = 'student'
                # For students we also set session flags used by the frontend
                session['logged_in'] = True
                session['student_id'] = student.id
                # Map to teacher DB so student APIs can use per-teacher DB
                # Resolve the teacher DB name robustly. Prefer direct id lookup,
                # fall back to matching section+grade if needed.
                session['student_db'] = None
                try:
                    teacher = db.session.get(Teacher, student.teacher_id) if student.teacher_id else None
                    if teacher and teacher.db_name:
                        session['student_db'] = teacher.db_name
                    else:
                        # fallback: find teacher by section and grade_level
                        fallback = Teacher.query.filter_by(section=student.section, grade_level=student.grade_level).first()
                        if fallback and fallback.db_name:
                            session['student_db'] = fallback.db_name
                except Exception:
                    session['student_db'] = None

                # Map student to the teacher-specific DB entry (TeacherStudent.id).
                # The main `Student.id` may not equal the id inside the per-teacher DB,
                # so attempt to resolve the teacher-db student record by email and
                # replace session['student_id'] with the teacher-db id. If the record
                # does not exist yet in the teacher DB, create it so downstream
                # endpoints (e.g. /api/student/current) can find the student.
                try:
                    db_name = session.get('student_db')
                    if db_name:
                        Session = get_teacher_db_session(db_name)
                        tsess = Session()
                        try:
                            # Try to find a matching student in the teacher DB by email
                            teacher_student = tsess.query(TeacherStudent).filter_by(email=student.email).first()
                            if teacher_student:
                                session['student_id'] = teacher_student.id
                            else:
                                # Create a synced record in the teacher DB so ids exist
                                new_ts = TeacherStudent(
                                    full_name=student.full_name,
                                    email=student.email,
                                    password_hash=student.password_hash,
                                    section=student.section or '',
                                    grade_level=student.grade_level or '',
                                    teacher_id=(teacher.id if 'teacher' in locals() and teacher else (fallback.id if 'fallback' in locals() and fallback else None)),
                                    created_at=student.created_at
                                )
                                tsess.add(new_ts)
                                tsess.commit()
                                session['student_id'] = new_ts.id
                        finally:
                            tsess.close()
                except Exception:
                    # If mapping fails, leave session['student_id'] as the main DB id
                    pass

                session.permanent = True
                session.modified = True
                print(f"[LOGIN] Origin: {request.headers.get('Origin')}")
                print(f"[LOGIN] Student {email} logged in. Authenticated: {student.is_authenticated}")
                print(f"[LOGIN] Session keys after login: {list(session.keys())}")
                return jsonify({
                    'success': True,
                    'message': 'Login successful',
                    'redirect': '/student.html',
                    'user_type': 'student'
                }), 200
            else:
                print(f"[LOGIN] Student login failed for {email}")
                return jsonify({'success': False, 'error': 'Invalid email or password'}), 401

        return jsonify({'success': False, 'error': 'Invalid user type'}), 400

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/logout', methods=['POST'])
def logout():
    # Clear session for students
    session.clear()
    if current_user.is_authenticated:
        logout_user()
    return jsonify({'success': True, 'redirect': '/'}), 200


@app.route('/api/debug-session', methods=['GET'])
def debug_session():
    """Debug endpoint to check session state - useful for troubleshooting Railway/production issues"""
    try:
        user_info = None
        if current_user and current_user.is_authenticated:
            user_info = {
                'id': current_user.get_id(),
                'is_teacher': isinstance(current_user, Teacher)
            }
        return jsonify({
            'session_keys': list(session.keys()),
            'session': {k: str(session.get(k)) for k in session.keys()},
            'current_user': user_info,
            'is_authenticated': current_user.is_authenticated if current_user else False,
            'request_info': {
                'is_secure': request.is_secure,
                'scheme': request.scheme,
                'x_forwarded_proto': request.headers.get('X-Forwarded-Proto'),
                'x_forwarded_for': request.headers.get('X-Forwarded-For'),
                'host': request.host,
                'cookies_received': bool(request.cookies),
                'cookie_names': list(request.cookies.keys())
            },
            'config': {
                'SESSION_COOKIE_SECURE': app.config.get('SESSION_COOKIE_SECURE'),
                'SESSION_COOKIE_SAMESITE': app.config.get('SESSION_COOKIE_SAMESITE'),
                'is_production': bool(os.environ.get('DATABASE_URL') or os.environ.get('RAILWAY_ENVIRONMENT'))
            }
        }), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/admin/create-teacher', methods=['POST'])
@teacher_required
def create_teacher():
    """Create a new teacher with section and grade level assignment"""
    try:
        data = request.get_json() if request.is_json else request.form
        full_name = data.get('fullname') or data.get('full_name')
        email = data.get('gmail') or data.get('email')
        password = data.get('password')
        section = data.get('section')
        grade_level = data.get('grade_level')

        if not all([full_name, email, password, section, grade_level]):
            return jsonify({'success': False, 'error': 'All fields are required (name, email, password, section, grade level)'}), 400

        # Validate grade level
        if grade_level not in ['11', '12']:
            return jsonify({'success': False, 'error': 'Grade level must be 11 or 12'}), 400

        if Teacher.query.filter_by(email=email).first():
            return jsonify({'success': False, 'error': 'Email already registered'}), 400

        # Check if section/grade combination already exists
        existing = Teacher.query.filter_by(section=section, grade_level=grade_level).first()
        if existing:
            return jsonify({
                'success': False, 
                'error': f'Grade {grade_level} - {section} is already assigned to {existing.full_name}'
            }), 400

        hashed_password = bcrypt.generate_password_hash(password).decode('utf-8')
        
        new_teacher = Teacher(
            full_name=full_name,
            email=email,
            password_hash=hashed_password,
            section=section,
            grade_level=grade_level
        )
        
        db.session.add(new_teacher)
        db.session.commit()

        # Create teacher's own database
        db_name = create_teacher_database(new_teacher.id, grade_level, section)
        new_teacher.db_name = db_name
        db.session.commit()

        return jsonify({
            'success': True,
            'message': f'Teacher account created for Grade {grade_level} - {section}',
            'teacher': {
                'id': new_teacher.id,
                'name': new_teacher.full_name,
                'section': section,
                'grade_level': grade_level
            }
        }), 201

    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500

# ==================== ADMIN CONFIGURATION ENDPOINTS ====================

@app.route('/api/admin/config', methods=['GET'])
@login_required
def get_admin_config():
    """Get admin configuration for attendance times and notification settings"""
    if not is_teacher(current_user) or current_user.email != 'admin@teacher':
        return jsonify({'success': False, 'error': 'Admin access required'}), 403
    
    try:
        config = AdminConfig.query.first()
        if not config:
            config = AdminConfig()
            db.session.add(config)
            db.session.commit()
        
        return jsonify({
            'success': True,
            'config': {
                'check_in_start_time': config.check_in_start_time,
                'check_in_end_time': config.check_in_end_time,
                'check_out_start_time': config.check_out_start_time,
                'check_out_end_time': config.check_out_end_time,
                'auto_mark_absent_enabled': config.auto_mark_absent_enabled,
                'auto_mark_cutting_enabled': config.auto_mark_cutting_enabled,
                'email_notifications_enabled': config.email_notifications_enabled,
                'notify_on_present': config.notify_on_present,
                'notify_on_absent': config.notify_on_absent,
                'notify_on_late': config.notify_on_late,
                'notify_on_cutting': config.notify_on_cutting,
                'notify_on_excused': getattr(config, 'notify_on_excused', True),
                'smtp_email': config.smtp_email or '',
                'smtp_password': config.smtp_password or '',
                'smtp_server': config.smtp_server or 'smtp.gmail.com',
                'smtp_port': config.smtp_port or 587
            }
        }), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/admin/config', methods=['PUT'])
@login_required
def update_admin_config():
    """Update admin configuration for attendance times and notification settings"""
    if not is_teacher(current_user) or current_user.email != 'admin@teacher':
        return jsonify({'success': False, 'error': 'Admin access required'}), 403
    
    try:
        data = request.get_json() if request.is_json else request.form
        
        config = AdminConfig.query.first()
        if not config:
            config = AdminConfig()
            db.session.add(config)
        
        # Update time settings if provided
        if 'check_in_start_time' in data:
            config.check_in_start_time = data['check_in_start_time']
        if 'check_in_end_time' in data:
            config.check_in_end_time = data['check_in_end_time']
        if 'check_out_start_time' in data:
            config.check_out_start_time = data['check_out_start_time']
        if 'check_out_end_time' in data:
            config.check_out_end_time = data['check_out_end_time']
        
        # Update auto-mark settings if provided
        if 'auto_mark_absent_enabled' in data:
            config.auto_mark_absent_enabled = bool(data['auto_mark_absent_enabled'])
        if 'auto_mark_cutting_enabled' in data:
            config.auto_mark_cutting_enabled = bool(data['auto_mark_cutting_enabled'])
        
        # Update notification settings if provided
        if 'email_notifications_enabled' in data:
            config.email_notifications_enabled = bool(data['email_notifications_enabled'])
        if 'notify_on_present' in data:
            config.notify_on_present = bool(data['notify_on_present'])
        if 'notify_on_absent' in data:
            config.notify_on_absent = bool(data['notify_on_absent'])
        if 'notify_on_late' in data:
            config.notify_on_late = bool(data['notify_on_late'])
        if 'notify_on_cutting' in data:
            config.notify_on_cutting = bool(data['notify_on_cutting'])
        
        # Update SMTP email settings if provided
        if 'smtp_email' in data:
            config.smtp_email = data['smtp_email']
        if 'smtp_password' in data:
            config.smtp_password = data['smtp_password']
        if 'smtp_server' in data:
            config.smtp_server = data['smtp_server']
        if 'smtp_port' in data:
            config.smtp_port = int(data['smtp_port'])
        
        db.session.commit();
        
        return jsonify({
            'success': True,
            'message': 'Configuration updated successfully',
            'config': {
                'check_in_start_time': config.check_in_start_time,
                'check_in_end_time': config.check_in_end_time,
                'check_out_start_time': config.check_out_start_time,
                'check_out_end_time': config.check_out_end_time,
                'auto_mark_absent_enabled': config.auto_mark_absent_enabled,
                'auto_mark_cutting_enabled': config.auto_mark_cutting_enabled,
                'email_notifications_enabled': config.email_notifications_enabled,
                'notify_on_present': config.notify_on_present,
                'notify_on_absent': config.notify_on_absent,
                'notify_on_late': config.notify_on_late,
                'notify_on_cutting': config.notify_on_cutting,
                'notify_on_excused': getattr(config, 'notify_on_excused', True),
                'smtp_email': config.smtp_email,
                'smtp_password': config.smtp_password,
                'smtp_server': config.smtp_server,
                'smtp_port': config.smtp_port
            }
        }), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/admin/test-email', methods=['POST'])
@login_required
def test_email():
    """Send a test email to verify SMTP settings"""
    if not is_teacher(current_user) or current_user.email != 'admin@teacher':
        return jsonify({'success': False, 'error': 'Admin access required'}), 403
    
    try:
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart
        
        data = request.get_json() if request.is_json else request.form
        test_recipient = data.get('test_email', current_user.email)
        
        email_config = get_email_config()
        
        if not email_config['email'] or not email_config['password']:
            return jsonify({'success': False, 'error': 'Email settings not configured'}), 400
        
        # Create test message
        msg = MIMEMultipart('alternative')
        msg['Subject'] = '✓ QR Attendance - Test Email'
        msg['From'] = f"QR Attendance <{email_config['email']}>"
        msg['To'] = test_recipient
        
        body = "This is a test email from QR Attendance System. If you received this, your email settings are configured correctly!"
        html_body = """
        <html>
            <body style="font-family: Arial, sans-serif; padding: 20px;">
                <h2 style="color: #48bb78;">✓ Email Configuration Successful!</h2>
                <p>This is a test email from <strong>QR Attendance System</strong>.</p>
                <p>If you received this, your email settings are configured correctly!</p>
                <hr>
                <p style="color: #666; font-size: 12px;">Sent from QR Attendance System</p>
            </body>
        </html>
        """
        
        msg.attach(MIMEText(body, 'plain'))
        msg.attach(MIMEText(html_body, 'html'))
        
        # Send via SMTP
        with smtplib.SMTP(email_config['server'], email_config['port']) as server:
            server.starttls()
            server.login(email_config['email'], email_config['password'].replace(' ', ''))
            server.sendmail(email_config['email'], [test_recipient], msg.as_string())
        
        return jsonify({
            'success': True,
            'message': f'Test email sent to {test_recipient}!'
        }), 200
        
    except Exception as e:
        return jsonify({'success': False, 'error': f'Failed to send test email: {str(e)}'}), 500

@app.route('/api/admin/dashboard-stats', methods=['GET'])
@login_required
def get_dashboard_stats():
    """Get attendance statistics for admin dashboard"""
    if not is_teacher(current_user) or current_user.email != 'admin@teacher':
        return jsonify({'success': False, 'error': 'Admin access required'}), 403
    
    try:
        now = get_philippine_time()
        today_str = now.date().strftime('%Y-%m-%d')
        # Determine current shift (morning/afternoon) using admin config
        config = AdminConfig.query.first()
        shift = select_shift(config, now.time())
        
        stats = {
            'total_students': 0,
            'present': 0,
            'absent': 0,
            'late': 0,
            'cutting': 0,
            'excused': 0,
            'by_section': {}
        }
        
        # Get all teachers with their databases
        teachers = Teacher.query.filter(Teacher.db_name.isnot(None)).all()
        
        for teacher in teachers:
            section_key = f"Grade {teacher.grade_level} - {teacher.section}"
            
            if section_key not in stats['by_section']:
                stats['by_section'][section_key] = {
                    'total': 0,
                    'present': 0,
                        'absent': 0,
                        'late': 0,
                        'cutting': 0,
                        'excused': 0
                }
            
            Session = get_teacher_db_session(teacher.db_name)
            sess = Session()
            
            try:
                # Count students
                students = sess.query(TeacherStudent).all()
                student_count = len(students)
                stats['total_students'] += student_count
                stats['by_section'][section_key]['total'] += student_count
                
                for student in students:
                    # Get today's attendance
                    attendance = sess.query(TeacherAttendance).filter(
                        TeacherAttendance.student_id == student.id,
                        TeacherAttendance.date == today_str,
                        TeacherAttendance.shift == shift
                    ).first()
                    
                    if attendance:
                        status = attendance.attendance_status
                        if status == 'PRESENT':
                            stats['present'] += 1
                            stats['by_section'][section_key]['present'] += 1
                        elif status == 'ABSENT':
                            stats['absent'] += 1
                            stats['by_section'][section_key]['absent'] += 1
                        elif status == 'LATE':
                            stats['late'] += 1
                            stats['by_section'][section_key]['late'] += 1
                        elif status == 'CUTTING':
                            stats['cutting'] += 1
                            stats['by_section'][section_key]['cutting'] += 1
                        elif status == 'EXCUSED':
                            stats['excused'] += 1
                            stats['by_section'][section_key]['excused'] += 1
                    else:
                        # No record means not yet marked (could be absent if past deadline)
                        stats['absent'] += 1
                        stats['by_section'][section_key]['absent'] += 1
            finally:
                sess.close()
        
        return jsonify({
            'success': True,
            'stats': stats,
            'date': today_str
        }), 200
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/admin/student-stats', methods=['GET'])
@login_required
def get_admin_student_stats():
    """Return per-student attendance statistics across teacher databases.
    Query params: start (YYYY-MM-DD), end (YYYY-MM-DD). Defaults to last 30 days.
    """
    if not is_teacher(current_user) or current_user.email != 'admin@teacher':
        return jsonify({'success': False, 'error': 'Admin access required'}), 403

    try:
        args = request.args
        end_str = args.get('end')
        start_str = args.get('start')

        now = get_philippine_time()
        end_date = None
        start_date = None
        if end_str:
            end_date = datetime.strptime(end_str, '%Y-%m-%d').date()
        else:
            end_date = now.date()

        if start_str:
            start_date = datetime.strptime(start_str, '%Y-%m-%d').date()
        else:
            start_date = end_date - timedelta(days=29)

        students_stats = []

        teachers = Teacher.query.filter(Teacher.db_name.isnot(None)).all()
        for teacher in teachers:
            section_key = f"Grade {teacher.grade_level} - {teacher.section}"
            Session = get_teacher_db_session(teacher.db_name)
            sess = Session()
            try:
                students = sess.query(TeacherStudent).all()
                for s in students:
                    # Gather attendance records in date range (date stored as YYYY-MM-DD)
                    records = sess.query(TeacherAttendance).filter(
                        TeacherAttendance.student_id == s.id,
                        TeacherAttendance.date >= start_date.strftime('%Y-%m-%d'),
                        TeacherAttendance.date <= end_date.strftime('%Y-%m-%d')
                    ).all()

                    total_days = (end_date - start_date).days + 1
                    present = sum(1 for r in records if (r.attendance_status or '').upper() == 'PRESENT')
                    absent = sum(1 for r in records if (r.attendance_status or '').upper() == 'ABSENT')
                    late = sum(1 for r in records if (r.attendance_status or '').upper() == 'LATE')
                    cutting = sum(1 for r in records if (r.attendance_status or '').upper() == 'CUTTING')
                    excused = sum(1 for r in records if (r.attendance_status or '').upper() == 'EXCUSED')

                    last_checkin = None
                    checkins = [r.check_in_time for r in records if getattr(r, 'check_in_time', None)]
                    if checkins:
                        last_checkin = max(checkins).isoformat()

                    attendance_percentage = None
                    if total_days > 0:
                        attendance_percentage = round((present / total_days) * 100, 2)

                    students_stats.append({
                        'student_id': s.id,
                        'name': s.full_name,
                        'email': s.email,
                        'section': s.section,
                        'grade_level': s.grade_level,
                        'teacher_id': s.teacher_id,
                        'teacher_name': teacher.full_name,
                        'total_days': total_days,
                        'present': present,
                        'absent': absent,
                        'late': late,
                        'cutting': cutting,
                        'excused': excused,
                        'last_checkin': last_checkin,
                        'attendance_percentage': attendance_percentage
                    })
            finally:
                sess.close()

        return jsonify({
            'success': True,
            'start': start_date.strftime('%Y-%m-%d'),
            'end': end_date.strftime('%Y-%m-%d'),
            'students': students_stats
        }), 200

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/students', methods=['GET'])
@login_required
def get_students():
    """Get students from teacher's own database"""
    if not is_teacher(current_user):
        return jsonify({'success': False, 'error': 'Teacher access required'}), 403
    try:
        # Admin can see all students from all databases
        if current_user.email == 'admin@teacher':
            # Get all teachers and their students
            all_students = []
            teachers = Teacher.query.filter(Teacher.db_name.isnot(None)).all()
            for teacher in teachers:
                Session = get_teacher_db_session(teacher.db_name)
                sess = Session()
                try:
                    students = sess.query(TeacherStudent).all()
                    for s in students:
                        all_students.append({
                            'id': s.id,
                            'name': s.full_name,
                            'email': s.email,
                            'section': s.section,
                            'grade_level': s.grade_level,
                            'teacher_id': s.teacher_id,
                            'teacher_name': teacher.full_name,
                            'created_at': s.created_at.isoformat() if s.created_at else None
                        })
                finally:
                    sess.close()
            return jsonify({'success': True, 'students': all_students}), 200
        
        # Regular teacher only sees their own students
        if not current_user.db_name:
            return jsonify({'success': True, 'students': []}), 200
        
        Session = get_teacher_db_session(current_user.db_name)
        sess = Session()
        try:
            students = sess.query(TeacherStudent).all()
            students_data = [{
                'id': s.id,
                'name': s.full_name,
                'email': s.email,
                'section': s.section,
                'grade_level': s.grade_level,
                'created_at': s.created_at.isoformat() if s.created_at else None
            } for s in students]
            return jsonify({'success': True, 'students': students_data}), 200
        finally:
            sess.close()
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/sections', methods=['GET'])
def get_sections():
    """Get all available section/grade combinations for student signup"""
    try:
        teachers = Teacher.query.filter(
            Teacher.section.isnot(None),
            Teacher.grade_level.isnot(None),
            Teacher.db_name.isnot(None)
        ).all()
        
        sections = [{
            'section': t.section,
            'grade_level': t.grade_level,
            'teacher_name': t.full_name,
            'display': f'Grade {t.grade_level} - {t.section}'
        } for t in teachers]
        
        return jsonify({'success': True, 'sections': sections}), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/teachers', methods=['GET'])
@login_required
def get_teachers():
    if not is_teacher(current_user):
        return jsonify({'success': False, 'error': 'Teacher access required'}), 403
    try:
        teachers = Teacher.query.all()
        teachers_data = [{
            'id': t.id,
            'name': t.full_name,
            'email': t.email,
            'section': t.section,
            'grade_level': t.grade_level,
            'db_name': t.db_name,
            'created_at': t.created_at.isoformat() if t.created_at else None
        } for t in teachers]

        return jsonify({'success': True, 'teachers': teachers_data}), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/teacher/<int:teacher_id>', methods=['GET', 'PUT', 'DELETE'])
@login_required
def manage_teacher(teacher_id):
    if not is_teacher(current_user):
        return jsonify({'success': False, 'error': 'Teacher access required'}), 403
    try:
        teacher = db.session.get(Teacher, teacher_id)
        if not teacher:
            return jsonify({'success': False, 'error': 'Teacher not found'}), 404

        if request.method == 'GET':
            return jsonify({
                'success': True,
                'teacher': {
                    'id': teacher.id,
                    'name': teacher.full_name,
                    'email': teacher.email,
                    'section': teacher.section,
                    'grade_level': teacher.grade_level
                }
            }), 200

        elif request.method == 'PUT':
            data = request.get_json() if request.is_json else request.form
            teacher.full_name = data.get('name', teacher.full_name)
            teacher.email = data.get('email', teacher.email)
            if 'grade_level' in data and data['grade_level']:
                teacher.grade_level = data.get('grade_level', teacher.grade_level)
            if 'section' in data and data['section']:
                teacher.section = data.get('section', teacher.section)

            db.session.commit()
            return jsonify({'success': True, 'message': 'Teacher updated successfully'}), 200

        elif request.method == 'DELETE':
            db.session.delete(teacher)
            db.session.commit()
            return jsonify({'success': True, 'message': 'Teacher deleted successfully'}), 200

    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500

# ==================== TEACHER STUDENT STATUS MANAGEMENT ====================

@app.route('/api/teacher/students', methods=['GET'])
@login_required
def get_teacher_students():
    """Get all students for the current teacher with their attendance status"""
    if not is_teacher(current_user):
        return jsonify({'success': False, 'error': 'Teacher access required'}), 403
    
    try:
        if not current_user.db_name:
            return jsonify({'success': False, 'error': 'Teacher database not configured'}), 400
        
        now = get_philippine_time()
        today_str = now.date().strftime('%Y-%m-%d')

        # Determine current shift based on admin config
        config = AdminConfig.query.first()
        if not config:
            config = AdminConfig()
            db.session.add(config)
            db.session.commit()
        shift = select_shift(config, now.time())

        Session = get_teacher_db_session(current_user.db_name)
        sess = Session()
        
        try:
            students = sess.query(TeacherStudent).all()
            
            students_data = []
            for student in students:
                # Get today's attendance for the current shift
                attendance = sess.query(TeacherAttendance).filter(
                    TeacherAttendance.student_id == student.id,
                    TeacherAttendance.date == today_str,
                    TeacherAttendance.shift == shift
                ).first()
                
                students_data.append({
                    'id': student.id,
                    'full_name': student.full_name,
                    'email': student.email,
                    'section': student.section,
                    'grade_level': student.grade_level,
                    'guardian_name': student.guardian_name,
                    'guardian_email': student.guardian_email,
                    'attendance_status': attendance.attendance_status if attendance else 'ABSENT',
                    'check_in_time': attendance.check_in_time.strftime('%I:%M %p') if attendance and attendance.check_in_time else None,
                    'check_out_time': attendance.check_out_time.strftime('%I:%M %p') if attendance and attendance.check_out_time else None
                })
            
            return jsonify({
                'success': True,
                'students': students_data,
                'date': today_str
            }), 200
        finally:
            sess.close()
            
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/teacher/student/<int:student_id>/status', methods=['PUT'])
@login_required
def update_student_status(student_id):
    """Teacher can update student attendance status"""
    if not is_teacher(current_user):
        return jsonify({'success': False, 'error': 'Teacher access required'}), 403
    
    try:
        if not current_user.db_name:
            return jsonify({'success': False, 'error': 'Teacher database not configured'}), 400
        
        data = request.get_json() if request.is_json else request.form
        new_status = data.get('status')
        reason = data.get('reason', 'Updated by teacher')
        
        # Validate status
        valid_statuses = ['PRESENT', 'ABSENT', 'LATE', 'CUTTING', 'EXCUSED']
        if new_status not in valid_statuses:
            return jsonify({
                'success': False, 
                'error': f'Invalid status. Must be one of: {", ".join(valid_statuses)}'
            }), 400
        
        now = get_philippine_time()
        today_str = now.date().strftime('%Y-%m-%d')

        # Get admin config for notification settings and determine shift
        config = AdminConfig.query.first()
        if not config:
            config = AdminConfig()
            db.session.add(config)
            db.session.commit()
        shift = select_shift(config, now.time())

        Session = get_teacher_db_session(current_user.db_name)
        sess = Session()
        
        try:
            student = sess.get(TeacherStudent, student_id)
            if not student:
                return jsonify({'success': False, 'error': 'Student not found'}), 404
            
            # Get or create today's attendance record for this shift
            attendance = sess.query(TeacherAttendance).filter(
                TeacherAttendance.student_id == student_id,
                TeacherAttendance.date == today_str,
                TeacherAttendance.shift == shift
            ).first()
            
            old_status = attendance.attendance_status if attendance else 'ABSENT'
            
            if not attendance:
                attendance = TeacherAttendance(
                    student_id=student_id,
                    status='manual',
                    attendance_status=new_status,
                    date=today_str,
                    shift=shift
                )
                sess.add(attendance)
            else:
                attendance.attendance_status = new_status
            
            sess.commit()
            
            # Send notification to guardian
            if student.guardian_email and config and config.email_notifications_enabled:
                send_attendance_notification(
                    guardian_email=student.guardian_email,
                    guardian_name=student.guardian_name or 'Parent/Guardian',
                    student_name=student.full_name,
                    status=f'{new_status} (Updated by Teacher: {reason})',
                    timestamp=now,
                    check_in_end_time=config.check_in_end_time if config else '08:00',
                    check_out_end_time=config.check_out_end_time if config else '17:00'
                )
            
            return jsonify({
                'success': True,
                'message': f'Status updated from {old_status} to {new_status}',
                'student_name': student.full_name,
                'old_status': old_status,
                'new_status': new_status
            }), 200
        finally:
            sess.close()
            
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/teacher/student/<int:student_id>/guardian', methods=['PUT'])
@login_required
def update_student_guardian(student_id):
    """Update student guardian information"""
    if not is_teacher(current_user):
        return jsonify({'success': False, 'error': 'Teacher access required'}), 403
    
    try:
        if not current_user.db_name:
            return jsonify({'success': False, 'error': 'Teacher database not configured'}), 400
        
        data = request.get_json() if request.is_json else request.form
        
        Session = get_teacher_db_session(current_user.db_name)
        sess = Session()
        
        try:
            student = sess.get(TeacherStudent, student_id)
            if not student:
                return jsonify({'success': False, 'error': 'Student not found'}), 404
            
            # Update guardian info
            if 'guardian_name' in data:
                student.guardian_name = data['guardian_name']
            if 'guardian_email' in data:
                student.guardian_email = data['guardian_email']
            if 'guardian_phone' in data:
                student.guardian_phone = data['guardian_phone']
            if 'notify_on_checkin' in data:
                student.notify_on_checkin = 1 if data['notify_on_checkin'] else 0
            if 'notify_on_checkout' in data:
                student.notify_on_checkout = 1 if data['notify_on_checkout'] else 0
            
            sess.commit()
            
            return jsonify({
                'success': True,
                'message': f'Guardian info updated for {student.full_name}',
                'guardian': {
                    'name': student.guardian_name,
                    'email': student.guardian_email,
                    'phone': student.guardian_phone,
                    'notify_on_checkin': bool(student.notify_on_checkin),
                    'notify_on_checkout': bool(student.notify_on_checkout)
                }
            }), 200
        finally:
            sess.close()
            
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/attendance', methods=['GET'])
@login_required
def get_attendance():
    if not is_teacher(current_user):
        return jsonify({'success': False, 'error': 'Teacher access required'}), 403
    try:
        # Get optional date filter from query params (format: YYYY-MM-DD)
        date_filter = request.args.get('date')
        
        query = Attendance.query.join(Student).order_by(Attendance.timestamp.desc())
        
        if date_filter:
            from datetime import datetime as dt
            try:
                filter_date = dt.strptime(date_filter, '%Y-%m-%d').date()
                query = query.filter(db.func.date(Attendance.timestamp) == filter_date)
            except ValueError:
                pass
        
        records = query.all()
        
        attendance_data = [{
            'id': a.id,
            'student_id': a.student_id,
            'student_name': a.student.full_name,
            'student_email': a.student.email,
            'timestamp': a.timestamp.isoformat() if a.timestamp else None,
            'status': a.status
        } for a in records]
        
        return jsonify({'success': True, 'attendance': attendance_data}), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/db-stats', methods=['GET'])
@login_required
def get_db_stats():
    if not is_teacher(current_user):
        return jsonify({'success': False, 'error': 'Teacher access required'}), 403
    try:
        teacher_count = Teacher.query.count()
        
        # Count students from all teacher databases
        student_count = 0
        attendance_count = 0
        
        teachers = Teacher.query.filter(Teacher.db_name.isnot(None)).all()
        for teacher in teachers:
            try:
                Session = get_teacher_db_session(teacher.db_name)
                sess = Session()
                student_count += sess.query(TeacherStudent).count()
                attendance_count += sess.query(TeacherAttendance).count()
                sess.close()
            except:
                pass
        
        return jsonify({
            'success': True,
            'students': student_count,
            'teachers': teacher_count,
            'attendance_records': attendance_count
        }), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/student/<int:student_id>', methods=['GET', 'DELETE'])
@login_required
def manage_student(student_id):
    if not is_teacher(current_user):
        return jsonify({'success': False, 'error': 'Teacher access required'}), 403
    try:
        student = db.session.get(Student, student_id)
        if not student:
            return jsonify({'success': False, 'error': 'Student not found'}), 404
        
        if request.method == 'GET':
            return jsonify({
                'success': True,
                'student': {
                    'id': student.id,
                    'name': student.full_name,
                    'email': student.email
                }
            }), 200
        elif request.method == 'DELETE':
            db.session.delete(student)
            db.session.commit()
            return jsonify({'success': True, 'message': 'Student deleted successfully'}), 200
            
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/student/<int:student_id>/qr-code', methods=['GET'])
@login_required
def get_qr_code(student_id):
    try:
        student = db.session.get(Student, student_id)
        if not student:
            return jsonify({'success': False, 'error': 'Student not found'}), 404
        
        if not is_teacher(current_user):
            if not isinstance(current_user, Student) or current_user.id != student_id:
                return jsonify({'success': False, 'error': 'Access denied'}), 403
        
        if not student.qr_code:
            student.generate_qr_code()
            db.session.commit()
        
        return send_file(
            BytesIO(student.qr_code),
            mimetype='image/png',
            as_attachment=True,
            download_name=f'qr_code_{student.id}.png'
        )
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/student/<int:student_id>/qr-token', methods=['GET'])
@login_required
def get_qr_token(student_id):
    """Return a short-lived signed token representing the student's QR payload."""
    try:
        student = db.session.get(Student, student_id)
        if not student:
            return jsonify({'success': False, 'error': 'Student not found'}), 404

        # Authorization: teacher (admin) or the student themselves (session)
        allowed = False
        if is_teacher(current_user):
            if current_user.email == 'admin@teacher' or current_user.id == student.teacher_id:
                allowed = True
            else:
                # Allow teachers to request tokens for main Student table if they are admin
                allowed = current_user.email == 'admin@teacher'

        if not allowed:
            # session-based student login
            if session.get('logged_in') and session.get('user_type') == 'student' and session.get('student_id') == student_id:
                allowed = True

        if not allowed:
            return jsonify({'success': False, 'error': 'Unauthorized'}), 401

        qr_data = f'STUDENT_{student.id}_{student.email}'
        token = QR_SIGNER.dumps(qr_data)
        expires = int(os.environ.get('QR_TOKEN_MAX_AGE', '300'))
        return jsonify({'success': True, 'qr_token': token, 'expires_in': expires}), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/student/<int:student_id>/qr-token-image', methods=['GET'])
@login_required
def get_qr_token_image(student_id):
    """Return a PNG image of a QR code that encodes the signed token."""
    try:
        student = db.session.get(Student, student_id)
        if not student:
            return jsonify({'success': False, 'error': 'Student not found'}), 404

        # Reuse same authorization as token endpoint
        allowed = False
        if is_teacher(current_user):
            if current_user.email == 'admin@teacher' or current_user.id == student.teacher_id:
                allowed = True
            else:
                allowed = current_user.email == 'admin@teacher'
        if not allowed:
            if session.get('logged_in') and session.get('user_type') == 'student' and session.get('student_id') == student_id:
                allowed = True
        if not allowed:
            return jsonify({'success': False, 'error': 'Unauthorized'}), 401

        qr_data = f'STUDENT_{student.id}_{student.email}'
        token = QR_SIGNER.dumps(qr_data)

        # Make a QR image that contains the token string
        qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_L, box_size=10, border=4)
        qr.add_data(token)
        qr.make(fit=True)
        img = qr.make_image(fill_color='black', back_color='white')
        buffer = BytesIO()
        img.save(buffer, format='PNG')
        buffer.seek(0)

        return send_file(buffer, mimetype='image/png', as_attachment=False, download_name=f'qr_token_{student.id}.png')
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

SCANNER_SECRET = os.environ.get('SCANNER_SECRET', 'dev-scanner')

@app.route('/api/attendance/scan', methods=['POST'])
def scan_attendance():
    """Scan QR code and record attendance in teacher's database with status tracking"""
    try:
        data = request.get_json() if request.is_json else request.form
        # Accept either a signed token (`qr_token`) or legacy/plain `qr_data`
        qr_token = data.get('qr_token')
        qr_data = None
        if qr_token:
            try:
                max_age = int(os.environ.get('QR_TOKEN_MAX_AGE', '300'))
                qr_data = QR_SIGNER.loads(qr_token, max_age=max_age)
            except SignatureExpired:
                return jsonify({'success': False, 'error': 'QR token expired'}), 400
            except BadSignature:
                return jsonify({'success': False, 'error': 'Invalid QR token'}), 400
        else:
            qr_data = data.get('qr_data')

        if not qr_data:
            return jsonify({'success': False, 'error': 'QR code data is required'}), 400

        # Parse QR data - supports two formats:
        # New format: STUDENT_{id}_{teacher_id}_{email}
        # Old format: STUDENT_{id}_{email} (for backwards compatibility)
        if not qr_data.startswith('STUDENT_'):
            return jsonify({'success': False, 'error': 'Invalid QR code format'}), 400

        parts = qr_data.split('_')
        
        if len(parts) >= 4 and parts[2].isdigit():
            # New format: STUDENT_{id}_{teacher_id}_{email}
            student_id = int(parts[1])
            teacher_id = int(parts[2])
            email = '_'.join(parts[3:])  # Email might have underscores
        elif len(parts) >= 3:
            # Old format: STUDENT_{id}_{email} - need to find teacher from logged in user
            student_id = int(parts[1])
            email = '_'.join(parts[2:])
            # Get teacher from current logged in user
            if current_user.is_authenticated and is_teacher(current_user):
                teacher_id = current_user.id
            else:
                return jsonify({'success': False, 'error': 'Cannot determine teacher for old QR format'}), 400
        else:
            return jsonify({'success': False, 'error': 'Invalid QR code format'}), 400
        
        # Get teacher and their database
        teacher = db.session.get(Teacher, teacher_id)
        if not teacher or not teacher.db_name:
            return jsonify({'success': False, 'error': 'Invalid teacher reference'}), 404

        # Authorization: allow if teacher is logged in OR scanner secret header matches
        authorized = False
        if current_user.is_authenticated and is_teacher(current_user):
            # Teachers can only scan their own students (or admin can scan any)
            if current_user.email == 'admin@teacher' or current_user.id == teacher_id:
                authorized = True
        else:
            header_secret = request.headers.get('X-Scanner-Secret')
            if header_secret and header_secret == SCANNER_SECRET:
                authorized = True
        
        if not authorized:
            return jsonify({'success': False, 'error': 'Unauthorized'}), 401
        
        # Get admin config for time settings
        config = AdminConfig.query.first()
        if not config:
            config = AdminConfig()
            db.session.add(config)
            db.session.commit()
        
        # Get student from teacher's database
        Session = get_teacher_db_session(teacher.db_name)
        sess = Session()
        
        try:
            student = sess.get(TeacherStudent, student_id)
            if not student:
                return jsonify({'success': False, 'error': 'Student not found'}), 404
            
            # Get current Philippine time
            now = get_philippine_time()
            today = now.date()
            today_str = today.strftime('%Y-%m-%d')
            current_time = now.time()

            # Determine shift for this scan
            shift = select_shift(config, current_time)
            
            from sqlalchemy import func
            
            # Get today's attendance record for this student and shift
            today_attendance = sess.query(TeacherAttendance).filter(
                TeacherAttendance.student_id == student_id,
                TeacherAttendance.date == today_str,
                TeacherAttendance.shift == shift
            ).first()
            
            # Parse configured times for the selected shift
            if shift == 'afternoon':
                check_in_end_time = datetime.strptime(config.afternoon_check_in_end_time, '%H:%M').time()
                check_out_end_time = datetime.strptime(config.afternoon_check_out_end_time, '%H:%M').time()
            else:
                check_in_end_time = datetime.strptime(config.check_in_end_time, '%H:%M').time()
                check_out_end_time = datetime.strptime(config.check_out_end_time, '%H:%M').time()
            
            # Determine what type of scan this is (check-in or check-out)
            if today_attendance is None:
                # First scan of the day - this is check-in
                new_status = 'check_in'
                
                # Determine attendance status based on time
                if current_time <= check_in_end_time:
                    attendance_status = 'PRESENT'
                else:
                    attendance_status = 'LATE'
                
                # Create new attendance record
                attendance = TeacherAttendance(
                    student_id=student_id, 
                    status=new_status,
                    attendance_status=attendance_status,
                    check_in_time=now,
                    date=today_str,
                    shift=shift
                )
                sess.add(attendance)
                sess.commit()
                
                # Send notification to guardian
                if student.guardian_email and student.notify_on_checkin:
                    send_attendance_notification(
                        guardian_email=student.guardian_email,
                        guardian_name=student.guardian_name or 'Parent/Guardian',
                        student_name=student.full_name,
                        status=attendance_status,
                        timestamp=now,
                        check_in_end_time=config.check_in_end_time,
                        check_out_end_time=config.check_out_end_time
                    )
                
                message = f'{attendance_status}: {student.full_name} checked in at {now.strftime("%I:%M %p")}'
                
            elif today_attendance.check_out_time is None:
                # Second scan - this is check-out
                new_status = 'check_out'
                today_attendance.check_out_time = now
                today_attendance.status = new_status
                
                # Keep the existing attendance status (PRESENT or LATE)
                attendance_status = today_attendance.attendance_status
                
                sess.commit()
                
                # Send checkout notification to guardian
                if student.guardian_email and student.notify_on_checkout:
                    send_attendance_notification(
                        guardian_email=student.guardian_email,
                        guardian_name=student.guardian_name or 'Parent/Guardian',
                        student_name=student.full_name,
                        status=f'{attendance_status} (Checked Out)',
                        timestamp=now,
                        check_in_end_time=config.check_in_end_time,
                        check_out_end_time=config.check_out_end_time
                    )
                
                message = f'{student.full_name} checked out at {now.strftime("%I:%M %p")}'
                
            else:
                # Already checked in and out today
                message = f'{student.full_name} has already completed attendance for today'
                attendance_status = today_attendance.attendance_status
                new_status = 'completed'
            
            return jsonify({
                'success': True,
                'message': message,
                'student_name': student.full_name,
                'status': new_status,
                'attendance_status': attendance_status,
                'timestamp': now.isoformat()
            }), 201
        finally:
            sess.close()
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/attendance/auto-mark', methods=['POST'])
def auto_mark_attendance():
    """
    Auto-mark students as ABSENT (if not checked in by deadline) or CUTTING (if not checked out)
    This should be called by a scheduled task or admin manually
    """
    try:
        # Authorization: only admin or system
        header_secret = request.headers.get('X-Scanner-Secret')
        is_admin = current_user.is_authenticated and is_teacher(current_user) and current_user.email == 'admin@teacher'
        
        if not is_admin and header_secret != SCANNER_SECRET:
            return jsonify({'success': False, 'error': 'Unauthorized'}), 401
        
        config = AdminConfig.query.first()
        if not config:
            return jsonify({'success': False, 'error': 'Admin config not found'}), 500
        
        now = get_philippine_time()
        today_str = now.date().strftime('%Y-%m-%d')
        current_time = now.time()
        
        # Prepare shift-specific configured times
        morning_check_in_end = datetime.strptime(config.check_in_end_time, '%H:%M').time()
        morning_check_out_end = datetime.strptime(config.check_out_end_time, '%H:%M').time()
        afternoon_check_in_end = datetime.strptime(config.afternoon_check_in_end_time, '%H:%M').time()
        afternoon_check_out_end = datetime.strptime(config.afternoon_check_out_end_time, '%H:%M').time()

        marked_absent = 0
        marked_cutting = 0

        # Get all teachers
        teachers = Teacher.query.filter(Teacher.db_name.isnot(None)).all()

        for teacher in teachers:
            Session = get_teacher_db_session(teacher.db_name)
            sess = Session()

            try:
                # Get all students for this teacher
                students = sess.query(TeacherStudent).all()

                for student in students:
                    # Check both shifts separately
                    for shift_name, cin_end, cout_end in (
                        ('morning', morning_check_in_end, morning_check_out_end),
                        ('afternoon', afternoon_check_in_end, afternoon_check_out_end)
                    ):
                        # Get today's attendance record for this student and shift
                        attendance = sess.query(TeacherAttendance).filter(
                            TeacherAttendance.student_id == student.id,
                            TeacherAttendance.date == today_str,
                            TeacherAttendance.shift == shift_name
                        ).first()

                        # Mark ABSENT if past check-in time and no attendance record for this shift
                        if config.auto_mark_absent_enabled and current_time > cin_end:
                            if attendance is None:
                                # Create absence record for this shift
                                attendance = TeacherAttendance(
                                    student_id=student.id,
                                    status='absent',
                                    attendance_status='ABSENT',
                                    date=today_str,
                                    shift=shift_name
                                )
                                sess.add(attendance)
                                sess.commit()
                                marked_absent += 1

                                # Send notification
                                if student.guardian_email:
                                    send_attendance_notification(
                                        guardian_email=student.guardian_email,
                                        guardian_name=student.guardian_name or 'Parent/Guardian',
                                        student_name=student.full_name,
                                        status='ABSENT',
                                        timestamp=now,
                                        check_in_end_time=cin_end.strftime('%H:%M'),
                                        check_out_end_time=cout_end.strftime('%H:%M')
                                    )

                        # Mark CUTTING if past checkout time and checked in but not out for this shift
                        if config.auto_mark_cutting_enabled and current_time > cout_end:
                            if attendance and attendance.check_in_time and not attendance.check_out_time:
                                # Do not overwrite teacher-set EXCUSED status; only set to CUTTING
                                if attendance.attendance_status not in ('CUTTING', 'EXCUSED'):
                                    attendance.attendance_status = 'CUTTING'
                                    sess.commit()
                                    marked_cutting += 1

                                    # Send notification
                                    if student.guardian_email:
                                        send_attendance_notification(
                                            guardian_email=student.guardian_email,
                                            guardian_name=student.guardian_name or 'Parent/Guardian',
                                            student_name=student.full_name,
                                            status='CUTTING',
                                            timestamp=now,
                                            check_in_end_time=cin_end.strftime('%H:%M'),
                                            check_out_end_time=cout_end.strftime('%H:%M')
                                        )
            finally:
                sess.close()
        
        return jsonify({
            'success': True,
            'message': f'Auto-marked {marked_absent} students as ABSENT and {marked_cutting} as CUTTING',
            'marked_absent': marked_absent,
            'marked_cutting': marked_cutting
        }), 200
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/student/<int:student_id>/status', methods=['GET'])
@login_required
def get_student_status(student_id):
    """Get a specific student's attendance status (used by teachers)"""
    try:
        if not is_teacher(current_user):
            return jsonify({'success': False, 'error': 'Access denied'}), 403
        
        # Get the teacher's database
        teacher = db.session.get(Teacher, current_user.id)
        if not teacher:
            return jsonify({'success': False, 'error': 'Teacher not found'}), 404
        
        # Use the stored db_name from teacher
        db_name = teacher.db_name
        if not db_name:
            return jsonify({'success': False, 'error': 'Teacher database not configured'}), 404
        
        Session = get_teacher_db_session(db_name)
        sess = Session()
        
        try:
            student = sess.get(TeacherStudent, student_id)
            if not student:
                return jsonify({'success': False, 'error': 'Student not found'}), 404
            
            # Get today's attendance records for the current shift
            now = get_philippine_time()
            today_str = now.date().strftime('%Y-%m-%d')
            config = AdminConfig.query.first()
            if not config:
                config = AdminConfig()
                db.session.add(config)
                db.session.commit()
            shift = select_shift(config, now.time())

            today_attendance = sess.query(TeacherAttendance).filter(
                TeacherAttendance.student_id == student_id,
                TeacherAttendance.date == today_str,
                TeacherAttendance.shift == shift
            ).order_by(TeacherAttendance.timestamp.desc()).all()
            
            # Determine current status
            current_status = 'checked_out'
            last_record = today_attendance[0] if today_attendance else None
            if last_record and last_record.status == 'check_in':
                current_status = 'checked_in'
            
            # Get attendance history
            attendance_history = [{
                'id': a.id,
                'timestamp': a.timestamp.isoformat(),
                'status': a.status,
                'attendance_status': getattr(a, 'attendance_status', None)
            } for a in today_attendance]
            
            return jsonify({
                'success': True,
                'current_status': current_status,
                'attendance_status': getattr(last_record, 'attendance_status', None) if last_record else None,
                'last_record': {
                    'timestamp': last_record.timestamp.isoformat() if last_record else None,
                    'status': last_record.status if last_record else None,
                    'attendance_status': getattr(last_record, 'attendance_status', None) if last_record else None
                },
                'today_attendance': attendance_history
            }), 200
        finally:
            sess.close()
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/student/current', methods=['GET'])
def get_current_student():
    """Get current logged-in student information from session"""
    try:
        # Check if student is logged in via session
        if not session.get('logged_in') or session.get('user_type') != 'student':
            return jsonify({'success': False, 'error': 'Not logged in as student'}), 403
        
        db_name = session.get('student_db')
        student_id = session.get('student_id')
        
        if not db_name or not student_id:
            return jsonify({'success': False, 'error': 'Session invalid'}), 403
        
        Session = get_teacher_db_session(db_name)
        sess = Session()
        try:
            student = sess.get(TeacherStudent, student_id)
            if not student:
                return jsonify({'success': False, 'error': 'Student not found'}), 404
            
            return jsonify({
                'success': True,
                'student_id': student.id,
                'student': {
                    'id': student.id,
                    'full_name': student.full_name,
                    'email': student.email,
                    'section': student.section,
                    'grade_level': student.grade_level,
                    'teacher_id': student.teacher_id,
                    'created_at': student.created_at.isoformat() if student.created_at else None
                }
            }), 200
        finally:
            sess.close()
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/student/status', methods=['GET'])
def get_current_student_status():
    """Get current student's attendance status"""
    try:
        if not session.get('logged_in') or session.get('user_type') != 'student':
            return jsonify({'success': False, 'error': 'Not logged in as student'}), 403
        
        db_name = session.get('student_db')
        student_id = session.get('student_id')
        
        Session = get_teacher_db_session(db_name)
        sess = Session()
        try:
            student = sess.get(TeacherStudent, student_id)
            if not student:
                return jsonify({'success': False, 'error': 'Student not found'}), 404
            
            # Get today's attendance records for the current shift
            now = get_philippine_time()
            today_str = now.date().strftime('%Y-%m-%d')
            config = AdminConfig.query.first()
            if not config:
                config = AdminConfig()
                db.session.add(config)
                db.session.commit()
            shift = select_shift(config, now.time())

            today_attendance = sess.query(TeacherAttendance).filter(
                TeacherAttendance.student_id == student_id,
                TeacherAttendance.date == today_str,
                TeacherAttendance.shift == shift
            ).order_by(TeacherAttendance.timestamp.desc()).all()
            
            # Determine current status. If teacher has manually set an
            # `attendance_status` for today, prefer that value so student
            # panel reflects teacher edits (PRESENT/ABSENT/LATE/CUTTING/EXCUSED).
            last_record = today_attendance[0] if today_attendance else None

            # Default checked state (checked_in / checked_out) derived from last record
            checked_state = 'checked_out'
            if last_record and last_record.status == 'check_in':
                checked_state = 'checked_in'

            # Prefer teacher-set attendance_status when present
            attendance_status = None
            if last_record and getattr(last_record, 'attendance_status', None):
                attendance_status = last_record.attendance_status
            else:
                # If no explicit attendance_status was recorded, derive from check-in/out
                attendance_status = 'PRESENT' if checked_state == 'checked_in' else 'ABSENT'

            attendance_history = [{
                'id': a.id,
                'timestamp': a.timestamp.isoformat(),
                'status': a.status,
                'attendance_status': getattr(a, 'attendance_status', None)
            } for a in today_attendance]

            return jsonify({
                'success': True,
                'checked_state': checked_state,
                'attendance_status': attendance_status,
                'last_record': {
                    'timestamp': last_record.timestamp.isoformat() if last_record else None,
                    'status': last_record.status if last_record else None,
                    'attendance_status': getattr(last_record, 'attendance_status', None) if last_record else None
                },
                'today_attendance': attendance_history
            }), 200
        finally:
            sess.close()
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/student/qr-image', methods=['GET'])
def get_current_student_qr():
    """Get current student's QR code as base64 image"""
    try:
        if not session.get('logged_in') or session.get('user_type') != 'student':
            return jsonify({'success': False, 'error': 'Not logged in as student'}), 403
        
        db_name = session.get('student_db')
        student_id = session.get('student_id')
        
        Session = get_teacher_db_session(db_name)
        sess = Session()
        try:
            student = sess.get(TeacherStudent, student_id)
            if not student:
                return jsonify({'success': False, 'error': 'Student not found'}), 404
            
            if not student.qr_code:
                student.generate_qr_code()
                sess.commit()
            
            import base64
            qr_base64 = base64.b64encode(student.qr_code).decode('utf-8')
            
            return jsonify({
                'success': True,
                'qr_image': f'data:image/png;base64,{qr_base64}'
            }), 200
        finally:
            sess.close()
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/student/qr-code', methods=['GET'])
def download_current_student_qr():
    """Download current student's QR code as PNG file"""
    try:
        if not session.get('logged_in') or session.get('user_type') != 'student':
            return jsonify({'success': False, 'error': 'Not logged in as student'}), 403
        
        db_name = session.get('student_db')
        student_id = session.get('student_id')
        
        Session = get_teacher_db_session(db_name)
        sess = Session()
        try:
            student = sess.get(TeacherStudent, student_id)
            if not student:
                return jsonify({'success': False, 'error': 'Student not found'}), 404
            
            if not student.qr_code:
                student.generate_qr_code()
                sess.commit()
            
            from flask import send_file
            import io
            return send_file(
                io.BytesIO(student.qr_code),
                mimetype='image/png',
                as_attachment=True,
                download_name=f'qr_code_{student.email}.png'
            )
        finally:
            sess.close()
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/launch-scanner', methods=['POST'])
@login_required
def launch_scanner():
    try:
        # Check if user is a teacher
        if not is_teacher(current_user):
            return jsonify({
                'success': False,
                'error': 'Only teachers can launch the scanner'
            }), 403

        # Launch the scanner as a separate process so OpenCV windows
        # are created on the process main thread and reliably appear.
        try:
            import subprocess
            import sys
            scanner_path = os.path.join(os.path.dirname(__file__), 'testscanner.py')
            # On Windows, prefer pythonw (no console). If not available, use CREATE_NO_WINDOW
            creationflags = 0
            exec_to_use = sys.executable
            if os.name == 'nt':
                pythonw = os.path.join(os.path.dirname(sys.executable), 'pythonw.exe')
                try:
                    if os.path.exists(pythonw):
                        exec_to_use = pythonw
                    else:
                        creationflags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
                except Exception:
                    creationflags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)

            subprocess.Popen([exec_to_use, scanner_path], cwd=os.path.dirname(__file__), creationflags=creationflags)
        except Exception:
            # Fall back to running in a background thread if subprocess launch fails
            def run_scanner():
                try:
                    import testscanner
                    testscanner.scan_qr_webcam()
                except Exception as e:
                    print(f"Scanner error: {e}")
                    import traceback
                    traceback.print_exc()

            threading.Thread(target=run_scanner, daemon=True).start()

        return jsonify({
            'success': True,
            'message': 'Desktop scanner launched successfully!'
        }), 200

    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"Error launching scanner: {error_details}")
        return jsonify({
            'success': False,
            'error': f'Failed to launch scanner: {str(e)}'
        }), 500

if __name__ == '__main__':
    # In production (Railway) Gunicorn will be used via Procfile; this block
    # supports local development. Respect environment variables for port
    # and debug mode. Default to debug=False to avoid accidental debug
    # servers in production.
    debug = os.environ.get('FLASK_DEBUG', 'False').lower() in ('1', 'true', 'yes')
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=debug)
sonify({'success': False, 'error': str(e)}), 500


@app.route('/api/launch-scanner', methods=['POST'])
@login_required
def launch_scanner():
    try:
        # Check if user is a teacher
        if not is_teacher(current_user):
            return jsonify({
                'success': False,
                'error': 'Only teachers can launch the scanner'
            }), 403

        # Launch the scanner as a separate process so OpenCV windows
        # are created on the process main thread and reliably appear.
        try:
            import subprocess
            import sys
            scanner_path = os.path.join(os.path.dirname(__file__), 'testscanner.py')
            # On Windows, prefer pythonw (no console). If not available, use CREATE_NO_WINDOW
            creationflags = 0
            exec_to_use = sys.executable
            if os.name == 'nt':
                pythonw = os.path.join(os.path.dirname(sys.executable), 'pythonw.exe')
                try:
                    if os.path.exists(pythonw):
                        exec_to_use = pythonw
                    else:
                        creationflags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
                except Exception:
                    creationflags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)

            subprocess.Popen([exec_to_use, scanner_path], cwd=os.path.dirname(__file__), creationflags=creationflags)
        except Exception:
            # Fall back to running in a background thread if subprocess launch fails
            def run_scanner():
                try:
                    import testscanner
                    testscanner.scan_qr_webcam()
                except Exception as e:
                    print(f"Scanner error: {e}")
                    import traceback
                    traceback.print_exc()

            threading.Thread(target=run_scanner, daemon=True).start()

        return jsonify({
            'success': True,
            'message': 'Desktop scanner launched successfully!'
        }), 200

    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"Error launching scanner: {error_details}")
        return jsonify({
            'success': False,
            'error': f'Failed to launch scanner: {str(e)}'
        }), 500

if __name__ == '__main__':
    # In production (Railway) Gunicorn will be used via Procfile; this block
    # supports local development. Respect environment variables for port
    # and debug mode. Default to debug=False to avoid accidental debug
    # servers in production.
    debug = os.environ.get('FLASK_DEBUG', 'False').lower() in ('1', 'true', 'yes')
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=debug)
    