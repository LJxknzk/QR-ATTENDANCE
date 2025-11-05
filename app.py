import os
from flask import Flask, render_template, request, jsonify, redirect, url_for, session, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask_bcrypt import Bcrypt
from sqlalchemy.orm import DeclarativeBase
from datetime import datetime
import qrcode
from io import BytesIO

class Base(DeclarativeBase):
    pass

db = SQLAlchemy(model_class=Base)
app = Flask(__name__, static_folder='.', static_url_path='')
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-key-change-in-production")
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", "sqlite:///attendance.db")
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_recycle": 300,
    "pool_pre_ping": True,
}
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)
bcrypt = Bcrypt(app)
login_manager = LoginManager(app)
login_manager.login_view = 'serve_index'
login_manager.session_protection = 'strong'

class Student(UserMixin, db.Model):
    __tablename__ = 'students'
    
    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(200), nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    qr_code = db.Column(db.LargeBinary)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
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
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def get_id(self):
        return f'teacher_{self.id}'
    
    def __repr__(self):
        return f'<Teacher {self.full_name}>'

class Attendance(db.Model):
    __tablename__ = 'attendance'
    
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('students.id'), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    
    def __repr__(self):
        return f'<Attendance {self.student_id} at {self.timestamp}>'

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

@login_manager.user_loader
def load_user(user_id):
    user_type, user_id = user_id.split('_')
    if user_type == 'student':
        return Student.query.get(int(user_id))
    elif user_type == 'teacher':
        return Teacher.query.get(int(user_id))
    return None

with app.app_context():
    db.create_all()

@app.route('/')
def serve_index():
    return send_file('index.html')

@app.route('/index.html')
def index():
    return send_file('index.html')

@app.route('/accountcreate.html')
def accountcreate():
    return send_file('accountcreate.html')

@app.route('/admin.html')
def admin():
    return send_file('admin.html')

@app.route('/api/signup', methods=['POST'])
def signup():
    try:
        data = request.get_json() if request.is_json else request.form
        full_name = data.get('full_name') or data.get('fullname')
        email = data.get('email')
        password = data.get('password')
        confirm_password = data.get('confirm_password') or data.get('cpwd')

        if not all([full_name, email, password, confirm_password]):
            return jsonify({'success': False, 'error': 'All fields are required'}), 400

        if password != confirm_password:
            return jsonify({'success': False, 'error': 'Passwords do not match'}), 400

        if Student.query.filter_by(email=email).first():
            return jsonify({'success': False, 'error': 'Email already registered'}), 400

        hashed_password = bcrypt.generate_password_hash(password).decode('utf-8')
        
        new_student = Student(
            full_name=full_name,
            email=email,
            password_hash=hashed_password
        )
        
        db.session.add(new_student)
        db.session.commit()
        
        new_student.generate_qr_code()
        db.session.commit()

        return jsonify({
            'success': True,
            'message': 'Account created successfully',
            'redirect': '/?signup=success'
        }), 201

    except Exception as e:
        db.session.rollback()
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

        if user_type == 'student':
            user = Student.query.filter_by(email=email).first()
        else:
            user = Teacher.query.filter_by(email=email).first()

        if user and bcrypt.check_password_hash(user.password_hash, password):
            login_user(user)
            
            if user_type == 'student':
                redirect_url = '/index.html'
            else:
                redirect_url = '/admin.html'
            
            return jsonify({
                'success': True,
                'message': 'Login successful',
                'redirect': redirect_url,
                'user_type': user_type
            }), 200
        else:
            return jsonify({'success': False, 'error': 'Invalid email or password'}), 401

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/logout', methods=['POST'])
@login_required
def logout():
    logout_user()
    return jsonify({'success': True, 'redirect': '/'}), 200

@app.route('/api/admin/create-teacher', methods=['POST'])
@teacher_required
def create_teacher():
    try:
        data = request.get_json() if request.is_json else request.form
        full_name = data.get('fullname') or data.get('full_name')
        email = data.get('gmail') or data.get('email')
        password = data.get('password')

        if not all([full_name, email, password]):
            return jsonify({'success': False, 'error': 'All fields are required'}), 400

        if Teacher.query.filter_by(email=email).first():
            return jsonify({'success': False, 'error': 'Email already registered'}), 400

        hashed_password = bcrypt.generate_password_hash(password).decode('utf-8')
        
        new_teacher = Teacher(
            full_name=full_name,
            email=email,
            password_hash=hashed_password
        )
        
        db.session.add(new_teacher)
        db.session.commit()

        return jsonify({
            'success': True,
            'message': 'Teacher account created successfully'
        }), 201

    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/students', methods=['GET'])
@teacher_required
def get_students():
    try:
        students = Student.query.all()
        students_data = [{
            'id': s.id,
            'name': s.full_name,
            'email': s.email,
            'created_at': s.created_at.isoformat() if s.created_at else None
        } for s in students]
        
        return jsonify({'success': True, 'students': students_data}), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/student/<int:student_id>', methods=['GET', 'PUT', 'DELETE'])
@teacher_required
def manage_student(student_id):
    try:
        student = Student.query.get_or_404(student_id)
        
        if request.method == 'GET':
            return jsonify({
                'success': True,
                'student': {
                    'id': student.id,
                    'name': student.full_name,
                    'email': student.email
                }
            }), 200
        
        elif request.method == 'PUT':
            data = request.get_json() if request.is_json else request.form
            student.full_name = data.get('name', student.full_name)
            student.email = data.get('email', student.email)
            
            db.session.commit()
            return jsonify({'success': True, 'message': 'Student updated successfully'}), 200
        
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
        student = Student.query.get_or_404(student_id)
        
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

@app.route('/api/attendance/scan', methods=['POST'])
@teacher_required
def scan_attendance():
    try:
        data = request.get_json() if request.is_json else request.form
        qr_data = data.get('qr_data')
        
        if not qr_data:
            return jsonify({'success': False, 'error': 'QR code data is required'}), 400
        
        student_id = int(qr_data.split('_')[1])
        student = Student.query.get(student_id)
        
        if not student:
            return jsonify({'success': False, 'error': 'Invalid QR code'}), 404
        
        attendance = Attendance(student_id=student_id)
        db.session.add(attendance)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': f'Attendance marked for {student.full_name}',
            'student_name': student.full_name,
            'timestamp': attendance.timestamp.isoformat()
        }), 201
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
