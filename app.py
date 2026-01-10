import os
import re
import threading
import time as time_module
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime, time, timedelta

app = Flask(__name__)
app.config['SECRET_KEY'] = 'chave-secreta-v10-marketing'
basedir = os.path.abspath(os.path.dirname(__file__))

database_url = os.environ.get('DATABASE_URL')
if database_url and database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = database_url or 'sqlite:///' + os.path.join(basedir, 'agendamento.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

UPLOAD_FOLDER = os.path.join(basedir, 'static', 'uploads')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

if not os.path.exists(UPLOAD_FOLDER):
    try: os.makedirs(UPLOAD_FOLDER)
    except OSError: pass

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Faça login para continuar.'

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# MODELOS
class Establishment(db.Model):
    __tablename__ = 'establishments'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    url_prefix = db.Column(db.String(50), nullable=False, unique=True)
    contact_phone = db.Column(db.String(20), nullable=True)
    logo_filename = db.Column(db.String(100), nullable=True)
    schedules = db.relationship('DaySchedule', backref='establishment', lazy=True, cascade="all, delete-orphan")
    admins = db.relationship('Admin', backref='establishment', lazy=True)
    services = db.relationship('Service', backref='establishment', lazy=True)
    appointments = db.relationship('Appointment', backref='establishment', lazy=True)

class DaySchedule(db.Model):
    __tablename__ = 'day_schedules'
    id = db.Column(db.Integer, primary_key=True)
    establishment_id = db.Column(db.Integer, db.ForeignKey('establishments.id'), nullable=False)
    day_index = db.Column(db.Integer, nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    work_start = db.Column(db.Time, nullable=False, default=time(9, 0))
    work_end = db.Column(db.Time, nullable=False, default=time(18, 0))
    lunch_start = db.Column(db.Time, nullable=True)
    lunch_end = db.Column(db.Time, nullable=True)

class Admin(UserMixin, db.Model):
    __tablename__ = 'admins'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    establishment_id = db.Column(db.Integer, db.ForeignKey('establishments.id'), nullable=False)
    def set_password(self, password): self.password_hash = generate_password_hash(password)
    def check_password(self, password): return check_password_hash(self.password_hash, password)

class Service(db.Model):
    __tablename__ = 'services'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    duration = db.Column(db.Integer, nullable=False)
    price = db.Column(db.Float, nullable=False, default=0.0)
    establishment_id = db.Column(db.Integer, db.ForeignKey('establishments.id'), nullable=False)
    appointments = db.relationship('Appointment', backref='service_info', lazy=True, cascade="all, delete-orphan")

class Appointment(db.Model):
    __tablename__ = 'appointments'
    id = db.Column(db.Integer, primary_key=True)
    client_name = db.Column(db.String(150), nullable=False)
    client_phone = db.Column(db.String(20), nullable=False)
    appointment_date = db.Column(db.Date, nullable=False)
    appointment_time = db.Column(db.Time, nullable=False)
    notified = db.Column(db.Boolean, default=False)
    service_id = db.Column(db.Integer, db.ForeignKey('services.id'), nullable=False)
    establishment_id = db.Column(db.Integer, db.ForeignKey('establishments.id'), nullable=False)

@login_manager.user_loader
def load_user(user_id): return Admin.query.get(int(user_id))

def notification_worker():
    while True:
        try:
            with app.app_context():
                db.engine.connect()
                now = datetime.now()
                upcoming = Appointment.query.filter(Appointment.notified == False, Appointment.appointment_date == now.date()).all()
                for appt in upcoming:
                    appt_dt = datetime.combine(appt.appointment_date, appt.appointment_time)
                    if timedelta(minutes=55) <= (appt_dt - now) <= timedelta(minutes=65):
                        print(f"\n🔔 [NOTIFICAÇÃO] Cliente: {appt.client_name} - {appt.appointment_time}")
                        appt.notified = True
                        db.session.commit()
        except: pass
        time_module.sleep(60)

@app.route('/')
def index(): return render_template('index.html')

@app.route('/cadastro-negocio', methods=['GET', 'POST'])
def register_business():
    if current_user.is_authenticated: return redirect(url_for('admin_dashboard'))
    if request.method == 'POST':
        est = Establishment(
            name=request.form.get('business_name'),
            url_prefix=request.form.get('url_prefix').lower().strip(),
            contact_phone=request.form.get('contact_phone')
        )
        db.session.add(est)
        db.session.commit()
        for i in range(7):
            day_schedule = DaySchedule(establishment_id=est.id, day_index=i, is_active=(i < 5), work_start=time(9,0), work_end=time(18,0))
            db.session.add(day_schedule)
        adm = Admin(username=request.form.get('username'), establishment_id=est.id)
        adm.set_password(request.form.get('password'))
        db.session.add(adm)
        db.session.commit()
        flash('Conta criada com sucesso!', 'success')
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/b/<url_prefix>')
def establishment_services(url_prefix):
    est = Establishment.query.filter_by(url_prefix=url_prefix).first_or_404()
    services = Service.query.filter_by(establishment_id=est.id).order_by(Service.name).all()
    return render_template('lista_servicos.html', services=services, establishment=est)

@app.route('/b/<url_prefix>/agendar/<int:service_id>')
def schedule_service(url_prefix, service_id):
    est = Establishment.query.filter_by(url_prefix=url_prefix).first_or_404()
    service = Service.query.get_or_404(service_id)
    return render_template('agendamento.html', service=service, establishment=est)

@app.route('/b/<url_prefix>/confirmar', methods=['POST'])
def create_appointment(url_prefix):
    est = Establishment.query.filter_by(url_prefix=url_prefix).first_or_404()
    d = datetime.strptime(request.form.get('appointment_date'), '%Y-%m-%d').date()
    t = datetime.strptime(request.form.get('appointment_time'), '%H:%M').time()
    if datetime.combine(d, t) < datetime.now():
        flash('Data inválida (passado).', 'danger')
        return redirect(url_for('schedule_service', url_prefix=url_prefix, service_id=request.form.get('service_id')))
    appt = Appointment(
        client_name=request.form.get('client_name'), client_phone=request.form.get('client_phone'),
        service_id=request.form.get('service_id'), appointment_date=d, appointment_time=t, establishment_id=est.id
    )
    db.session.add(appt)
    db.session.commit()
    flash('Agendado com sucesso!', 'success')
    return redirect(url_for('establishment_services', url_prefix=url_prefix))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        adm = Admin.query.filter_by(username=request.form.get('username')).first()
        if adm and adm.check_password(request.form.get('password')):
            login_user(adm)
            return redirect(url_for('admin_dashboard'))
        flash('Erro no login.', 'danger')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout(): logout_user(); return redirect(url_for('login'))

@app.route('/admin')
@login_required
def admin_dashboard():
    est = current_user.establishment
    today = datetime.now().date()
    appts = Appointment.query.filter(Appointment.establishment_id == est.id, Appointment.appointment_date >= today).order_by(Appointment.appointment_date, Appointment.appointment_time).all()
    services = Service.query.filter_by(establishment_id=est.id).all()
    schedules = DaySchedule.query.filter_by(establishment_id=est.id).order_by(DaySchedule.day_index).all()
    return render_template('admin.html', appointments=appts, services=services, establishment=est, schedules=schedules)

@app.route('/admin/configurar', methods=['POST'])
@login_required
def update_settings():
    est = current_user.establishment
    form_type = request.form.get('form_type')
    if form_type == 'contact':
        est.contact_phone = request.form.get('contact_phone')
        if 'logo' in request.files:
            file = request.files['logo']
            if file and allowed_file(file.filename):
                fname = secure_filename(file.filename)
                unique = f"{est.id}_{int(time_module.time())}_{fname}"
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], unique))
                est.logo_filename = unique
        flash('Dados atualizados!', 'success')
    elif form_type == 'schedule':
        sids = request.form.getlist('schedule_id')
        for sid in sids:
            ds = DaySchedule.query.get(sid)
            if ds and ds.establishment_id == est.id:
                ds.is_active = (request.form.get(f'active_{sid}') == 'on')
                ws, we = request.form.get(f'work_start_{sid}'), request.form.get(f'work_end_{sid}')
                ls, le = request.form.get(f'lunch_start_{sid}'), request.form.get(f'lunch_end_{sid}')
                if ws and we: ds.work_start = datetime.strptime(ws, '%H:%M').time(); ds.work_end = datetime.strptime(we, '%H:%M').time()
                if ls and le: ds.lunch_start = datetime.strptime(ls, '%H:%M').time(); ds.lunch_end = datetime.strptime(le, '%H:%M').time()
                else: ds.lunch_start = None; ds.lunch_end = None
        flash('Horários salvos!', 'success')
    db.session.commit()
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/servicos/novo', methods=['POST'])
@login_required
def add_service():
    try: price = float(request.form.get('price', '0').replace(',', '.'))
    except: price = 0.0
    svc = Service(name=request.form.get('name'), duration=int(request.form.get('duration')), price=price, establishment_id=current_user.establishment_id)
    db.session.add(svc); db.session.commit()
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/servicos/excluir/<int:id>', methods=['POST'])
@login_required
def delete_service(id):
    svc = Service.query.get(id)
    if svc.establishment_id == current_user.establishment_id: db.session.delete(svc); db.session.commit()
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/agendamentos/excluir/<int:id>', methods=['POST'])
@login_required
def delete_appointment(id):
    a = Appointment.query.get(id)
    if a.establishment_id == current_user.establishment_id: db.session.delete(a); db.session.commit()
    return redirect(url_for('admin_dashboard'))

