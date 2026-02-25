import os
import sys
import subprocess

REQUIREMENTS_TXT = r'''Flask
Flask-SQLAlchemy
Flask-Login
Werkzeug
gunicorn
stripe
requests
psycopg2-binary
Flask-Migrate
'''

PROCFILE = r'''web: flask db upgrade && gunicorn app:app'''

APP_PY = r'''import os
import threading
import time as time_module
import socket
import requests
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime, time, timedelta
from sqlalchemy import inspect
from flask_migrate import Migrate
import stripe

socket.setdefaulttimeout(15)

app = Flask(__name__)
app.config['SECRET_KEY'] = 'chave-v47-trial-br'
basedir = os.path.abspath(os.path.dirname(__file__))

database_url = os.environ.get('DATABASE_URL')
if database_url and database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = database_url or 'sqlite:///' + os.path.join(basedir, 'agendamento.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

raw_key = os.environ.get('BREVO_API_KEY', '')
BREVO_API_KEY = raw_key.strip() if raw_key else None
BREVO_SENDER_EMAIL = os.environ.get('BREVO_SENDER_EMAIL', 'seu_email@gmail.com') 
BREVO_SENDER_NAME = "Agenda Fácil"

stripe.api_key = os.environ.get('STRIPE_API_KEY')
STRIPE_PRICE_ID = os.environ.get('STRIPE_PRICE_ID')

UPLOAD_FOLDER = os.path.join(basedir, 'static', 'uploads')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
if not os.path.exists(UPLOAD_FOLDER):
    try: os.makedirs(UPLOAD_FOLDER)
    except OSError: pass

db = SQLAlchemy(app)
migrate = Migrate(app, db, render_as_batch=True)

login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Faça login para continuar.'

def get_now_brazil():
    return datetime.utcnow() - timedelta(hours=3)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def send_email(subject, recipient, body):
    if not BREVO_API_KEY:
        print(f"\n⚠️ [EMAIL VIRTUAL] Para: {recipient}")
        return
    def _send_thread():
        url = "https://api.brevo.com/v3/smtp/email"
        headers = {"accept": "application/json", "api-key": BREVO_API_KEY, "content-type": "application/json"}
        html_body = f"<html><body><p>{body.replace(chr(10), '<br>')}</p></body></html>"
        payload = {"sender": {"name": BREVO_SENDER_NAME, "email": BREVO_SENDER_EMAIL}, "to": [{"email": recipient}], "subject": subject, "htmlContent": html_body}
        try: requests.post(url, json=payload, headers=headers)
        except Exception as e: print(f"\n❌ [ERRO BREVO] {e}")
    threading.Thread(target=_send_thread).start()

# --- MODELOS ---
class Establishment(db.Model):
    __tablename__ = 'establishments'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    url_prefix = db.Column(db.String(50), nullable=False, unique=True)
    contact_phone = db.Column(db.String(20), nullable=True)
    contact_email = db.Column(db.String(120), nullable=True)
    logo_filename = db.Column(db.String(100), nullable=True)
    is_active = db.Column(db.Boolean, default=False)
    capacity = db.Column(db.Integer, default=1, nullable=False)
    
    trial_ends = db.Column(db.DateTime, nullable=True)
    
    schedules = db.relationship('DaySchedule', backref='establishment', lazy=True, cascade="all, delete-orphan")
    admins = db.relationship('Admin', backref='establishment', lazy=True)
    services = db.relationship('Service', backref='establishment', lazy=True)
    appointments = db.relationship('Appointment', backref='establishment', lazy=True)
    blacklists = db.relationship('Blacklist', backref='establishment', lazy=True)

    @property
    def has_access(self):
        if self.is_active: return True
        if self.trial_ends and get_now_brazil() < self.trial_ends: return True
        return False

    @property
    def trial_days_left(self):
        if self.is_active or not self.trial_ends: return 0
        delta = self.trial_ends - get_now_brazil()
        return max(0, delta.days)

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

appointment_services = db.Table('appointment_services',
    db.Column('appointment_id', db.Integer, db.ForeignKey('appointments.id'), primary_key=True),
    db.Column('service_id', db.Integer, db.ForeignKey('services.id'), primary_key=True)
)

class Appointment(db.Model):
    __tablename__ = 'appointments'
    id = db.Column(db.Integer, primary_key=True)
    client_name = db.Column(db.String(150), nullable=False)
    client_phone = db.Column(db.String(20), nullable=False)
    client_email = db.Column(db.String(120), nullable=False)
    appointment_date = db.Column(db.Date, nullable=False)
    appointment_time = db.Column(db.Time, nullable=False)
    notified = db.Column(db.Boolean, default=False)
    total_duration = db.Column(db.Integer, default=0, nullable=False)
    total_price = db.Column(db.Float, default=0.0, nullable=False)
    establishment_id = db.Column(db.Integer, db.ForeignKey('establishments.id'), nullable=False)
    services = db.relationship('Service', secondary=appointment_services, lazy='subquery', backref=db.backref('appointments', lazy=True))

    @property
    def service_names(self):
        return " + ".join([s.name for s in self.services])

class Blacklist(db.Model):
    __tablename__ = 'blacklists'
    id = db.Column(db.Integer, primary_key=True)
    client_phone = db.Column(db.String(20), nullable=False)
    establishment_id = db.Column(db.Integer, db.ForeignKey('establishments.id'), nullable=False)

@login_manager.user_loader
def load_user(user_id): return Admin.query.get(int(user_id))

# --- WORKER ---
def notification_worker():
    while True:
        try:
            with app.app_context():
                inspector = inspect(db.engine)
                if not inspector.has_table("appointments"): 
                    time_module.sleep(10); continue
                upcoming = Appointment.query.filter(Appointment.notified == False).all()
                now = get_now_brazil()
                for appt in upcoming:
                    appt_dt = datetime.combine(appt.appointment_date, appt.appointment_time)
                    minutes = (appt_dt - now).total_seconds() / 60
                    if 50 <= minutes <= 70:
                        subj = f"Lembrete: {appt.establishment.name}"
                        body = f"Olá {appt.client_name},\n\nLembrete do seu horário hoje às {appt.appointment_time.strftime('%H:%M')} para {appt.service_names}."
                        send_email(subj, appt.client_email, body)
                        appt.notified = True
                        db.session.commit()
        except: pass
        time_module.sleep(60)

try:
    with app.app_context():
        if not inspect(db.engine).has_table("establishments"): db.create_all()
except: pass 

if not os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
    threading.Thread(target=notification_worker, daemon=True).start()

# --- ROTAS DE PAGAMENTO ---
@app.route('/pagamento')
@login_required
def payment():
    if current_user.establishment.is_active: 
        return redirect(url_for('admin_dashboard'))
    
    try:
        # Corrige erro do Render passar http:// para a Stripe
        success_url = request.host_url.replace('http://', 'https://') + 'pagamento/sucesso'
        cancel_url = request.host_url.replace('http://', 'https://') + 'pagamento/cancelado'

        session = stripe.checkout.Session.create(
            payment_method_types=['card'], 
            line_items=[{'price': STRIPE_PRICE_ID, 'quantity': 1}],
            mode='subscription', 
            allow_promotion_codes=True, 
            success_url=success_url,
            cancel_url=cancel_url, 
            customer_email=current_user.establishment.contact_email,
        )
        return redirect(session.url, code=303)
    except Exception as e:
        # FIM DO BURACO NEGRO: Mostra o erro real na tela ao invés de jogar pro login
        return f"<div style='font-family:sans-serif; padding:40px; text-align:center;'> <h2 style='color:red;'>Erro na comunicação com a Stripe</h2> <p>Por favor, verifique se as chaves <b>STRIPE_API_KEY</b> e <b>STRIPE_PRICE_ID</b> estão corretas no Render.</p> <p style='background:#f4f4f4; padding:15px; border-radius:8px;'>Detalhe do Erro: <b>{str(e)}</b></p> <a href='/logout'>Sair</a> </div>", 500

@app.route('/pagamento/sucesso')
@login_required
def payment_success():
    current_user.establishment.is_active = True; db.session.commit()
    return redirect(url_for('admin_dashboard'))

@app.route('/pagamento/cancelado')
@login_required
def payment_cancel(): return redirect(url_for('logout'))

# --- ROTAS PRINCIPAIS ---
@app.route('/')
def index(): return render_template('index.html')

@app.route('/cadastro-negocio', methods=['GET', 'POST'])
def register_business():
    if current_user.is_authenticated: return redirect(url_for('admin_dashboard'))
    if request.method == 'POST':
        username = request.form.get('username')
        is_master = (username == 'admin_demo') 
        
        est = Establishment(
            name=request.form.get('business_name'), 
            url_prefix=request.form.get('url_prefix').lower().strip(), 
            contact_phone=request.form.get('contact_phone'), 
            contact_email=request.form.get('contact_email'), 
            is_active=is_master, 
            capacity=1,
            trial_ends=get_now_brazil() + timedelta(days=7) # +7 DIAS DE TESTE
        )
        db.session.add(est); db.session.commit()
        for i in range(7): db.session.add(DaySchedule(establishment_id=est.id, day_index=i, is_active=(i < 5), work_start=time(9,0), work_end=time(18,0)))
        adm = Admin(username=username, establishment_id=est.id)
        adm.set_password(request.form.get('password'))
        db.session.add(adm); db.session.commit()
        
        login_user(adm)
        # Login vai direto para o painel para iniciar os 7 dias
        return redirect(url_for('admin_dashboard'))
        
    return render_template('register.html')

@app.route('/b/<url_prefix>')
def establishment_services(url_prefix):
    est = Establishment.query.filter_by(url_prefix=url_prefix).first_or_404()
    if not est.has_access: return render_template('error_inactive.html', message="O período de teste ou assinatura deste estabelecimento expirou."), 403
    services = Service.query.filter_by(establishment_id=est.id).order_by(Service.name).all()
    return render_template('lista_servicos.html', services=services, establishment=est)

@app.route('/b/<url_prefix>/agendar/<int:service_id>')
def schedule_service(url_prefix, service_id):
    est = Establishment.query.filter_by(url_prefix=url_prefix).first_or_404()
    if not est.has_access: return "Inativo", 403
    main_service = Service.query.get_or_404(service_id)
    other_services = Service.query.filter(Service.establishment_id == est.id, Service.id != service_id).order_by(Service.name).all()
    return render_template('agendamento.html', main_service=main_service, other_services=other_services, establishment=est)

@app.route('/b/<url_prefix>/confirmar', methods=['POST'])
def create_appointment(url_prefix):
    est = Establishment.query.filter_by(url_prefix=url_prefix).first_or_404()
    if not est.has_access: return "Inativo", 403
    
    client_phone = request.form.get('client_phone').strip()
    
    if Blacklist.query.filter_by(establishment_id=est.id, client_phone=client_phone).first():
        flash('Agendamento bloqueado. Por favor, entre em contato com o estabelecimento.', 'danger')
        return redirect(url_for('establishment_services', url_prefix=url_prefix))

    now = get_now_brazil()
    user_appts = Appointment.query.filter_by(establishment_id=est.id, client_phone=client_phone).all()
    if any(datetime.combine(a.appointment_date, a.appointment_time) >= now for a in user_appts):
        flash('Você já possui um agendamento futuro conosco. Conclua-o antes de agendar novamente.', 'warning')
        return redirect(url_for('establishment_services', url_prefix=url_prefix))

    d = datetime.strptime(request.form.get('appointment_date'), '%Y-%m-%d').date()
    t = datetime.strptime(request.form.get('appointment_time'), '%H:%M').time()
    service_ids = request.form.getlist('services')
    
    selected_services = Service.query.filter(Service.id.in_(service_ids)).all()
    if not selected_services:
        flash('Nenhum serviço selecionado.', 'danger')
        return redirect(url_for('establishment_services', url_prefix=url_prefix))

    total_dur = sum(s.duration for s in selected_services)
    total_price = sum(s.price for s in selected_services)

    start_dt = datetime.combine(d, t)
    end_dt = start_dt + timedelta(minutes=total_dur)
    
    if start_dt < now:
        flash('Horário inválido.', 'danger'); return redirect(url_for('establishment_services', url_prefix=url_prefix))
        
    appts_on_day = Appointment.query.filter_by(appointment_date=d, establishment_id=est.id).all()
    overlap_count = 0
    for a in appts_on_day:
        s = datetime.combine(d, a.appointment_time)
        e = s + timedelta(minutes=a.total_duration)
        if max(start_dt, s) < min(end_dt, e):
            overlap_count += 1
            
    if overlap_count >= est.capacity:
        flash('Ops! Esse horário acabou de ser ocupado. Tente outro.', 'danger')
        return redirect(url_for('schedule_service', url_prefix=url_prefix, service_id=selected_services[0].id))

    appt = Appointment(client_name=request.form.get('client_name'), client_phone=client_phone, client_email=request.form.get('client_email'), appointment_date=d, appointment_time=t, establishment_id=est.id, total_duration=total_dur, total_price=total_price)
    for s in selected_services: appt.services.append(s)
    
    db.session.add(appt); db.session.commit()
    
    send_email(f"Confirmado: {est.name}", appt.client_email, f"Agendado para {d.strftime('%d/%m')} às {t.strftime('%H:%M')}.")
    if est.contact_email: send_email(f"Novo Cliente", est.contact_email, f"Novo agendamento recebido.")
    
    zap_msg = f"Olá, confirmo meu agendamento para: {d.strftime('%d/%m')} às {t.strftime('%H:%M')}."
    zap_link = f"https://wa.me/55{est.contact_phone}?text={zap_msg}" if est.contact_phone else "#"
    return render_template('success_appointment.html', appointment=appt, zap_link=zap_link)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        adm = Admin.query.filter_by(username=request.form.get('username')).first()
        if adm and adm.check_password(request.form.get('password')):
            login_user(adm)
            if not adm.establishment.has_access: return redirect(url_for('payment'))
            return redirect(url_for('admin_dashboard'))
        flash('Login inválido.', 'danger')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout(): logout_user(); return redirect(url_for('login'))

@app.route('/admin')
@login_required
def admin_dashboard():
    # HARD TRIAL (Paywall): Se acabou o teste e nao tem plano, força o checkout
    if not current_user.establishment.has_access: return redirect(url_for('payment'))
    est = current_user.establishment
    today = get_now_brazil().date()
    appts = Appointment.query.filter(Appointment.establishment_id == est.id, Appointment.appointment_date >= today).order_by(Appointment.appointment_date, Appointment.appointment_time).all()
    services = Service.query.filter_by(establishment_id=est.id).all()
    schedules = DaySchedule.query.filter_by(establishment_id=est.id).order_by(DaySchedule.day_index).all()
    blacklists = Blacklist.query.filter_by(establishment_id=est.id).all()
    today_count = Appointment.query.filter(Appointment.establishment_id == est.id, Appointment.appointment_date == today).count()
    return render_template('admin.html', appointments=appts, services=services, establishment=est, schedules=schedules, blacklists=blacklists, today_count=today_count)

@app.route('/admin/configurar', methods=['POST'])
@login_required
def update_settings():
    if not current_user.establishment.has_access: return redirect(url_for('payment'))
    est = current_user.establishment
    ft = request.form.get('form_type')
    if ft == 'contact':
        est.contact_phone = request.form.get('contact_phone')
        est.contact_email = request.form.get('contact_email')
        try: est.capacity = int(request.form.get('capacity', 1))
        except: pass
        if 'logo' in request.files:
            file = request.files['logo']
            if file and allowed_file(file.filename):
                fname = secure_filename(file.filename)
                uid = f"{est.id}_{int(time_module.time())}_{fname}"
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], uid))
                est.logo_filename = uid
        flash('Salvo com sucesso!', 'success')
    elif ft == 'schedule':
        for sid in request.form.getlist('schedule_id'):
            ds = DaySchedule.query.get(sid)
            if ds and ds.establishment_id == est.id:
                ds.is_active = (request.form.get(f'active_{sid}') == 'on')
                ws, we = request.form.get(f'work_start_{sid}'), request.form.get(f'work_end_{sid}')
                ls, le = request.form.get(f'lunch_start_{sid}'), request.form.get(f'lunch_end_{sid}')
                if ws and we: ds.work_start = datetime.strptime(ws, '%H:%M').time(); ds.work_end = datetime.strptime(we, '%H:%M').time()
                if ls and le: ds.lunch_start = datetime.strptime(ls, '%H:%M').time(); ds.lunch_end = datetime.strptime(le, '%H:%M').time()
                else: ds.lunch_start = None; ds.lunch_end = None
        flash('Horários atualizados!', 'success')
    db.session.commit()
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/servicos/novo', methods=['POST'])
@login_required
def add_service():
    if not current_user.establishment.has_access: return redirect(url_for('payment'))
    try: p = float(request.form.get('price', '0').replace(',', '.'))
    except: p = 0.0
    svc = Service(name=request.form.get('name'), duration=int(request.form.get('duration')), price=p, establishment_id=current_user.establishment_id)
    db.session.add(svc); db.session.commit()
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/servicos/excluir/<int:id>', methods=['POST'])
@login_required
def delete_service(id):
    s = Service.query.get(id); db.session.delete(s); db.session.commit()
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/agendamentos/excluir/<int:id>', methods=['POST'])
@login_required
def delete_appointment(id):
    a = Appointment.query.get(id); db.session.delete(a); db.session.commit()
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/agendamentos/falta/<int:id>', methods=['POST'])
@login_required
def mark_no_show(id):
    if not current_user.establishment.has_access: return redirect(url_for('payment'))
    a = Appointment.query.get_or_404(id)
    if a.establishment_id != current_user.establishment_id: return "Erro", 403
    if not Blacklist.query.filter_by(establishment_id=a.establishment_id, client_phone=a.client_phone).first():
        bl = Blacklist(establishment_id=a.establishment_id, client_phone=a.client_phone)
        db.session.add(bl)
    db.session.delete(a)
    db.session.commit()
    flash('Cliente marcado como Falta e bloqueado.', 'warning')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/blacklist/add', methods=['POST'])
@login_required
def add_blacklist():
    if not current_user.establishment.has_access: return redirect(url_for('payment'))
    phone = request.form.get('phone', '').strip()
    if phone:
        exists = Blacklist.query.filter_by(establishment_id=current_user.establishment_id, client_phone=phone).first()
        if not exists:
            db.session.add(Blacklist(client_phone=phone, establishment_id=current_user.establishment_id))
            db.session.commit()
            flash('Número bloqueado.', 'success')
        else:
            flash('Número já bloqueado.', 'warning')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/blacklist/remove/<int:id>', methods=['POST'])
@login_required
def remove_blacklist(id):
    b = Blacklist.query.get_or_404(id)
    if b.establishment_id == current_user.establishment_id:
        db.session.delete(b)
        db.session.commit()
        flash('Número desbloqueado.', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/api/horarios_disponiveis')
def get_available_times():
    est_id = request.args.get('est_id')
    d_str = request.args.get('date')
    dur_str = request.args.get('duration')
    if not est_id or not d_str or not dur_str: return jsonify([])
    try: 
        sel_date = datetime.strptime(d_str, '%Y-%m-%d').date()
        total_dur = int(dur_str)
    except: return jsonify([])
    
    est = Establishment.query.get(est_id)
    day_sched = DaySchedule.query.filter_by(establishment_id=est.id, day_index=sel_date.weekday()).first()
    if not day_sched or not day_sched.is_active: return jsonify([])
    
    appts = Appointment.query.filter_by(appointment_date=sel_date, establishment_id=est.id).all()
    
    avail = []
    curr = datetime.combine(sel_date, day_sched.work_start)
    limit = datetime.combine(sel_date, day_sched.work_end)
    now = get_now_brazil()
    
    while curr + timedelta(minutes=total_dur) <= limit:
        end = curr + timedelta(minutes=total_dur)
        if sel_date == now.date() and curr < now: 
            curr += timedelta(minutes=15); continue
            
        in_lunch = False
        if day_sched.lunch_start and day_sched.lunch_end:
            lunch_s = datetime.combine(sel_date, day_sched.lunch_start)
            lunch_e = datetime.combine(sel_date, day_sched.lunch_end)
            if (curr >= lunch_s and curr < lunch_e) or (end > lunch_s and end <= lunch_e) or (curr < lunch_s and end > lunch_e):
               in_lunch = True
        if in_lunch:
            curr += timedelta(minutes=15); continue

        overlap_count = 0
        for a in appts:
            s = datetime.combine(sel_date, a.appointment_time)
            e = s + timedelta(minutes=a.total_duration)
            if max(curr, s) < min(end, e): overlap_count += 1
        
        if overlap_count < est.capacity: avail.append(curr.strftime('%H:%M'))
        curr += timedelta(minutes=15)
    return jsonify(avail)

if __name__ == '__main__':
    app.run(debug=True)
'''

