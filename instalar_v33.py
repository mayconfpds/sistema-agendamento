import os
import sys
import subprocess

# --- DEPENDÊNCIAS ---
REQUIREMENTS_TXT = r'''Flask
Flask-SQLAlchemy
Flask-Login
Werkzeug
gunicorn
stripe
requests
'''

PROCFILE = r'''web: gunicorn app:app'''

# --- APP.PY COMPLETO ---
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
import stripe

# Timeout de segurança
socket.setdefaulttimeout(15)

app = Flask(__name__)
app.config['SECRET_KEY'] = 'chave-v33-final-master'
basedir = os.path.abspath(os.path.dirname(__file__))

# --- BANCO ---
database_url = os.environ.get('DATABASE_URL')
if database_url and database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)
app.config['SQLALCHEMY_DATABASE_URI'] = database_url or 'sqlite:///' + os.path.join(basedir, 'agendamento.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# --- CONFIGURAÇÃO BREVO (API) ---
raw_key = os.environ.get('BREVO_API_KEY', '')
BREVO_API_KEY = raw_key.strip() if raw_key else None
BREVO_SENDER_EMAIL = os.environ.get('BREVO_SENDER_EMAIL', 'seu_email_login@gmail.com') 
BREVO_SENDER_NAME = "Agenda Facil"

# --- STRIPE ---
stripe.api_key = os.environ.get('STRIPE_API_KEY')
STRIPE_PRICE_ID = os.environ.get('STRIPE_PRICE_ID')

# --- UPLOAD ---
UPLOAD_FOLDER = os.path.join(basedir, 'static', 'uploads')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'} # Correção V33

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
if not os.path.exists(UPLOAD_FOLDER):
    try: os.makedirs(UPLOAD_FOLDER)
    except OSError: pass

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Faça login.'

# --- AUXILIARES ---
def get_now_brazil():
    return datetime.utcnow() - timedelta(hours=3)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# --- ENVIO DE EMAIL VIA BREVO API ---
def send_email(subject, recipient, body):
    if not BREVO_API_KEY:
        print(f"\n⚠️ [EMAIL VIRTUAL] Sem chave API. Para: {recipient}")
        return

    def _send_thread():
        url = "https://api.brevo.com/v3/smtp/email"
        headers = {
            "accept": "application/json",
            "api-key": BREVO_API_KEY,
            "content-type": "application/json"
        }
        html_body = f"<html><body><p>{body.replace(chr(10), '<br>')}</p></body></html>"
        
        payload = {
            "sender": {"name": BREVO_SENDER_NAME, "email": BREVO_SENDER_EMAIL},
            "to": [{"email": recipient}],
            "subject": subject,
            "htmlContent": html_body
        }
        
        try:
            response = requests.post(url, json=payload, headers=headers)
            if response.status_code in [200, 201, 202]:
                print(f"\n✅ [BREVO SUCESSO] Enviado para {recipient}")
            else:
                print(f"\n❌ [BREVO ERRO] {response.status_code} - {response.text}")
        except Exception as e:
            print(f"\n❌ [ERRO CONEXÃO BREVO] {e}")

    threading.Thread(target=_send_thread).start()

# --- ROTA DE DIAGNÓSTICO ---
@app.route('/teste-email')
def teste_email_brevo():
    status_chave = "OK" if BREVO_API_KEY else "FALTANDO"
    return f"Status Chave: {status_chave}<br>Remetente: {BREVO_SENDER_EMAIL}<br>Tente agendar algo para testar o worker."

@app.route('/health')
def health(): return "OK", 200

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
    client_email = db.Column(db.String(120), nullable=False)
    appointment_date = db.Column(db.Date, nullable=False)
    appointment_time = db.Column(db.Time, nullable=False)
    notified = db.Column(db.Boolean, default=False)
    service_id = db.Column(db.Integer, db.ForeignKey('services.id'), nullable=False)
    establishment_id = db.Column(db.Integer, db.ForeignKey('establishments.id'), nullable=False)

@login_manager.user_loader
def load_user(user_id): return Admin.query.get(int(user_id))

# --- WORKER DE NOTIFICAÇÕES (1H ANTES - API HTTP) ---
def notification_worker():
    print("--- Robô de Notificações Iniciado (Fuso BR) ---")
    while True:
        try:
            with app.app_context():
                inspector = inspect(db.engine)
                if not inspector.has_table("appointments"): 
                    time_module.sleep(5); continue
                
                upcoming = Appointment.query.filter(Appointment.notified == False).all()
                now = get_now_brazil()
                
                for appt in upcoming:
                    appt_dt = datetime.combine(appt.appointment_date, appt.appointment_time)
                    time_diff = appt_dt - now
                    minutes_diff = time_diff.total_seconds() / 60
                    
                    if 50 <= minutes_diff <= 70:
                        print(f"⏰ Enviando lembrete para {appt.client_name}")
                        
                        subj = f"Lembrete: {appt.establishment.name}"
                        body = f"Olá {appt.client_name},\n\nEste é um lembrete do seu agendamento hoje às {appt.appointment_time.strftime('%H:%M')}."
                        
                        send_email(subj, appt.client_email, body)
                        
                        if appt.establishment.contact_email:
                             send_email("Alerta de Agenda", appt.establishment.contact_email, f"Cliente {appt.client_name} agendado para daqui a 1 hora.")
                        
                        appt.notified = True
                        db.session.commit()
        except Exception as e:
            print(f"Erro Worker: {e}")
        
        time_module.sleep(60)

# --- ROTAS DE PAGAMENTO ---
@app.route('/pagamento')
@login_required
def payment():
    if current_user.establishment.is_active: return redirect(url_for('admin_dashboard'))
    if not stripe.api_key: flash('Erro Config: Chave Stripe ausente.', 'danger'); return redirect(url_for('login'))
    try:
        domain = request.host_url
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{'price': STRIPE_PRICE_ID, 'quantity': 1}],
            mode='subscription',
            allow_promotion_codes=True, # Correção V33: Cupons Ativados
            success_url=domain + 'pagamento/sucesso',
            cancel_url=domain + 'pagamento/cancelado',
            customer_email=current_user.establishment.contact_email,
        )
        return redirect(checkout_session.url, code=303)
    except Exception as e:
        flash(f'Erro Stripe: {str(e)}', 'danger')
        return render_template('login.html')