@app.route('/api/horarios_disponiveis')
def get_available_times():
    sid, d_str = request.args.get('service_id'), request.args.get('date')
    if not sid or not d_str: return jsonify([])
    try: sel_date = datetime.strptime(d_str, '%Y-%m-%d').date()
    except: return jsonify([])
    svc = Service.query.get(sid)
    est = svc.establishment
    day_sched = DaySchedule.query.filter_by(establishment_id=est.id, day_index=sel_date.weekday()).first()
    if not day_sched or not day_sched.is_active: return jsonify([])
    
    appts = Appointment.query.filter_by(appointment_date=sel_date, establishment_id=est.id).all()
    busy = []
    if day_sched.lunch_start and day_sched.lunch_end:
        busy.append((datetime.combine(sel_date, day_sched.lunch_start), datetime.combine(sel_date, day_sched.lunch_end)))
    for a in appts:
        s = datetime.combine(sel_date, a.appointment_time)
        busy.append((s, s + timedelta(minutes=a.service_info.duration)))
        
    avail = []
    curr = datetime.combine(sel_date, day_sched.work_start)
    limit = datetime.combine(sel_date, day_sched.work_end)
    now = datetime.now()
    
    while curr + timedelta(minutes=svc.duration) <= limit:
        end = curr + timedelta(minutes=svc.duration)
        if sel_date == now.date() and curr < now: curr += timedelta(minutes=15); continue
        collision = False
        for bs, be in busy:
            if max(curr, bs) < min(end, be): collision = True; break
        if not collision: avail.append(curr.strftime('%H:%M'))
        curr += timedelta(minutes=15)
    return jsonify(avail)

if __name__ == '__main__':
    with app.app_context(): db.create_all()
    if not app.debug or os.environ.get('WERKZEUG_RUN_MAIN') == 'true': threading.Thread(target=notification_worker, daemon=True).start()
    app.run(debug=True)