LAYOUT_HTML = r'''<!DOCTYPE html>
<html lang="pt-br">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{% block title %}Agenda Fácil{% endblock %}</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css">
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <style>body{font-family:'Inter',sans-serif;background-color:#f8f9fa} .tailwind-scope{font-family:'Inter',sans-serif} a{text-decoration:none} main{flex:1} body{min-height:100vh;display:flex;flex-direction:column}</style>
</head>
<body>
    <nav class="navbar navbar-expand-lg navbar-light bg-white shadow-sm sticky-top">
        <div class="container">
            <a class="navbar-brand fw-bold" href="{{ url_for('index') }}"><i class="bi bi-calendar-check text-primary"></i> Agenda Fácil</a>
            <div class="d-flex align-items-center">
                {% if current_user.is_authenticated %}
                    <button class="btn btn-outline-primary border-0" type="button" data-bs-toggle="offcanvas" data-bs-target="#sidebarMenu"><i class="bi bi-list fs-4"></i></button>
                {% else %}
                    <div class="d-none d-lg-block">
                        <a href="{{ url_for('login') }}" class="fw-bold text-decoration-none me-3 text-dark">Entrar</a>
                        <a href="{{ url_for('register_business') }}" class="btn btn-primary btn-sm rounded-pill px-3">Testar Grátis</a>
                    </div>
                     <button class="navbar-toggler d-lg-none" type="button" data-bs-toggle="collapse" data-bs-target="#mobileNav"><span class="navbar-toggler-icon"></span></button>
                {% endif %}
            </div>
            <div class="collapse navbar-collapse" id="mobileNav">
                 {% if not current_user.is_authenticated %}
                <ul class="navbar-nav ms-auto mt-2 mt-lg-0">
                    <li class="nav-item"><a class="nav-link" href="{{ url_for('login') }}">Entrar</a></li>
                    <li class="nav-item"><a class="nav-link" href="{{ url_for('register_business') }}">Testar Grátis</a></li>
                </ul>
                {% endif %}
            </div>
        </div>
    </nav>
    {% if current_user.is_authenticated %}
    <div class="offcanvas offcanvas-end" tabindex="-1" id="sidebarMenu">
        <div class="offcanvas-header"><h5 class="offcanvas-title fw-bold">{{ current_user.establishment.name }}</h5><button type="button" class="btn-close" data-bs-dismiss="offcanvas"></button></div>
        <div class="offcanvas-body d-flex flex-column">
            <ul class="nav flex-column fs-5 gap-2">
                <li class="nav-item"><a class="nav-link text-dark" href="{{ url_for('admin_dashboard') }}"><i class="bi bi-speedometer2 me-2"></i> Painel</a></li>
                <li class="nav-item"><a class="nav-link text-dark" href="{{ url_for('establishment_services', url_prefix=current_user.establishment.url_prefix) }}" target="_blank"><i class="bi bi-box-arrow-up-right me-2"></i> Ver Minha Página</a></li>
                <!-- REMOVIDO: Botão Assinar Plano retirado do Menu para o Hard Trial -->
            </ul>
            <div class="mt-auto border-top pt-3"><a class="nav-link text-danger fw-bold" href="{{ url_for('logout') }}"><i class="bi bi-box-arrow-right me-2"></i> Sair</a></div>
        </div>
    </div>
    {% endif %}
    <main class="container-fluid p-0">
        {% with m = get_flashed_messages(with_categories=true) %}
            {% if m %}<div class="container mt-3">{% for c, msg in m %}<div class="alert alert-{{ c }} alert-dismissible fade show shadow-sm">{{ msg }} <button type="button" class="btn-close" data-bs-dismiss="alert"></button></div>{% endfor %}</div>{% endif %}
        {% endwith %}
        {% block content %}{% endblock %}
    </main>
    <footer class="bg-white border-top pt-4 pb-3 mt-auto"><div class="container text-center"><p class="text-muted small mb-0">© 2025 Agenda Fácil.</p></div></footer>
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
    {% block scripts %}{% endblock %}
</body>
</html>
'''