@app.route('/pagamento/sucesso')
@login_required
def payment_success():
    est = current_user.establishment
    est.is_active = True
    db.session.commit()
    flash('Assinatura Ativa!', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/pagamento/cancelado')
@login_required
def payment_cancel():
    flash('Pagamento cancelado.', 'warning')
    return redirect(url_for('login'))

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
            is_active=is_master 
        )
        db.session.add(est); db.session.commit()
        for i in range(7): db.session.add(DaySchedule(establishment_id=est.id, day_index=i, is_active=(i < 5), work_start=time(9,0), work_end=time(18,0)))
        adm = Admin(username=username, establishment_id=est.id)
        adm.set_password(request.form.get('password'))
        db.session.add(adm); db.session.commit()
        
        login_user(adm)
        if is_master: return redirect(url_for('admin_dashboard'))
        return redirect(url_for('payment'))
    return render_template('register.html')

@app.route('/b/<url_prefix>')
def establishment_services(url_prefix):
    est = Establishment.query.filter_by(url_prefix=url_prefix).first_or_404()
    if not est.is_active: return render_template('error_inactive.html', message="Estabelecimento temporariamente indisponível."), 403
    services = Service.query.filter_by(establishment_id=est.id).order_by(Service.name).all()
    return render_template('lista_servicos.html', services=services, establishment=est)

@app.route('/b/<url_prefix>/agendar/<int:service_id>')
def schedule_service(url_prefix, service_id):
    est = Establishment.query.filter_by(url_prefix=url_prefix).first_or_404()
    if not est.is_active: return "Inativo", 403
    service = Service.query.get_or_404(service_id)
    return render_template('agendamento.html', service=service, establishment=est)

@app.route('/b/<url_prefix>/confirmar', methods=['POST'])
def create_appointment(url_prefix):
    est = Establishment.query.filter_by(url_prefix=url_prefix).first_or_404()
    d = datetime.strptime(request.form.get('appointment_date'), '%Y-%m-%d').date()
    t = datetime.strptime(request.form.get('appointment_time'), '%H:%M').time()
    
    if datetime.combine(d, t) < get_now_brazil():
        flash('Horário inválido.', 'danger')
        return redirect(url_for('schedule_service', url_prefix=url_prefix, service_id=request.form.get('service_id')))
    
    appt = Appointment(client_name=request.form.get('client_name'), client_phone=request.form.get('client_phone'), client_email=request.form.get('client_email'), service_id=request.form.get('service_id'), appointment_date=d, appointment_time=t, establishment_id=est.id)
    db.session.add(appt); db.session.commit()
    
    send_email(f"Confirmado: {est.name}", appt.client_email, f"Agendado para {d.strftime('%d/%m')} às {t.strftime('%H:%M')}")
    if est.contact_email: send_email(f"Novo Cliente: {appt.client_name}", est.contact_email, f"Novo agendamento: {d.strftime('%d/%m')} às {t.strftime('%H:%M')}")
    
    zap_msg = f"Olá, confirmo agendamento: {d.strftime('%d/%m')} às {t.strftime('%H:%M')}."
    zap_link = f"https://wa.me/55{est.contact_phone}?text={zap_msg}" if est.contact_phone else "#"
    
    return render_template('success_appointment.html', appointment=appt, zap_link=zap_link)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        adm = Admin.query.filter_by(username=request.form.get('username')).first()
        if adm and adm.check_password(request.form.get('password')):
            login_user(adm)
            if not adm.establishment.is_active: return redirect(url_for('payment'))
            return redirect(url_for('admin_dashboard'))
        flash('Login inválido.', 'danger')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout(): logout_user(); return redirect(url_for('login'))

