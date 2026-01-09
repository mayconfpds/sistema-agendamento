import os
import re
import threading
import time as time_module
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, time, timedelta

app = Flask(__name__)
app.config['SECRET_KEY'] = 'chave-secreta-v6'
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'agendamento.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

class Establishment(db.Model):
    __tablename__ = 'establishments'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    url_prefix = db.Column(db.String(50), nullable=False, unique=True)
    contact_phone = db.Column(db.String(20), nullable=True)
    
    # Horários Semana (Seg-Sex)
    work_start = db.Column(db.Time, nullable=False, default=time(9, 0))
    work_end = db.Column(db.Time, nullable=False, default=time(18, 0))
    
    # Horários Fim de Semana (Sáb-Dom) - NOVO
    weekend_start = db.Column(db.Time, nullable=False, default=time(9, 0))
    weekend_end = db.Column(db.Time, nullable=False, default=time(13, 0)) # Geralmente fecha mais cedo

    lunch_start = db.Column(db.Time, nullable=True)
    lunch_end = db.Column(db.Time, nullable=True)
    work_days = db.Column(db.String(20), default="0,1,2,3,4,5") # Inclui Sábado por padrão
    
    admins = db.relationship('Admin', backref='establishment', lazy=True)
    services = db.relationship('Service', backref='establishment', lazy=True)
    appointments = db.relationship('Appointment', backref='establishment', lazy=True)

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
    price = db.Column(db.Float, nullable=False, default=0.0) # NOVO: PREÇO
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
                        print(f"\n[NOTIFICAÇÃO] Cliente: {appt.client_name} - Horário: {appt.appointment_time}")
                        appt.notified = True
                        db.session.commit()
        except: pass
        time_module.sleep(60)

# --- ROTAS ---
@app.route('/')
def index(): return render_template('index.html')

@app.route('/cadastro-negocio', methods=['GET', 'POST'])
def register_business():
    if current_user.is_authenticated: return redirect(url_for('admin_dashboard'))
    if request.method == 'POST':
        # Criação simplificada
        est = Establishment(
            name=request.form.get('business_name'),
            url_prefix=request.form.get('url_prefix').lower().strip(),
            contact_phone=request.form.get('contact_phone')
        )
        db.session.add(est)
        db.session.commit()
        
        adm = Admin(username=request.form.get('username'), establishment_id=est.id)
        adm.set_password(request.form.get('password'))
        db.session.add(adm)
        db.session.commit()
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
    date_obj = datetime.strptime(request.form.get('appointment_date'), '%Y-%m-%d').date()
    time_obj = datetime.strptime(request.form.get('appointment_time'), '%H:%M').time()
    
    appt = Appointment(
        client_name=request.form.get('client_name'),
        client_phone=request.form.get('client_phone'),
        service_id=request.form.get('service_id'),
        appointment_date=date_obj,
        appointment_time=time_obj,
        establishment_id=est.id
    )
    db.session.add(appt)
    db.session.commit()
    return redirect(url_for('establishment_services', url_prefix=url_prefix))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        adm = Admin.query.filter_by(username=request.form.get('username')).first()
        if adm and adm.check_password(request.form.get('password')):
            login_user(adm)
            return redirect(url_for('admin_dashboard'))
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
    active_days = est.work_days.split(',') if est.work_days else []
    return render_template('admin.html', appointments=appts, services=services, establishment=est, active_days=active_days)

@app.route('/admin/configurar', methods=['POST'])
@login_required
def update_settings():
    est = current_user.establishment
    # Horários Semana
    est.work_start = datetime.strptime(request.form.get('work_start'), '%H:%M').time()
    est.work_end = datetime.strptime(request.form.get('work_end'), '%H:%M').time()
    # Horários Fim de Semana
    est.weekend_start = datetime.strptime(request.form.get('weekend_start'), '%H:%M').time()
    est.weekend_end = datetime.strptime(request.form.get('weekend_end'), '%H:%M').time()
    
    est.contact_phone = request.form.get('contact_phone')
    
    l_start = request.form.get('lunch_start')
    l_end = request.form.get('lunch_end')
    if l_start and l_end:
        est.lunch_start = datetime.strptime(l_start, '%H:%M').time()
        est.lunch_end = datetime.strptime(l_end, '%H:%M').time()
    else: est.lunch_start = None; est.lunch_end = None

    est.work_days = ",".join(request.form.getlist('work_days'))
    db.session.commit()
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/servicos/novo', methods=['POST'])
@login_required
def add_service():
    # Agora com PREÇO
    price = request.form.get('price').replace(',', '.') # Trata R$ 50,00
    svc = Service(
        name=request.form.get('name'), 
        duration=int(request.form.get('duration')), 
        price=float(price),
        establishment_id=current_user.establishment_id
    )
    db.session.add(svc)
    db.session.commit()
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

# API Lógica de Fim de Semana
@app.route('/api/horarios_disponiveis')
def get_available_times():
    svc_id = request.args.get('service_id')
    date_str = request.args.get('date')
    if not svc_id or not date_str: return jsonify([])
    
    sel_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    svc = Service.query.get(svc_id)
    est = svc.establishment
    
    weekday = sel_date.weekday() # 0=Seg ... 5=Sáb, 6=Dom
    if str(weekday) not in (est.work_days or ""): return jsonify([])

    # Define horário de abertura/fechamento baseado no dia
    if weekday >= 5: # Fim de Semana
        start_t = est.weekend_start
        end_t = est.weekend_end
    else: # Semana
        start_t = est.work_start
        end_t = est.work_end

    appts = Appointment.query.filter_by(appointment_date=sel_date, establishment_id=est.id).all()
    busy = []
    if est.lunch_start and est.lunch_end:
        busy.append((datetime.combine(sel_date, est.lunch_start), datetime.combine(sel_date, est.lunch_end)))
    
    for a in appts:
        d = a.service_info.duration
        s = datetime.combine(sel_date, a.appointment_time)
        busy.append((s, s + timedelta(minutes=d)))

    avail = []
    curr = datetime.combine(sel_date, start_t)
    limit = datetime.combine(sel_date, end_t)
    step = timedelta(minutes=15)
    now = datetime.now()

    while curr + timedelta(minutes=svc.duration) <= limit:
        end_slot = curr + timedelta(minutes=svc.duration)
        if sel_date == now.date() and curr < now: 
            curr += step; continue
        
        collision = False
        for b_s, b_e in busy:
            if max(curr, b_s) < min(end_slot, b_e): collision = True; break
        
        if not collision: avail.append(curr.strftime('%H:%M'))
        curr += step
        
    return jsonify(avail)

if __name__ == '__main__':
    with app.app_context(): db.create_all()
    if not app.debug or os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
        threading.Thread(target=notification_worker, daemon=True).start()
    app.run(debug=True)