INDEX_HTML = r'''{% extends 'layout.html' %}
{% block title %}Agenda Fácil - Plataforma Profissional{% endblock %}
{% block content %}
<div class="tailwind-scope">
    <section class="bg-gradient-to-b from-white to-gray-50 overflow-hidden pt-12 md:pt-16 pb-16 md:pb-20">
        <div class="max-w-7xl mx-auto px-6 lg:px-8 grid lg:grid-cols-2 gap-12 items-center">
            <div class="text-center lg:text-left">
                <div class="inline-block bg-blue-100 text-blue-700 text-xs font-bold px-3 py-1 rounded-full mb-6">🚀 Teste Grátis por 7 Dias</div>
                
                <h1 class="text-4xl md:text-5xl lg:text-6xl font-extrabold tracking-tight text-gray-900 leading-tight mb-4 md:mb-6">Transforme agendamentos em <span class="text-blue-600">mais lucro</span>.</h1>
                
                <p class="text-base md:text-lg text-gray-600 mb-8 leading-relaxed max-w-lg mx-auto lg:mx-0">
                    A ferramenta definitiva para barbearias, salões e clínicas.
                    <br><span class="text-blue-600 font-bold text-xl md:text-2xl">Teste grátis por 7 dias</span>. Depois, apenas R$ 34,90/mês.
                    <br>Tenha um link profissional, receba agendamentos 24h e elimine a troca de mensagens no WhatsApp.
                </p>
                <div class="flex flex-col sm:flex-row gap-4 justify-center lg:justify-start">
                    <a href="{{ url_for('register_business') }}" class="bg-blue-600 text-white px-8 py-4 rounded-xl font-bold text-lg hover:bg-blue-700 transition shadow-lg transform hover:-translate-y-1">Começar Teste Grátis</a>
                    <a href="{{ url_for('login') }}" class="px-8 py-4 rounded-xl font-bold text-gray-700 hover:bg-gray-200 transition border border-gray-300">Já sou Cliente</a>
                </div>
            </div>
            <div class="relative mt-12 lg:mt-0 perspective-1000">
                <div class="relative bg-gray-900 rounded-2xl p-2 shadow-2xl transform rotate-y-12 transition hover:rotate-y-0 duration-700">
                    <div class="relative rounded-xl overflow-hidden bg-white aspect-video group">
                        <img src="{{ url_for('static', filename='painel.png') }}" class="w-full h-full object-cover" onerror="this.onerror=null; this.src='https://placehold.co/1280x800/E2E8F0/475569?text=Insira+painel.png+na+pasta+static';">
                    </div>
                </div>
            </div>
        </div>
    </section>

    <!-- SEÇÃO PARA QUEM É -->
    <section class="py-16 md:py-20 bg-white">
        <div class="max-w-7xl mx-auto px-6 text-center">
            <h2 class="text-2xl md:text-3xl font-bold text-gray-900 mb-10 md:mb-12">Ideal para profissionais exigentes</h2>
            <div class="grid grid-cols-2 md:grid-cols-4 gap-4 md:gap-8">
                <div class="p-6 rounded-2xl bg-gray-50 hover:bg-blue-50 transition border border-gray-100 hover:border-blue-200">
                    <div class="text-3xl md:text-4xl mb-4">💈</div><h3 class="font-bold text-gray-900 text-sm md:text-base">Barbearias</h3>
                </div>
                <div class="p-6 rounded-2xl bg-gray-50 hover:bg-pink-50 transition border border-gray-100 hover:border-pink-200">
                    <div class="text-3xl md:text-4xl mb-4">💇‍♀️</div><h3 class="font-bold text-gray-900 text-sm md:text-base">Salões</h3>
                </div>
                <div class="p-6 rounded-2xl bg-gray-50 hover:bg-green-50 transition border border-gray-100 hover:border-green-200">
                    <div class="text-3xl md:text-4xl mb-4">💆‍♂️</div><h3 class="font-bold text-gray-900 text-sm md:text-base">Clínicas</h3>
                </div>
                <div class="p-6 rounded-2xl bg-gray-50 hover:bg-purple-50 transition border border-gray-100 hover:border-purple-200">
                    <div class="text-3xl md:text-4xl mb-4">💅</div><h3 class="font-bold text-gray-900 text-sm md:text-base">Estética</h3>
                </div>
            </div>
        </div>
    </section>

    <section class="py-16 md:py-20 bg-gray-900 text-white">
        <div class="max-w-7xl mx-auto px-6">
            <div class="text-center mb-12 md:mb-16"><h2 class="text-2xl md:text-3xl lg:text-4xl font-bold mb-4">Tudo o que você precisa para crescer</h2></div>
            <div class="grid md:grid-cols-3 gap-6 md:gap-8">
                <div class="bg-gray-800 p-8 rounded-2xl border border-gray-700 hover:border-blue-500 transition group">
                    <div class="w-12 h-12 bg-blue-500/20 rounded-lg flex items-center justify-center mb-6 text-blue-400 group-hover:bg-blue-500 group-hover:text-white transition"><i class="bi bi-link-45deg text-2xl"></i></div>
                    <h3 class="text-xl font-bold mb-3">Link Personalizado</h3>
                    <p class="text-gray-400 text-sm leading-relaxed">Pare de perguntar "qual horário você quer?". Envie seu link e deixe o cliente escolher.</p>
                </div>
                <div class="bg-gray-800 p-8 rounded-2xl border border-gray-700 hover:border-green-500 transition group">
                    <div class="w-12 h-12 bg-green-500/20 rounded-lg flex items-center justify-center mb-6 text-green-400 group-hover:bg-green-500 group-hover:text-white transition"><i class="bi bi-clock-history text-2xl"></i></div>
                    <h3 class="text-xl font-bold mb-3">Agenda 24 horas</h3>
                    <p class="text-gray-400 text-sm leading-relaxed">Seu negócio aberto mesmo quando você está dormindo.</p>
                </div>
                <div class="bg-gray-800 p-8 rounded-2xl border border-gray-700 hover:border-purple-500 transition group">
                    <div class="w-12 h-12 bg-purple-500/20 rounded-lg flex items-center justify-center mb-6 text-purple-400 group-hover:bg-purple-500 group-hover:text-white transition"><i class="bi bi-calendar-check text-2xl"></i></div>
                    <h3 class="text-xl font-bold mb-3">Controle Total</h3>
                    <p class="text-gray-400 text-sm leading-relaxed">Defina horários de almoço, dias de folga, bloqueio de clientes e duração de cada serviço.</p>
                </div>
            </div>
        </div>
    </section>

    <section class="py-16 md:py-24 bg-blue-600 text-center">
        <div class="max-w-4xl mx-auto px-6">
            <h2 class="text-2xl md:text-3xl lg:text-4xl font-bold text-white mb-8">Pronto para profissionalizar seu negócio?</h2>
            <a href="{{ url_for('register_business') }}" class="inline-block bg-white text-blue-600 px-10 py-4 rounded-full font-bold text-lg hover:bg-gray-100 transition shadow-lg">Iniciar Teste Grátis</a>
            <p class="mt-6 text-blue-200 text-sm">Configuração em menos de 2 minutos. Sem compromisso.</p>
        </div>
    </section>
</div>
{% endblock %}
'''