@app.route('/admin')
@login_required
def admin_dashboard():
    if not current_user.establishment.is_active: return redirect(url_for('payment'))
    est = current_user.establishment
    today = get_now_brazil().date()
    appts = Appointment.query.filter(Appointment.establishment_id == est.id, Appointment.appointment_date >= today).order_by(Appointment.appointment_date, Appointment.appointment_time).all()
    services = Service.query.filter_by(establishment_id=est.id).all()
    schedules = DaySchedule.query.filter_by(establishment_id=est.id).order_by(DaySchedule.day_index).all()
    today_count = Appointment.query.filter(Appointment.establishment_id == est.id, Appointment.appointment_date == today).count()
    return render_template('admin.html', appointments=appts, services=services, establishment=est, schedules=schedules, today_count=today_count)

@app.route('/admin/configurar', methods=['POST'])
@login_required
def update_settings():
    est = current_user.establishment
    ft = request.form.get('form_type')
    if ft == 'contact':
        est.contact_phone = request.form.get('contact_phone')
        est.contact_email = request.form.get('contact_email')
        if 'logo' in request.files:
            file = request.files['logo']
            if file and allowed_file(file.filename):
                fname = secure_filename(file.filename)
                uid = f"{est.id}_{int(time_module.time())}_{fname}"
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], uid))
                est.logo_filename = uid
        flash('Dados salvos!', 'success')
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
    if day_sched.lunch_start and day_sched.lunch_end: busy.append((datetime.combine(sel_date, day_sched.lunch_start), datetime.combine(sel_date, day_sched.lunch_end)))
    for a in appts: busy.append((datetime.combine(sel_date, a.appointment_time), datetime.combine(sel_date, a.appointment_time) + timedelta(minutes=a.service_info.duration)))
    avail = []
    curr = datetime.combine(sel_date, day_sched.work_start)
    limit = datetime.combine(sel_date, day_sched.work_end)
    now = get_now_brazil()
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
    with app.app_context():
        db.create_all()
        if not os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
            threading.Thread(target=notification_worker, daemon=True).start()
    app.run(debug=True)