REGISTER_HTML = r'''{% extends 'layout.html' %}
{% block title %}Criar Conta{% endblock %}
{% block content %}
<div class="row justify-content-center mt-5 mb-5">
    <div class="col-md-8 col-lg-6">
        <div class="card shadow-lg border-0 rounded-4 overflow-hidden">
            <div class="card-header bg-blue-600 text-white text-center py-4">
                <h3 class="fw-bold mb-0">Crie sua conta e teste grátis</h3>
                <p class="text-blue-100 text-sm mb-0 mt-1"><i class="bi bi-check-circle-fill"></i> 7 dias de acesso total. Sem cartão de crédito.</p>
            </div>
            <div class="card-body p-4 p-md-5 bg-white">
                <form method="POST" action="{{ url_for('register_business') }}">
                    <h5 class="mb-3 text-primary fw-bold small text-uppercase ls-1">Dados do Negócio</h5>
                    <div class="mb-3"><label class="form-label small fw-bold">Nome do Estabelecimento</label><input type="text" class="form-control" name="business_name" required></div>
                    <div class="mb-3"><label class="form-label small fw-bold">Link Personalizado</label><div class="input-group"><span class="input-group-text bg-light border-end-0">agendafacil.com/b/</span><input type="text" class="form-control border-start-0 ps-0" name="url_prefix" pattern="[a-z0-9-]+" required></div></div>
                    <div class="row g-2 mb-4">
                        <div class="col-md-6"><label class="form-label small fw-bold">WhatsApp</label><input type="text" class="form-control" name="contact_phone"></div>
                        <div class="col-md-6"><label class="form-label small fw-bold">E-mail para Notificações</label><input type="email" class="form-control" name="contact_email" required></div>
                    </div>
                    <h5 class="mb-3 text-primary fw-bold small text-uppercase ls-1 border-top pt-4">Acesso ao Painel</h5>
                    <div class="row g-2">
                        <div class="col-md-6 mb-3"><label class="form-label small fw-bold">Usuário</label><input type="text" class="form-control" name="username" required></div>
                        <div class="col-md-6 mb-3"><label class="form-label small fw-bold">Senha</label><input type="password" class="form-control" name="password" required></div>
                    </div>
                    <button class="btn btn-primary w-100 py-3 fw-bold rounded-3 shadow-sm mt-2">Criar Conta e Acessar Painel</button>
                    <div class="text-center mt-3 small text-muted">Ao criar conta você concorda com nossos termos de serviço.</div>
                </form>
            </div>
        </div>
    </div>
</div>
{% endblock %}
'''

LOGIN_HTML = r'''{% extends 'layout.html' %}
{% block title %}Login{% endblock %}
{% block content %}
<div class="row justify-content-center mt-5">
    <div class="col-md-5 col-lg-4">
        <div class="card shadow-lg border-0 rounded-4 p-4">
            <div class="text-center mb-4"><h2 class="fw-bold h4">Acessar Painel</h2></div>
            <form method="POST">
                <div class="mb-3"><label class="form-label small fw-bold">Usuário</label><input type="text" class="form-control form-control-lg" name="username" required></div>
                <div class="mb-4"><label class="form-label small fw-bold">Senha</label><input type="password" class="form-control form-control-lg" name="password" required></div>
                <button class="btn btn-dark w-100 py-3 fw-bold rounded-3">Entrar</button>
            </form>
            <div class="text-center mt-4 border-top pt-3"><a href="{{ url_for('register_business') }}" class="text-decoration-none small text-muted">Não tem conta? <span class="text-blue-600 fw-bold">Teste grátis</span></a></div>
        </div>
    </div>
</div>
{% endblock %}
'''

ADMIN_HTML = r'''{% extends 'layout.html' %}
{% block title %}Painel Admin{% endblock %}
{% block content %}
<div class="container py-4">

    <!-- BANNER DE TESTE GRÁTIS (SEM BOTÃO DE PAGAMENTO - HARD TRIAL) -->
    {% if not current_user.establishment.is_active and current_user.establishment.trial_ends %}
    <div class="alert alert-warning border-warning shadow-sm d-flex flex-column flex-md-row justify-content-between align-items-center mb-4 rounded-3">
        <div class="d-flex align-items-center">
            <i class="bi bi-clock-history fs-3 me-3 text-warning-emphasis"></i>
            <div>
                <h6 class="fw-bold mb-0 text-dark">Período de Teste Gratuito</h6>
                <span class="small text-dark">Aproveite! Faltam <strong>{{ current_user.establishment.trial_days_left }} dias</strong> para o fim do seu teste.</span>
            </div>
        </div>
    </div>
    {% endif %}

    <div class="d-flex justify-content-between align-items-center mb-4">
        <div class="d-flex align-items-center gap-3">
            {% if establishment.logo_filename %}<img src="{{ url_for('static', filename='uploads/' + establishment.logo_filename) }}" class="rounded-circle shadow-sm border border-2 border-white" style="width: 50px; height: 50px; object-fit: cover;">
            {% else %}<div class="rounded-circle bg-secondary d-flex align-items-center justify-content-center text-white fw-bold" style="width: 60px; height: 60px;">Logo</div>{% endif %}
            <div><h1 class="h3 mb-0">Painel: {{ establishment.name }}</h1><a href="{{ url_for('establishment_services', url_prefix=establishment.url_prefix) }}" target="_blank" class="text-decoration-none small">Ver a Minha Página <i class="bi bi-box-arrow-up-right"></i></a></div>
        </div>
    </div>
    
    <div class="card shadow-sm border-0 mb-4 p-3 bg-light">
        <form action="{{ url_for('update_settings') }}" method="POST" enctype="multipart/form-data" class="row align-items-center g-2">
            <input type="hidden" name="form_type" value="contact"> 
            <div class="col-md-3"><label class="small fw-bold">WhatsApp:</label><input type="text" name="contact_phone" class="form-control form-control-sm" value="{{ establishment.contact_phone or '' }}"></div>
            <div class="col-md-3"><label class="small fw-bold">E-mail (Notificações):</label><input type="email" name="contact_email" class="form-control form-control-sm" value="{{ establishment.contact_email or '' }}"></div>
            <div class="col-md-2"><label class="small fw-bold">Profissionais:</label><select name="capacity" class="form-select form-select-sm"><option value="1" {% if establishment.capacity == 1 %}selected{% endif %}>1</option><option value="2" {% if establishment.capacity == 2 %}selected{% endif %}>2</option><option value="3" {% if establishment.capacity == 3 %}selected{% endif %}>3</option></select></div>
            <div class="col-md-2"><label class="small fw-bold">Logo:</label><input type="file" name="logo" class="form-control form-control-sm" accept="image/*"></div>
            <div class="col-md-2 text-end pt-4"><button class="btn btn-primary btn-sm w-100">Salvar</button></div>
        </form>
    </div>
    
    <div class="row">
        <div class="col-12 mb-4">
            <div class="card shadow-sm border-0">
                <div class="card-header bg-white fw-bold">Horários de Funcionamento</div>
                <div class="card-body p-0">
                    <form action="{{ url_for('update_settings') }}" method="POST">
                        <input type="hidden" name="form_type" value="schedule">
                        <div class="table-responsive">
                            <table class="table table-bordered mb-0 align-middle text-center"><thead class="table-light"><tr><th style="width: 50px;">Ativo</th><th>Dia</th><th>Abertura</th><th>Fechamento</th><th>Almoço Início</th><th>Almoço Fim</th></tr></thead><tbody>
                                    {% set day_names = ['Segunda', 'Terça', 'Quarta', 'Quinta', 'Sexta', 'Sábado', 'Domingo'] %}{% for d in schedules %}<tr class="{% if not d.is_active %}bg-light text-muted{% endif %}"><input type="hidden" name="schedule_id" value="{{ d.id }}"><td><input class="form-check-input" type="checkbox" name="active_{{ d.id }}" {% if d.is_active %}checked{% endif %}></td><td class="fw-bold text-start">{{ day_names[d.day_index] }}</td><td><input type="time" class="form-control form-control-sm" name="work_start_{{ d.id }}" value="{{ d.work_start.strftime('%H:%M') }}"></td><td><input type="time" class="form-control form-control-sm" name="work_end_{{ d.id }}" value="{{ d.work_end.strftime('%H:%M') }}"></td><td><input type="time" class="form-control form-control-sm" name="lunch_start_{{ d.id }}" value="{{ d.lunch_start.strftime('%H:%M') if d.lunch_start else '' }}"></td><td><input type="time" class="form-control form-control-sm" name="lunch_end_{{ d.id }}" value="{{ d.lunch_end.strftime('%H:%M') if d.lunch_end else '' }}"></td></tr>{% endfor %}
                            </tbody></table>
                        </div>
                        <div class="p-3 bg-light border-top text-end"><button class="btn btn-success fw-bold px-4">Salvar Horários</button></div>
                    </form>
                </div>
            </div>
        </div>
        
        <div class="col-lg-7">
            <div class="card shadow-sm border-0 mb-4">
                <div class="card-header bg-white fw-bold d-flex justify-content-between">
                    <span>Próximos Agendamentos</span> <span class="badge bg-primary rounded-pill">{{ today_count }} hoje</span>
                </div>
                <div class="card-body p-0">
                    <table class="table table-hover mb-0 align-middle">
                        <thead class="table-light"><tr><th>Data/Hora</th><th>Serviços / Cliente</th><th>Ações</th></tr></thead>
                        <tbody>
                            {% for a in appointments %}
                            <tr>
                                <td><div class="fw-bold text-primary">{{ a.appointment_date.strftime('%d/%m') }}</div><div>{{ a.appointment_time.strftime('%H:%M') }}</div><div class="small text-muted">{{ a.total_duration }}min</div></td>
                                <td><div class="fw-bold">{{ a.service_names }}</div><div class="text-success fw-bold small">R$ {{ "%.2f"|format(a.total_price) }}</div><div class="mt-1">{{ a.client_name }} <br> <i class="bi bi-whatsapp text-success"></i> {{ a.client_phone }}</div></td>
                                <td>
                                    <div class="d-flex gap-1 flex-column flex-md-row">
                                        <form method="POST" action="{{ url_for('delete_appointment', id=a.id) }}" onsubmit="return confirm('Apenas cancelar?');"><button class="btn btn-sm btn-outline-danger w-100" title="Cancelar Agendamento"><i class="bi bi-trash"></i> Cancelar</button></form>
                                        <form method="POST" action="{{ url_for('mark_no_show', id=a.id) }}" onsubmit="return confirm('Isso bloqueará este cliente de novos agendamentos.');"><button class="btn btn-sm btn-warning w-100 text-dark fw-bold" title="Bloquear Cliente"><i class="bi bi-person-x-fill"></i> Deu Bolo!</button></form>
                                    </div>
                                </td>
                            </tr>
                            {% else %}<tr><td colspan="3" class="text-center py-4 text-muted">Agenda livre.</td></tr>{% endfor %}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
        
        <div class="col-lg-5">
            <div class="card shadow-sm border-0 mb-4">
                <div class="card-header bg-white fw-bold">Serviços Oferecidos</div>
                <div class="card-body">
                    <form action="{{ url_for('add_service') }}" method="POST" class="mb-3">
                        <div class="input-group input-group-sm">
                            <input type="text" name="name" class="form-control" placeholder="Nome" required>
                            <input type="number" name="duration" class="form-control" placeholder="Min" style="max-width: 70px;" required>
                            <input type="text" name="price" class="form-control" placeholder="R$" style="max-width: 90px;" required>
                            <button class="btn btn-success"><i class="bi bi-plus"></i></button>
                        </div>
                    </form>
                    <ul class="list-group list-group-flush small">
                        {% for s in services %}
                        <li class="list-group-item d-flex justify-content-between align-items-center px-0">
                            <div><strong>{{ s.name }}</strong> ({{ s.duration }}m)<br><span class="text-success fw-bold">R$ {{ "%.2f"|format(s.price) }}</span></div>
                            <form method="POST" action="{{ url_for('delete_service', id=s.id) }}" onsubmit="return confirm('Excluir?');"><button class="btn btn-link text-danger p-0"><i class="bi bi-trash"></i></button></form>
                        </li>
                        {% endfor %}
                    </ul>
                </div>
            </div>

            <div class="card shadow-sm border-0 mt-4">
                <div class="card-header bg-white py-3 border-bottom"><h6 class="mb-0 fw-bold text-danger"><i class="bi bi-person-x"></i> Clientes Bloqueados (No-Show)</h6></div>
                <div class="card-body">
                    <form action="{{ url_for('add_blacklist') }}" method="POST" class="mb-3">
                        <div class="input-group input-group-sm">
                            <input type="text" name="phone" class="form-control" placeholder="WhatsApp (ex: 87999999999)" required>
                            <button class="btn btn-danger fw-bold">Bloquear</button>
                        </div>
                    </form>
                    <ul class="list-group list-group-flush small" style="max-height: 200px; overflow-y: auto;">
                        {% for b in blacklists %}
                        <li class="list-group-item d-flex justify-content-between align-items-center px-0">
                            <span class="text-muted fw-bold"><i class="bi bi-whatsapp"></i> {{ b.client_phone }}</span>
                            <form method="POST" action="{{ url_for('remove_blacklist', id=b.id) }}" onsubmit="return confirm('Desbloquear este cliente?');">
                                <button class="btn btn-link text-success p-0 text-decoration-none"><i class="bi bi-unlock-fill"></i> Liberar</button>
                            </form>
                        </li>
                        {% else %}
                        <li class="list-group-item px-0 text-muted text-center border-0">Nenhum número bloqueado.</li>
                        {% endfor %}
                    </ul>
                </div>
            </div>
        </div>
    </div>
</div>
{% endblock %}
'''