'''

# --- TEMPLATES (Com Preços e Copy Ajustada) ---

INDEX_HTML = r'''{% extends 'layout.html' %}
{% block title %}Agenda Fácil - A Plataforma do Profissional{% endblock %}
{% block content %}
<div class="tailwind-scope font-sans">
    <section class="bg-gradient-to-b from-white to-gray-50 overflow-hidden pt-16 pb-20">
        <div class="max-w-7xl mx-auto px-6 lg:px-8 grid lg:grid-cols-2 gap-12 items-center">
            <!-- Texto Hero -->
            <div class="text-center lg:text-left">
                <div class="inline-block bg-blue-100 text-blue-700 text-xs font-bold px-3 py-1 rounded-full mb-6">
                    🚀 Sistema de Gestão Completo
                </div>
                <h1 class="text-5xl lg:text-6xl font-extrabold tracking-tight text-gray-900 leading-tight mb-6">
                    Transforme agendamentos em <span class="text-blue-600">mais lucro</span> e tempo livre.
                </h1>
                <p class="text-lg text-gray-600 mb-8 leading-relaxed max-w-lg mx-auto lg:mx-0">
                    A ferramenta definitiva para barbearias, salões e clínicas. 
                    <br><span class="text-blue-600 font-bold text-2xl">Apenas R$ 34,90/mês</span>.
                    <br>Tenha um link profissional, receba agendamentos 24h e elimine a troca de mensagens no WhatsApp.
                </p>
                <div class="flex flex-col sm:flex-row gap-4 justify-center lg:justify-start">
                    <a href="{{ url_for('register_business') }}" class="bg-blue-600 text-white px-8 py-4 rounded-xl font-bold text-lg hover:bg-blue-700 transition shadow-lg hover:shadow-xl transform hover:-translate-y-1">
                        Começar Agora
                    </a>
                    <a href="{{ url_for('login') }}" class="px-8 py-4 rounded-xl font-bold text-gray-700 hover:bg-gray-200 transition border border-gray-300">
                        Já sou Cliente
                    </a>
                </div>
                <p class="mt-4 text-xs text-gray-500">Gestão simplificada para o seu crescimento.</p>
            </div>

            <!-- Imagem do Painel (Notebook) -->
            <div class="relative mt-12 lg:mt-0 perspective-1000">
                <div class="relative bg-gray-900 rounded-2xl p-2 shadow-2xl transform rotate-y-12 transition hover:rotate-y-0 duration-700">
                    <div class="absolute top-0 left-1/2 -translate-x-1/2 w-20 h-1 bg-gray-800 rounded-b-md z-20"></div>
                    <div class="relative rounded-xl overflow-hidden bg-white aspect-video group">
                        <img src="{{ url_for('static', filename='painel.png') }}" 
                             alt="Painel Administrativo" 
                             class="w-full h-full object-cover transition duration-500 group-hover:scale-105"
                             onerror="this.onerror=null; this.src='https://placehold.co/1280x800/E2E8F0/475569?text=Insira+painel.png+na+pasta+static';">
                    </div>
                </div>
            </div>
        </div>
    </section>

    <!-- SEÇÃO PARA QUEM É -->
    <section class="py-20 bg-white">
        <div class="max-w-7xl mx-auto px-6 text-center">
            <h2 class="text-3xl font-bold text-gray-900 mb-12">Ideal para profissionais exigentes</h2>
            <div class="grid grid-cols-2 md:grid-cols-4 gap-8">
                <div class="p-6 rounded-2xl bg-gray-50 hover:bg-blue-50 transition border border-gray-100 hover:border-blue-200">
                    <div class="text-4xl mb-4">💈</div>
                    <h3 class="font-bold text-gray-900">Barbearias</h3>
                </div>
                <div class="p-6 rounded-2xl bg-gray-50 hover:bg-pink-50 transition border border-gray-100 hover:border-pink-200">
                    <div class="text-4xl mb-4">💇‍♀️</div>
                    <h3 class="font-bold text-gray-900">Salões</h3>
                </div>
                <div class="p-6 rounded-2xl bg-gray-50 hover:bg-green-50 transition border border-gray-100 hover:border-green-200">
                    <div class="text-4xl mb-4">💆‍♂️</div>
                    <h3 class="font-bold text-gray-900">Clínicas</h3>
                </div>
                <div class="p-6 rounded-2xl bg-gray-50 hover:bg-purple-50 transition border border-gray-100 hover:border-purple-200">
                    <div class="text-4xl mb-4">💅</div>
                    <h3 class="font-bold text-gray-900">Estética</h3>
                </div>
            </div>
        </div>
    </section>

    <!-- BENEFÍCIOS -->
    <section class="py-20 bg-gray-900 text-white">
        <div class="max-w-7xl mx-auto px-6">
            <div class="text-center mb-16">
                <h2 class="text-3xl lg:text-4xl font-bold mb-4">Tudo o que você precisa para crescer</h2>
            </div>
            
            <div class="grid md:grid-cols-3 gap-8">
                <div class="bg-gray-800 p-8 rounded-2xl border border-gray-700 hover:border-blue-500 transition group">
                    <div class="w-12 h-12 bg-blue-500/20 rounded-lg flex items-center justify-center mb-6 text-blue-400 group-hover:bg-blue-500 group-hover:text-white transition"><i class="bi bi-link-45deg text-2xl"></i></div>
                    <h3 class="text-xl font-bold mb-3">Link Personalizado</h3>
                    <p class="text-gray-400 text-sm leading-relaxed">Pare de perguntar "qual horário você quer?". Envie seu link (agendafacil/b/voce) e deixe o cliente escolher.</p>
                </div>
                <div class="bg-gray-800 p-8 rounded-2xl border border-gray-700 hover:border-green-500 transition group">
                    <div class="w-12 h-12 bg-green-500/20 rounded-lg flex items-center justify-center mb-6 text-green-400 group-hover:bg-green-500 group-hover:text-white transition"><i class="bi bi-clock-history text-2xl"></i></div>
                    <h3 class="text-xl font-bold mb-3">Agenda 24 horas</h3>
                    <p class="text-gray-400 text-sm leading-relaxed">Seu negócio aberto mesmo quando você está dormindo. Preencha horários vazios automaticamente.</p>
                </div>
                <div class="bg-gray-800 p-8 rounded-2xl border border-gray-700 hover:border-purple-500 transition group">
                    <div class="w-12 h-12 bg-purple-500/20 rounded-lg flex items-center justify-center mb-6 text-purple-400 group-hover:bg-purple-500 group-hover:text-white transition"><i class="bi bi-calendar-check text-2xl"></i></div>
                    <h3 class="text-xl font-bold mb-3">Controle Total</h3>
                    <p class="text-gray-400 text-sm leading-relaxed">Defina horários de almoço, dias de folga e duração de cada serviço. Você no comando da sua agenda.</p>
                </div>
            </div>
        </div>
    </section>

    <!-- CTA FINAL -->
    <section class="py-24 bg-blue-600 text-center">
        <div class="max-w-4xl mx-auto px-6">
            <h2 class="text-3xl lg:text-4xl font-bold text-white mb-8">Pronto para profissionalizar seu negócio?</h2>
            <a href="{{ url_for('register_business') }}" class="inline-block bg-white text-blue-600 px-10 py-4 rounded-full font-bold text-lg hover:bg-gray-100 transition shadow-lg">
                Criar Minha Conta Agora
            </a>
            <p class="mt-6 text-blue-200 text-sm">Configuração em menos de 2 minutos.</p>
        </div>
    </section>