LISTA_SERVICOS_HTML = r'''{% extends 'layout.html' %}
{% block content %}
<div class="container py-5">
    <div class="text-center mb-5">
        {% if establishment.logo_filename %}<img src="{{ url_for('static', filename='uploads/' + establishment.logo_filename) }}" class="rounded-circle shadow mb-3" style="width: 100px; height: 100px; object-fit: cover;">
        {% else %}<div class="rounded-circle bg-secondary d-inline-flex align-items-center justify-content-center text-white fw-bold mb-3 shadow" style="width: 100px; height: 100px; font-size: 2rem;">{{ establishment.name[0] }}</div>{% endif %}
        <h1 class="display-5 fw-bold">{{ establishment.name }}</h1>
    </div>
    <div class="row justify-content-center gap-3">
        {% for s in services %}
        <div class="col-md-4">
            <div class="card shadow-sm border-0 h-100 p-3 text-center hover-shadow transition">
                <h4 class="fw-bold">{{ s.name }}</h4><p class="text-success fw-bold fs-5">R$ {{ "%.2f"|format(s.price) }}</p><p class="text-muted"><i class="bi bi-clock"></i> {{ s.duration }} min</p>
                <a href="{{ url_for('schedule_service', url_prefix=establishment.url_prefix, service_id=s.id) }}" class="btn btn-primary w-100 fw-bold rounded-pill">Agendar</a>
            </div>
        </div>
        {% endfor %}
    </div>
</div>
{% endblock %}
'''

AGENDAMENTO_HTML = r'''{% extends 'layout.html' %}
{% block content %}
<div class="container py-4">
    <div class="row justify-content-center">
        <div class="col-lg-6">
            <div class="card shadow-lg border-0 p-4 rounded-4">
                <div class="text-center mb-4 border-bottom pb-3">
                    <h4 class="fw-bold text-dark">{{ establishment.name }}</h4>
                </div>
                
                <form id="form" method="POST" action="{{ url_for('create_appointment', url_prefix=establishment.url_prefix) }}">
                    
                    <div class="bg-light p-3 rounded-3 mb-4 border">
                        <h6 class="fw-bold text-primary mb-3">Serviços Selecionados</h6>
                        
                        <div class="form-check mb-2">
                            <input class="form-check-input service-cb" type="checkbox" name="services" value="{{ main_service.id }}" data-dur="{{ main_service.duration }}" data-price="{{ main_service.price }}" checked onclick="return false;" style="pointer-events: none;">
                            <label class="form-check-label fw-bold">{{ main_service.name }} <span class="text-success">- R$ {{ "%.2f"|format(main_service.price) }}</span> <small class="text-muted">({{ main_service.duration }}m)</small></label>
                        </div>
                        
                        {% if other_services %}
                        <hr class="text-muted">
                        <p class="small fw-bold text-muted mb-2">Deseja adicionar algo mais?</p>
                        {% for s in other_services %}
                        <div class="form-check mb-2">
                            <input class="form-check-input service-cb" type="checkbox" name="services" value="{{ s.id }}" id="s_{{ s.id }}" data-dur="{{ s.duration }}" data-price="{{ s.price }}">
                            <label class="form-check-label" for="s_{{ s.id }}">{{ s.name }} <span class="text-success fw-bold">+ R$ {{ "%.2f"|format(s.price) }}</span> <small class="text-muted">(+{{ s.duration }}m)</small></label>
                        </div>
                        {% endfor %}
                        {% endif %}
                        
                        <div class="mt-3 text-end fw-bold fs-5">Total: <span class="text-success">R$ <span id="display-price">0.00</span></span></div>
                        <div class="text-end small text-muted">Tempo estimado: <span id="display-dur">0</span> min</div>
                    </div>

                    <div class="mb-3"><label class="fw-bold small">Seu Nome</label><input type="text" name="client_name" class="form-control" required></div>
                    <div class="row g-2 mb-3">
                        <div class="col-6"><label class="fw-bold small">WhatsApp</label><input type="tel" name="client_phone" class="form-control" required></div>
                        <div class="col-6"><label class="fw-bold small">Seu E-mail</label><input type="email" name="client_email" class="form-control" required></div>
                    </div>
                    
                    <div class="mb-3"><label class="fw-bold small">Escolha a Data</label><input type="date" id="date" name="appointment_date" class="form-control" required></div>
                    <div class="mb-4"><label class="fw-bold small">Horários Disponíveis</label><div id="slots" class="d-flex flex-wrap gap-2 mt-2"><small class="text-muted">Selecione a data...</small></div><input type="hidden" id="time" name="appointment_time" required></div>
                    
                    <div class="form-check mb-4 bg-warning bg-opacity-10 p-3 rounded border border-warning">
                        <input class="form-check-input ms-1" type="checkbox" id="term_noshow" required>
                        <label class="form-check-label ms-2 small fw-bold text-dark" for="term_noshow">
                            Declaro que comparecerei ao horário marcado. Entendo que o não comparecimento sem aviso prévio resultará no bloqueio permanente do meu número neste estabelecimento.
                        </label>
                    </div>

                    <button id="btn" class="btn btn-primary w-100 py-3 fw-bold rounded-pill" disabled>Confirmar Agendamento</button>
                </form>
            </div>
        </div>
    </div>
</div>
{% endblock %}
{% block scripts %}
<script>
let totalDur = 0;

function updateTotals() {
    let p = 0;
    let d = 0;
    document.querySelectorAll('.service-cb:checked').forEach(cb => {
        p += parseFloat(cb.getAttribute('data-price'));
        d += parseInt(cb.getAttribute('data-dur'));
    });
    document.getElementById('display-price').innerText = p.toFixed(2);
    document.getElementById('display-dur').innerText = d;
    totalDur = d;
    
    if(document.getElementById('date').value) {
        fetchSlots();
    }
}

document.querySelectorAll('.service-cb').forEach(cb => cb.addEventListener('change', updateTotals));
updateTotals(); 

const today = new Date();
const year = today.getFullYear();
const month = String(today.getMonth() + 1).padStart(2, '0');
const day = String(today.getDate()).padStart(2, '0');
document.getElementById('date').min = `${year}-${month}-${day}`;

async function fetchSlots() {
    const dVal = document.getElementById('date').value;
    if(!dVal) return;
    const div = document.getElementById('slots');
    div.innerHTML = '<span class="spinner-border spinner-border-sm text-primary"></span> Carregando...';
    document.getElementById('btn').disabled = true;
    document.getElementById('time').value = '';
    
    const res = await fetch(`/api/horarios_disponiveis?est_id={{ establishment.id }}&date=${dVal}&duration=${totalDur}`);
    const times = await res.json();
    
    div.innerHTML = '';
    if(times.length === 0) div.innerHTML = '<span class="text-danger small fw-bold">Nenhum horário com tempo suficiente disponível.</span>';
    
    times.forEach(t => {
        const b = document.createElement('button');
        b.type='button'; b.className='btn btn-outline-dark btn-sm rounded-pill px-3'; b.innerText=t;
        b.onclick = () => {
            document.querySelectorAll('#slots button').forEach(x=>x.classList.replace('btn-dark','btn-outline-dark'));
            b.classList.replace('btn-outline-dark','btn-dark');
            document.getElementById('time').value=t;
            document.getElementById('btn').disabled=false;
        };
        div.appendChild(b);
    });
}

document.getElementById('date').addEventListener('change', fetchSlots);
</script>
{% endblock %}
'''