</div>
{% endblock %}
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
            <div class="collapse navbar-collapse" id="nav">
                <ul class="navbar-nav ms-auto align-items-center">
                    {% if current_user.is_authenticated %}
                        <li class="nav-item"><a class="nav-link fw-bold" href="{{ url_for('admin_dashboard') }}">Painel</a></li>
                        <li class="nav-item"><a class="nav-link text-danger" href="{{ url_for('logout') }}">Sair</a></li>
                    {% else %}
                        <li class="nav-item"><a class="nav-link" href="{{ url_for('login') }}">Login</a></li>
                        <li class="nav-item ms-2"><a href="{{ url_for('register_business') }}" class="btn btn-primary btn-sm rounded-pill px-3">Criar Conta</a></li>
                    {% endif %}
                </ul>
            </div>
        </div>
    </nav>
    <main class="container-fluid p-0">
        {% with m = get_flashed_messages(with_categories=true) %}
            {% if m %}<div class="container mt-3">{% for c, msg in m %}<div class="alert alert-{{ c }} alert-dismissible fade show shadow-sm">{{ msg }} <button type="button" class="btn-close" data-bs-dismiss="alert"></button></div>{% endfor %}</div>{% endif %}
        {% endwith %}
        {% block content %}{% endblock %}
    </main>
    <footer class="bg-white border-top pt-8 pb-8 mt-auto">
        <div class="container text-center">
            <p class="text-gray-500 text-sm mb-2">© 2025 Agenda Fácil SaaS. Todos os direitos reservados.</p>
        </div>
    </footer>
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
    {% block scripts %}{% endblock %}
</body>
</html>
'''

REGISTER_HTML = r'''{% extends 'layout.html' %}
{% block title %}Criar Conta{% endblock %}
{% block content %}
<div class="row justify-content-center mt-5 mb-5">
    <div class="col-md-8 col-lg-6">
        <div class="card shadow-lg border-0 rounded-4 overflow-hidden">
            <div class="card-header bg-blue-600 text-white text-center py-4"><h3 class="fw-bold mb-0">Assine Agora</h3><p class="text-blue-100 text-sm mb-0">Plano Profissional: R$ 34,90/mês</p></div>
            <div class="card-body p-4 p-md-5 bg-white">
                <form method="POST" action="{{ url_for('register_business') }}">
                    <h5 class="mb-3 text-primary fw-bold small text-uppercase ls-1">Dados do Negócio</h5>
                    <div class="mb-3"><label class="form-label small fw-bold">Nome do Estabelecimento</label><input type="text" class="form-control" name="business_name" required></div>
                    <div class="mb-3"><label class="form-label small fw-bold">Link Personalizado</label><div class="input-group"><span class="input-group-text bg-light border-end-0">agendafacil.com/b/</span><input type="text" class="form-control border-start-0 ps-0" name="url_prefix" pattern="[a-z0-9-]+" required></div></div>
                    <div class="row g-2 mb-4">
                        <div class="col-md-6"><label class="form-label small fw-bold">WhatsApp</label><input type="text" class="form-control" name="contact_phone"></div>
                        <div class="col-md-6"><label class="form-label small fw-bold">E-mail para Notificações</label><input type="email" class="form-control" name="contact_email" required></div>
                    </div>
                    <h5 class="mb-3 text-primary fw-bold small text-uppercase ls-1 border-top pt-4">Acesso</h5>
                    <div class="row g-2">
                        <div class="col-md-6 mb-3"><label class="form-label small fw-bold">Usuário</label><input type="text" class="form-control" name="username" required></div>
                        <div class="col-md-6 mb-3"><label class="form-label small fw-bold">Senha</label><input type="password" class="form-control" name="password" required></div>
                    </div>
                    <button class="btn btn-primary w-100 py-3 fw-bold rounded-3 shadow-sm mt-2">Ir para Pagamento</button>
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
            <div class="text-center mt-4 border-top pt-3"><a href="{{ url_for('register_business') }}" class="text-decoration-none small text-muted">Não tem conta? <span class="text-blue-600 fw-bold">Assine já</span></a></div>
        </div>
    </div>
</div>
{% endblock %}
'''

ADMIN_HTML = r'''{% extends 'layout.html' %}
{% block title %}Painel Admin{% endblock %}
{% block content %}
<div class="container py-4">
    <div class="d-flex justify-content-between align-items-center mb-4">
        <div class="d-flex align-items-center gap-3">
            {% if establishment.logo_filename %}<img src="{{ url_for('static', filename='uploads/' + establishment.logo_filename) }}" class="rounded-circle shadow-sm border border-2 border-white" style="width: 50px; height: 50px; object-fit: cover;">
            {% else %}<div class="rounded-circle bg-secondary d-flex align-items-center justify-content-center text-white fw-bold" style="width: 60px; height: 60px;">Logo</div>{% endif %}
            <div><h1 class="h3 mb-0">Painel: {{ establishment.name }}</h1><a href="{{ url_for('establishment_services', url_prefix=establishment.url_prefix) }}" target="_blank" class="text-decoration-none small">Ver Página <i class="bi bi-box-arrow-up-right"></i></a></div>
        </div>
    </div>
    {% if today_count > 0 %}
    <div class="alert alert-info d-flex align-items-center mb-4 shadow-sm" role="alert"><i class="bi bi-bell-fill me-2 fs-4"></i><div><strong>Atenção!</strong> Você tem <strong>{{ today_count }}</strong> agendamento(s) para hoje.</div></div>
    {% endif %}
    <div class="card shadow-sm border-0 mb-4 p-3 bg-light">
        <form action="{{ url_for('update_settings') }}" method="POST" enctype="multipart/form-data" class="row align-items-center g-2">
            <input type="hidden" name="form_type" value="contact"> 
            <div class="col-md-3"><label class="small fw-bold">WhatsApp:</label><input type="text" name="contact_phone" class="form-control form-control-sm" value="{{ establishment.contact_phone or '' }}"></div>
            <div class="col-md-4"><label class="small fw-bold">E-mail (Notificações):</label><input type="email" name="contact_email" class="form-control form-control-sm" value="{{ establishment.contact_email or '' }}"></div>
            <div class="col-md-3"><label class="small fw-bold">Logo:</label><input type="file" name="logo" class="form-control form-control-sm" accept="image/*"></div>
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
                            <table class="table table-bordered mb-0 align-middle text-center">
                                <thead class="table-light"><tr><th style="width: 50px;">Ativo</th><th>Dia</th><th>Abertura</th><th>Fechamento</th><th>Almoço Início</th><th>Almoço Fim</th></tr></thead>
                                <tbody>
                                    {% set day_names = ['Segunda', 'Terça', 'Quarta', 'Quinta', 'Sexta', 'Sábado', 'Domingo'] %}
                                    {% for d in schedules %}
                                    <tr class="{% if not d.is_active %}bg-light text-muted{% endif %}">
                                        <input type="hidden" name="schedule_id" value="{{ d.id }}">
                                        <td><div class="form-check d-flex justify-content-center"><input class="form-check-input" type="checkbox" name="active_{{ d.id }}" {% if d.is_active %}checked{% endif %}></div></td>
                                        <td class="fw-bold text-start">{{ day_names[d.day_index] }}</td>
                                        <td><input type="time" class="form-control form-control-sm" name="work_start_{{ d.id }}" value="{{ d.work_start.strftime('%H:%M') }}"></td>
                                        <td><input type="time" class="form-control form-control-sm" name="work_end_{{ d.id }}" value="{{ d.work_end.strftime('%H:%M') }}"></td>
                                        <td><input type="time" class="form-control form-control-sm" name="lunch_start_{{ d.id }}" value="{{ d.lunch_start.strftime('%H:%M') if d.lunch_start else '' }}"></td>
                                        <td><input type="time" class="form-control form-control-sm" name="lunch_end_{{ d.id }}" value="{{ d.lunch_end.strftime('%H:%M') if d.lunch_end else '' }}"></td>
                                    </tr>
                                    {% endfor %}
                                </tbody>
                            </table>
                        </div>
                        <div class="p-3 bg-light border-top text-end"><button class="btn btn-success fw-bold px-4">Salvar Horários</button></div>
                    </form>
                </div>
            </div>
        </div>
        <div class="col-lg-6">
            <div class="card shadow-sm border-0 mb-4">
                <div class="card-header bg-white fw-bold">Próximos Agendamentos</div>
                <div class="card-body p-0">
                    <table class="table table-hover mb-0">
                        <thead><tr><th>Data/Hora</th><th>Cliente</th><th>Ação</th></tr></thead>
                        <tbody>
                            {% for a in appointments %}
                            <tr>
                                <td><b>{{ a.appointment_date.strftime('%d/%m') }}</b> {{ a.appointment_time.strftime('%H:%M') }}<br><small>{{ a.service_info.name }}</small></td>
                                <td>{{ a.client_name }}<br><small class="text-success"><i class="bi bi-whatsapp"></i> {{ a.client_phone }}</small></td>
                                <td><form method="POST" action="{{ url_for('delete_appointment', id=a.id) }}" onsubmit="return confirm('Cancelar?');"><button class="btn btn-sm btn-danger"><i class="bi bi-trash"></i></button></form></td>
                            </tr>
                            {% else %}<tr><td colspan="3" class="text-center py-4">Agenda livre.</td></tr>{% endfor %}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
        <div class="col-lg-6">
            <div class="card shadow-sm border-0">
                <div class="card-header bg-white fw-bold">Serviços</div>
                <div class="card-body">
                    <form action="{{ url_for('add_service') }}" method="POST" class="mb-3">
                        <div class="input-group input-group-sm">
                            <input type="text" name="name" class="form-control" placeholder="Nome (ex: Corte)" required>
                            <input type="number" name="duration" class="form-control" placeholder="Min" style="max-width: 70px;" required>
                            <input type="text" name="price" class="form-control" placeholder="R$" style="max-width: 100px;" required>
                            <button class="btn btn-success">+</button>
                        </div>
                    </form>
                    <ul class="list-group list-group-flush small">
                        {% for s in services %}
                        <li class="list-group-item d-flex justify-content-between px-0"><span>{{ s.name }} ({{ s.duration }}min) - <span class="fw-bold text-success">R$ {{ "%.2f"|format(s.price) }}</span></span><form method="POST" action="{{ url_for('delete_service', id=s.id) }}" onsubmit="return confirm('Excluir?');"><button class="btn btn-link text-danger p-0 border-0"><i class="bi bi-trash"></i></button></form></li>
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
{% block title %}{{ establishment.name }}{% endblock %}
{% block content %}
<div class="container py-5">
    <div class="text-center mb-5">
        {% if establishment.logo_filename %}<img src="{{ url_for('static', filename='uploads/' + establishment.logo_filename) }}" class="rounded-circle shadow mb-3" style="width: 100px; height: 100px; object-fit: cover;">
        {% else %}<div class="rounded-circle bg-secondary d-inline-flex align-items-center justify-content-center text-white fw-bold mb-3 shadow" style="width: 100px; height: 100px; font-size: 2rem;">{{ establishment.name[0] }}</div>{% endif %}
        <h1 class="display-5 fw-bold">{{ establishment.name }}</h1>
        {% if establishment.contact_phone %}<p class="text-muted"><i class="bi bi-whatsapp text-success"></i> {{ establishment.contact_phone }}</p>{% endif %}
    </div>
    <div class="row justify-content-center gap-3">
        {% for s in services %}
        <div class="col-md-4">
            <div class="card shadow-sm border-0 h-100 p-3 text-center">
                <h4 class="fw-bold">{{ s.name }}</h4>
                <p class="text-success fw-bold">R$ {{ "%.2f"|format(s.price) }}</p>
                <p class="text-muted"><i class="bi bi-clock"></i> {{ s.duration }} min</p>
                <a href="{{ url_for('schedule_service', url_prefix=establishment.url_prefix, service_id=s.id) }}" class="btn btn-outline-primary w-100 fw-bold">Agendar</a>
            </div>
        </div>
        {% else %}<div class="text-center text-muted">Sem serviços cadastrados.</div>{% endfor %}
    </div>