SUCCESS_APPOINTMENT_HTML = r'''{% extends 'layout.html' %}
{% block content %}
<div class="container py-5 text-center">
    <div class="card shadow-lg border-0 rounded-4 p-5 max-w-lg mx-auto">
        <div class="mb-4 text-success display-1"><i class="bi bi-check-circle-fill"></i></div>
        <h2 class="fw-bold text-gray-800">Agendamento Confirmado!</h2>
        <p class="text-muted mb-4">Enviamos um e-mail com os detalhes.</p>
        
        <div class="bg-light p-3 rounded-3 mb-4 text-start border">
            <p class="mb-2"><strong>Serviços:</strong><br><span class="text-primary fw-bold">{{ appointment.service_names }}</span></p>
            <p class="mb-2"><strong>Data:</strong> {{ appointment.appointment_date.strftime('%d/%m/%Y') }} às {{ appointment.appointment_time.strftime('%H:%M') }}</p>
            <p class="mb-0 border-top pt-2 mt-2"><strong>Total a pagar no local:</strong> <span class="text-success fw-bold fs-5">R$ {{ "%.2f"|format(appointment.total_price) }}</span></p>
        </div>

        {% if zap_link != "#" %}
        <a href="{{ zap_link }}" target="_blank" class="btn btn-success w-100 py-3 fw-bold rounded-pill mb-2"><i class="bi bi-whatsapp"></i> Enviar Mensagem ao Profissional</a>
        {% endif %}
        <div class="mt-3"><a href="{{ url_for('establishment_services', url_prefix=appointment.establishment.url_prefix) }}" class="text-decoration-none fw-bold">Agendar Outro</a></div>
    </div>
</div>
{% endblock %}
'''

ERROR_INACTIVE_HTML = r'''{% extends 'layout.html' %}
{% block content %}<div class="container py-5 text-center"><div class="alert alert-warning py-5"><h2>Estabelecimento Indisponível</h2><p>{{ message }}</p></div></div>{% endblock %}
'''

def atualizar_sistema():
    if not os.path.exists('templates'): os.makedirs('templates')
    uploads_path = os.path.join('static', 'uploads')
    if not os.path.exists(uploads_path): os.makedirs(uploads_path)

    arquivos = {
        'app.py': APP_PY,
        'requirements.txt': REQUIREMENTS_TXT,
        'Procfile': PROCFILE,
        'templates/admin.html': ADMIN_HTML,
        'templates/layout.html': LAYOUT_HTML,
        'templates/index.html': INDEX_HTML,
        'templates/register.html': REGISTER_HTML,
        'templates/login.html': LOGIN_HTML,
        'templates/lista_servicos.html': LISTA_SERVICOS_HTML,
        'templates/agendamento.html': AGENDAMENTO_HTML,
        'templates/success_appointment.html': SUCCESS_APPOINTMENT_HTML,
        'templates/error_inactive.html': ERROR_INACTIVE_HTML
    }

    for caminho, conteudo in arquivos.items():
        with open(caminho, 'w', encoding='utf-8') as f: f.write(conteudo.strip())

    print("\n[INFO] Instalando dependências...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])
    print("\n[SUCESSO] Sistema V47 (Hard Trial Paywall + Correção Stripe) instalado!")

if __name__ == "__main__":
    atualizar_sistema()