</div>
{% endblock %}
'''

AGENDAMENTO_HTML = r'''{% extends 'layout.html' %}
{% block title %}Agendar{% endblock %}
{% block content %}
<div class="container py-5">
    <div class="row justify-content-center">
        <div class="col-lg-6">
            <div class="card shadow-sm border-0 p-4">
                <div class="text-center mb-4">
                    {% if establishment.logo_filename %}<img src="{{ url_for('static', filename='uploads/' + establishment.logo_filename) }}" class="rounded-circle shadow-sm mb-2" style="width: 60px; height: 60px; object-fit: cover;">{% endif %}
                    <h4 class="fw-bold">{{ establishment.name }}</h4>
                    <h5 class="text-muted">{{ service.name }}</h5>
                    <p class="text-success fw-bold">Valor: R$ {{ "%.2f"|format(service.price) }}</p>
                </div>
                <form id="form" method="POST" action="{{ url_for('create_appointment', url_prefix=establishment.url_prefix) }}">
                    <input type="hidden" name="service_id" value="{{ service.id }}">
                    <div class="mb-2"><label class="fw-bold small">Seu Nome</label><input type="text" name="client_name" class="form-control" required></div>
                    <div class="row g-2 mb-3">
                        <div class="col-6"><label class="fw-bold small">WhatsApp</label><input type="tel" name="client_phone" class="form-control" required></div>
                        <div class="col-6"><label class="fw-bold small">Seu E-mail</label><input type="email" name="client_email" class="form-control" placeholder="Para confirmação" required></div>
                    </div>
                    <div class="mb-3"><label class="fw-bold small">Data</label><input type="date" id="date" name="appointment_date" class="form-control" required></div>
                    <div class="mb-4"><label class="fw-bold small">Horários Disponíveis</label><div id="slots" class="d-flex flex-wrap gap-2 mt-2"><small class="text-muted">Selecione a data...</small></div><input type="hidden" id="time" name="appointment_time" required></div>
                    <button id="btn" class="btn btn-primary w-100 fw-bold" disabled>Confirmar Agendamento</button>
                </form>
            </div>
        </div>
    </div>
</div>
{% endblock %}
{% block scripts %}
<script>
const today = new Date();
const year = today.getFullYear();
const month = String(today.getMonth() + 1).padStart(2, '0');
const day = String(today.getDate()).padStart(2, '0');
document.getElementById('date').min = `${year}-${month}-${day}`;

document.getElementById('date').addEventListener('change', async (e) => {
    if(!e.target.value) return;
    const div = document.getElementById('slots');
    div.innerHTML = 'Carregando...';
    const res = await fetch(`/api/horarios_disponiveis?service_id={{ service.id }}&date=${e.target.value}`);
    const times = await res.json();
    div.innerHTML = '';
    if(times.length === 0) div.innerHTML = '<span class="text-danger small">Indisponível.</span>';
    times.forEach(t => {
        const b = document.createElement('button');
        b.type='button'; b.className='btn btn-outline-dark btn-sm'; b.innerText=t;
        b.onclick = () => {
            document.querySelectorAll('#slots button').forEach(x=>x.classList.replace('btn-dark','btn-outline-dark'));
            b.classList.replace('btn-outline-dark','btn-dark');
            document.getElementById('time').value=t;
            document.getElementById('btn').disabled=false;
        };
        div.appendChild(b);
    });
});
</script>
{% endblock %}
'''

# --- NOVA TELA DE SUCESSO (COM BOTÃO ZAP) ---
SUCCESS_APPOINTMENT_HTML = r'''{% extends 'layout.html' %}
{% block title %}Sucesso{% endblock %}
{% block content %}
<div class="container py-5 text-center">
    <div class="card shadow-lg border-0 rounded-4 p-5 max-w-lg mx-auto">
        <div class="mb-4 text-success display-1"><i class="bi bi-check-circle-fill"></i></div>
        <h2 class="fw-bold text-gray-800">Agendamento Confirmado!</h2>
        <p class="text-muted mb-4">Enviamos um e-mail de confirmação para você.</p>
        
        <div class="bg-light p-3 rounded-3 mb-4 text-start">
            <p class="mb-1"><strong>Serviço:</strong> {{ appointment.service_info.name }}</p>
            <p class="mb-1"><strong>Data:</strong> {{ appointment.appointment_date.strftime('%d/%m/%Y') }}</p>
            <p class="mb-0"><strong>Horário:</strong> {{ appointment.appointment_time.strftime('%H:%M') }}</p>
        </div>

        {% if zap_link != "#" %}
        <a href="{{ zap_link }}" target="_blank" class="btn btn-success w-100 py-3 fw-bold rounded-pill mb-2">
            <i class="bi bi-whatsapp"></i> Confirmar no WhatsApp do Profissional
        </a>
        <small class="text-muted d-block">Clique acima para enviar uma mensagem direta.</small>
        {% endif %}
        
        <div class="mt-4">
            <a href="{{ url_for('establishment_services', url_prefix=appointment.establishment.url_prefix) }}" class="text-decoration-none">Voltar ao Início</a>
        </div>
    </div>
</div>
{% endblock %}
'''

ERROR_INACTIVE_HTML = r'''{% extends 'layout.html' %}
{% block content %}
<div class="container py-5 text-center">
    <div class="alert alert-warning py-5">
        <h1 class="display-4 fw-bold mb-3"><i class="bi bi-cone-striped"></i></h1>
        <h2>Estabelecimento Indisponível</h2>
        <p class="lead text-muted">{{ message }}</p>
    </div>
</div>
{% endblock %}
'''

def atualizar_sistema():
    if not os.path.exists('templates'): os.makedirs('templates')
    uploads_path = os.path.join('static', 'uploads')
    if not os.path.exists(uploads_path): os.makedirs(uploads_path)
    
    if os.path.exists('agendamento.db'):
        try: os.remove('agendamento.db')
        except: pass

    arquivos = {
        'app.py': APP_PY,
        'requirements.txt': REQUIREMENTS_TXT,
        'Procfile': PROCFILE,
        'templates/layout.html': LAYOUT_HTML,
        'templates/index.html': INDEX_HTML,
        'templates/register.html': REGISTER_HTML,
        'templates/login.html': LOGIN_HTML,
        'templates/admin.html': ADMIN_HTML,
        'templates/lista_servicos.html': LISTA_SERVICOS_HTML,
        'templates/agendamento.html': AGENDAMENTO_HTML,
        'templates/success_appointment.html': SUCCESS_APPOINTMENT_HTML,
        'templates/error_inactive.html': ERROR_INACTIVE_HTML
    }

    for caminho, conteudo in arquivos.items():
        with open(caminho, 'w', encoding='utf-8') as f:
            f.write(conteudo.strip())
        print(f"Atualizado: {caminho}")

    print("\n[INFO] Instalando dependências...")
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])
        print("[SUCESSO] Dependências instaladas!")
    except Exception as e:
        print(f"[ERRO] Instale manualmente: pip install -r requirements.txt")

    print("\n[SUCESSO] Sistema V33 Final instalado!")
    print("Execute: python app.py")

if __name__ == "__main__":
    atualizar_sistema()