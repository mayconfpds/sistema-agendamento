import os
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
STRIPE_PRICE_SOLO = os.environ.get('STRIPE_PRICE_SOLO')
STRIPE_PRICE_GESTAO = os.environ.get('STRIPE_PRICE_GESTAO')

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
    plan_type = db.Column(db.String(20), default='solo')
    
    trial_ends = db.Column(db.DateTime, nullable=True)
    
    loyalty_points_goal = db.Column(db.Integer, default=0)
    loyalty_reward = db.Column(db.String(150), nullable=True)
    
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
    
    @property
    def rating_count(self):
        return Appointment.query.filter_by(establishment_id=self.id).filter(Appointment.rating != None).count()

    @property
    def average_rating(self):
        appts = Appointment.query.filter_by(establishment_id=self.id).filter(Appointment.rating != None).all()
        if not appts: return 0
        return sum(a.rating for a in appts) / len(appts)

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
    pause2_start = db.Column(db.Time, nullable=True)
    pause2_end = db.Column(db.Time, nullable=True)

class Admin(UserMixin, db.Model):
    __tablename__ = 'admins'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    establishment_id = db.Column(db.Integer, db.ForeignKey('establishments.id'), nullable=False)
    def set_password(self, password): self.password_hash = generate_password_hash(password)
    def check_password(self, password): return check_password_hash(self.password_hash, password)

class Category(db.Model):
    __tablename__ = 'categories'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    establishment_id = db.Column(db.Integer, db.ForeignKey('establishments.id'), nullable=False)
    services = db.relationship('Service', backref='category', lazy=True)

class Professional(db.Model):
    __tablename__ = 'professionals'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    commission_rate = db.Column(db.Float, default=0.0) 
    is_active = db.Column(db.Boolean, default=True)
    establishment_id = db.Column(db.Integer, db.ForeignKey('establishments.id'), nullable=False)
    appointments = db.relationship('Appointment', backref='professional', lazy=True)

class Service(db.Model):
    __tablename__ = 'services'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    duration = db.Column(db.Integer, nullable=False)
    price = db.Column(db.Float, nullable=False, default=0.0)
    establishment_id = db.Column(db.Integer, db.ForeignKey('establishments.id'), nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey('categories.id'), nullable=True)
    is_combo = db.Column(db.Boolean, default=False)
    original_price = db.Column(db.Float, nullable=True)

class Client(db.Model):
    __tablename__ = 'clients'
    id = db.Column(db.Integer, primary_key=True)
    phone = db.Column(db.String(20), nullable=False)
    name = db.Column(db.String(150), nullable=False)
    points = db.Column(db.Integer, default=0)
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
    status = db.Column(db.String(20), default='pendente')
    professional_id = db.Column(db.Integer, db.ForeignKey('professionals.id'), nullable=True)
    commission_value = db.Column(db.Float, default=0.0)
    rating = db.Column(db.Integer, nullable=True)
    services = db.relationship('Service', secondary=appointment_services, lazy='subquery', backref=db.backref('appointments', lazy=True))

    @property
    def service_names(self):
        return " + ".join([s.name for s in self.services])
    
    @property
    def client_loyalty(self):
        return Client.query.filter_by(establishment_id=self.establishment_id, phone=self.client_phone).first()

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
                upcoming = Appointment.query.filter(Appointment.notified == False, Appointment.status == 'pendente').all()
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
        inspector = inspect(db.engine)
        if not inspector.has_table("establishments"): 
            db.create_all()
        else:
            columns = [c['name'] for c in inspector.get_columns('day_schedules')]
            if 'pause2_start' not in columns:
                with db.engine.connect() as conn:
                    conn.execute(db.text('ALTER TABLE day_schedules ADD COLUMN pause2_start TIME;'))
                    conn.execute(db.text('ALTER TABLE day_schedules ADD COLUMN pause2_end TIME;'))
                    conn.commit()
            columns_appt = [c['name'] for c in inspector.get_columns('appointments')]
            if 'status' not in columns_appt:
                with db.engine.connect() as conn:
                    conn.execute(db.text("ALTER TABLE appointments ADD COLUMN status VARCHAR(20) DEFAULT 'pendente';"))
                    conn.execute(db.text("ALTER TABLE appointments ADD COLUMN rating INTEGER;"))
                    conn.commit()
            columns_est = [c['name'] for c in inspector.get_columns('establishments')]
            if 'loyalty_points_goal' not in columns_est:
                with db.engine.connect() as conn:
                    conn.execute(db.text("ALTER TABLE establishments ADD COLUMN loyalty_points_goal INTEGER DEFAULT 0;"))
                    conn.execute(db.text("ALTER TABLE establishments ADD COLUMN loyalty_reward VARCHAR(150);"))
                    conn.commit()
            if not inspector.has_table("clients"):
                Client.__table__.create(db.engine)
            if not inspector.has_table("categories"):
                Category.__table__.create(db.engine)
            columns_srv = [c['name'] for c in inspector.get_columns('services')]
            if 'category_id' not in columns_srv:
                with db.engine.connect() as conn:
                    conn.execute(db.text("ALTER TABLE services ADD COLUMN category_id INTEGER;"))
                    conn.commit()
            if 'is_combo' not in columns_srv:
                with db.engine.connect() as conn:
                    conn.execute(db.text("ALTER TABLE services ADD COLUMN is_combo BOOLEAN DEFAULT FALSE;"))
                    conn.execute(db.text("ALTER TABLE services ADD COLUMN original_price FLOAT;"))
                    conn.commit()
            columns_est = [c['name'] for c in inspector.get_columns('establishments')]
            if 'plan_type' not in columns_est:
                with db.engine.connect() as conn:
                    conn.execute(db.text("ALTER TABLE establishments ADD COLUMN plan_type VARCHAR(20) DEFAULT 'solo';"))
                    conn.commit()
            if not inspector.has_table("professionals"):
                Professional.__table__.create(db.engine)
            columns_appt = [c['name'] for c in inspector.get_columns('appointments')]
            if 'professional_id' not in columns_appt:
                with db.engine.connect() as conn:
                    conn.execute(db.text("ALTER TABLE appointments ADD COLUMN professional_id INTEGER;"))
                    conn.execute(db.text("ALTER TABLE appointments ADD COLUMN commission_value FLOAT DEFAULT 0.0;"))
                    conn.commit()
                for est in Establishment.query.all():
                    geral = Category.query.filter_by(establishment_id=est.id, name='Geral').first()
                    if not geral:
                        geral = Category(name='Geral', establishment_id=est.id)
                        db.session.add(geral)
                        db.session.commit()
                    Service.query.filter_by(establishment_id=est.id, category_id=None).update({'category_id': geral.id})
                db.session.commit()
except Exception as e:
    print(f"Erro na verificação do banco: {e}") 

if not os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
    threading.Thread(target=notification_worker, daemon=True).start()

# --- ROTAS DE PAGAMENTO ---
@app.route('/pagamento')
@login_required
def payment():
    if current_user.establishment.is_active: 
        return redirect(url_for('admin_dashboard'))
    
    plan_chosen = request.args.get('plan')
    if plan_chosen in ['solo', 'gestao']:
        current_user.establishment.plan_type = plan_chosen
        db.session.commit()
    
    try:
        success_url = request.host_url.replace('http://', 'https://') + 'pagamento/sucesso'
        cancel_url = request.host_url.replace('http://', 'https://') + 'pagamento/cancelado'

        if current_user.establishment.plan_type == 'gestao':
            price_id = STRIPE_PRICE_GESTAO
        else:
            price_id = STRIPE_PRICE_SOLO

        if not price_id:
            return "Erro: Os IDs da Stripe não foram configurados no Render.", 500

        session = stripe.checkout.Session.create(
            payment_method_types=['card'], 
            line_items=[{'price': price_id, 'quantity': 1}],
            mode='subscription', 
            allow_promotion_codes=True, 
            success_url=success_url,
            cancel_url=cancel_url, 
            customer_email=current_user.establishment.contact_email,
        )
        return redirect(session.url, code=303)
    except Exception as e:
        return f"<div style='font-family:sans-serif; padding:40px; text-align:center;'> <h2 style='color:red;'>Erro na comunicação com a Stripe</h2> <p style='background:#f4f4f4; padding:15px; border-radius:8px;'>Detalhe do Erro: <b>{str(e)}</b></p> <a href='/logout'>Sair</a> </div>", 500

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

@app.route('/planos')
def planos():
    if current_user.is_authenticated and current_user.establishment.has_access:
        return redirect(url_for('admin_dashboard'))
    return render_template('planos.html')

@app.route('/cadastro-negocio', methods=['GET', 'POST'])
def register_business():
    if current_user.is_authenticated: return redirect(url_for('admin_dashboard'))
    if request.method == 'POST':
        username = request.form.get('username')
        is_master = (username == 'admin_demo') 
        
        # Correção 2: Puxar o plano com blindagem extra (do form ou da url)
        plan_chosen = request.form.get('plan_type') or request.args.get('plan') or 'solo'
        
        est = Establishment(
            name=request.form.get('business_name'), 
            url_prefix=request.form.get('url_prefix').lower().strip(), 
            contact_phone=request.form.get('contact_phone'), 
            contact_email=request.form.get('contact_email'), 
            is_active=is_master, 
            capacity=1,
            plan_type=plan_chosen,
            trial_ends=get_now_brazil() + timedelta(days=7)
        )
        db.session.add(est); db.session.commit()
        for i in range(7): db.session.add(DaySchedule(establishment_id=est.id, day_index=i, is_active=(i < 5), work_start=time(9,0), work_end=time(18,0)))
        adm = Admin(username=username, establishment_id=est.id)
        adm.set_password(request.form.get('password'))
        db.session.add(adm); db.session.commit()
        
        login_user(adm)
        return redirect(url_for('admin_dashboard'))
        
    return render_template('register.html')

@app.route('/b/<url_prefix>')
def establishment_services(url_prefix):
    est = Establishment.query.filter_by(url_prefix=url_prefix).first_or_404()
    if not est.has_access: return render_template('error_inactive.html', message="O período de teste ou assinatura deste estabelecimento expirou."), 403
    services = Service.query.filter_by(establishment_id=est.id).order_by(Service.name).all()
    categories = Category.query.filter_by(establishment_id=est.id).all()
    return render_template('lista_servicos.html', services=services, categories=categories, establishment=est)

@app.route('/b/<url_prefix>/agendar/<int:service_id>')
def schedule_service(url_prefix, service_id):
    est = Establishment.query.filter_by(url_prefix=url_prefix).first_or_404()
    if not est.has_access: return "Inativo", 403
    main_service = Service.query.get_or_404(service_id)
    other_services = Service.query.filter(Service.establishment_id == est.id, Service.id != service_id).order_by(Service.name).all()
    
    professionals = []
    if est.plan_type == 'gestao':
        professionals = Professional.query.filter_by(establishment_id=est.id).all()
        
    return render_template('agendamento.html', main_service=main_service, other_services=other_services, establishment=est, professionals=professionals)

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
            
     # NOVA LÓGICA: Permite até 4 agendamentos simultâneos (ex: Pai + 3 filhos)
    futuros_pendentes = [a for a in user_appts if datetime.combine(a.appointment_date, a.appointment_time) >= now and a.status == 'pendente']
                        
    if len(futuros_pendentes) >= 4:
        flash('Limite de horários simultâneos atingido (Máx: 4). Conclua os agendamentos atuais ou contacte o estabelecimento.', 'warning')
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

    prof_id = request.form.get('professional_id')
    professional_id = None
    commission_value = 0.0
    
    if prof_id and prof_id != 'any':
        prof = Professional.query.get(int(prof_id))
        if prof and prof.establishment_id == est.id:
            professional_id = prof.id
            commission_value = total_price * (prof.commission_rate / 100.0)

    appts_on_day = Appointment.query.filter_by(appointment_date=d, establishment_id=est.id).filter(Appointment.status == 'pendente')
    if professional_id:
        appts_on_day = appts_on_day.filter_by(professional_id=professional_id)
    appts_on_day = appts_on_day.all()
    
    overlap_count = 0
    for a in appts_on_day:
        s = datetime.combine(d, a.appointment_time)
        e = s + timedelta(minutes=a.total_duration)
        if max(start_dt, s) < min(end_dt, e):
            overlap_count += 1
            
    current_capacity = 1 if professional_id else est.capacity
    if overlap_count >= current_capacity:
        flash('Ops! Esse horário acabou de ser ocupado. Tente outro.', 'danger')
        return redirect(url_for('schedule_service', url_prefix=url_prefix, service_id=selected_services[0].id))

    appt = Appointment(client_name=request.form.get('client_name'), client_phone=client_phone, client_email=request.form.get('client_email'), appointment_date=d, appointment_time=t, establishment_id=est.id, total_duration=total_dur, total_price=total_price, professional_id=professional_id, commission_value=commission_value)
    for s in selected_services: appt.services.append(s)
    
    db.session.add(appt); db.session.commit()
    
    send_email(f"Confirmado: {est.name}", appt.client_email, f"Agendado para {d.strftime('%d/%m')} às {t.strftime('%H:%M')}.\n\n Caso precise cancelar entre em contato com o estabelecimento com antecedência")
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
            if not adm.establishment.has_access: return redirect(url_for('planos'))
            return redirect(url_for('admin_dashboard'))
        flash('Login inválido.', 'danger')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout(): logout_user(); return redirect(url_for('login'))

@app.route('/admin')
@login_required
def admin_dashboard():
    if not current_user.establishment.has_access: return redirect(url_for('planos'))
    est = current_user.establishment
    today = get_now_brazil().date()
    
    # --- NOVOS FILTROS DE BUSCA E DATA ---
    search_query = request.args.get('search', '').strip()
    filter_date_str = request.args.get('filter_date', '')
    
    query = Appointment.query.filter(
        Appointment.establishment_id == est.id,
        Appointment.status.notin_(['arquivado', 'falta', 'cancelado'])
    )
    
    # Aplica o filtro de data (Se escolheu uma data, mostra só ela)
    if filter_date_str:
        try:
            filter_date = datetime.strptime(filter_date_str, '%Y-%m-%d').date()
            query = query.filter(Appointment.appointment_date == filter_date)
        except: pass
    elif not search_query:
        # Padrão Inteligente: Se não pesquisou um nome específico, mostra só de hoje em diante
        query = query.filter(Appointment.appointment_date >= today)
        
    # Aplica o filtro de texto (Nome ou WhatsApp)
    if search_query:
        search_term = f"%{search_query}%"
        query = query.filter(db.or_(
            Appointment.client_name.ilike(search_term), 
            Appointment.client_phone.ilike(search_term)
        ))
        
    # ORDENAÇÃO PERFEITA: Primeiro a Data (mais próxima), depois o Horário (mais cedo)
    appts = query.order_by(Appointment.appointment_date.asc(), Appointment.appointment_time.asc()).all()
    
    services = Service.query.filter_by(establishment_id=est.id).all()
    categories = Category.query.filter_by(establishment_id=est.id).all()
    schedules = DaySchedule.query.filter_by(establishment_id=est.id).order_by(DaySchedule.day_index).all()
    blacklists = Blacklist.query.filter_by(establishment_id=est.id).all()
    professionals = Professional.query.filter_by(establishment_id=est.id).all() 
    today_count = Appointment.query.filter(Appointment.establishment_id == est.id, Appointment.appointment_date == today).count()
    
    return render_template('admin.html', appointments=appts, services=services, categories=categories, establishment=est, schedules=schedules, blacklists=blacklists, professionals=professionals, today_count=today_count)

@app.route('/admin/historico')
@login_required
def historico_atendimentos():
    if not current_user.establishment.has_access: return redirect(url_for('planos'))
    est = current_user.establishment
    
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')
    
    query = Appointment.query.filter_by(establishment_id=est.id)
    
    if start_date_str:
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
            query = query.filter(Appointment.appointment_date >= start_date)
        except: pass
    if end_date_str:
        try:
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
            query = query.filter(Appointment.appointment_date <= end_date)
        except: pass
        
    appts = query.order_by(Appointment.appointment_date.desc(), Appointment.appointment_time.desc()).all()
    total_revenue = sum(a.total_price for a in appts if a.status in ['concluido', 'arquivado'])
    total_appts = len(appts)
    
    return render_template('historico.html', appointments=appts, establishment=est, start_date=start_date_str, end_date=end_date_str, total_revenue=total_revenue, total_appts=total_appts)

@app.route('/admin/relatorios')
@login_required
def relatorios_gerenciais():
    if not current_user.establishment.has_access: return redirect(url_for('planos'))
    est = current_user.establishment
    
    hoje = get_now_brazil().date()
    start_date_str = request.args.get('start_date', (hoje - timedelta(days=30)).strftime('%Y-%m-%d'))
    end_date_str = request.args.get('end_date', hoje.strftime('%Y-%m-%d'))
    
    try:
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
    except:
        start_date = hoje - timedelta(days=30); end_date = hoje

    appts = Appointment.query.filter(
        Appointment.establishment_id == est.id,
        Appointment.appointment_date >= start_date,
        Appointment.appointment_date <= end_date
    ).all()

    concluidos = [a for a in appts if a.status in ['concluido', 'arquivado']]
    faltas = [a for a in appts if a.status == 'falta']
    
    faturamento_total = sum(a.total_price for a in concluidos)
    qtd_concluidos = len(concluidos)
    ticket_medio = faturamento_total / qtd_concluidos if qtd_concluidos > 0 else 0
    
    qtd_total_marcacoes = len(appts)
    taxa_faltas = (len(faltas) / qtd_total_marcacoes * 100) if qtd_total_marcacoes > 0 else 0
    
    avaliacoes = [a.rating for a in concluidos if a.rating]
    avaliacao_media = sum(avaliacoes) / len(avaliacoes) if avaliacoes else 0.0
    qtd_avaliacoes = len(avaliacoes)

    total_comissoes = 0
    comissoes_por_profissional = {}
    
    if est.plan_type == 'gestao':
        for a in concluidos:
            if a.professional_id:
                # Correção 3: Auto-calcula comissões retroativas de atendimentos já feitos
                if (a.commission_value is None or a.commission_value == 0.0) and a.professional and a.professional.commission_rate > 0:
                    a.commission_value = a.total_price * (a.professional.commission_rate / 100.0)
                    db.session.commit()
                
                if a.commission_value > 0:
                    total_comissoes += a.commission_value
                    prof_name = a.professional.name if a.professional else "Desconhecido"
                    if prof_name not in comissoes_por_profissional:
                        comissoes_por_profissional[prof_name] = 0
                    comissoes_por_profissional[prof_name] += a.commission_value
                
    receita_liquida = faturamento_total - total_comissoes

    dias_diferenca = (end_date - start_date).days
    faturamento_tempo = {}
    
    for a in concluidos:
        data = a.appointment_date
        if dias_diferenca <= 31: chave = data.strftime('%d/%m') 
        elif dias_diferenca <= 90: chave = f"Semana {data.isocalendar()[1]}" 
        else: chave = data.strftime('%b/%Y') 
        faturamento_tempo[chave] = faturamento_tempo.get(chave, 0) + a.total_price

    labels_tempo = list(faturamento_tempo.keys())
    dados_tempo = list(faturamento_tempo.values())

    ranking_servicos = {}
    for a in concluidos:
        for s in a.services:
            if s.name not in ranking_servicos:
                ranking_servicos[s.name] = {'qtd': 0, 'receita': 0}
            ranking_servicos[s.name]['qtd'] += 1
            ranking_servicos[s.name]['receita'] += s.price

    servicos_top = sorted(ranking_servicos.items(), key=lambda x: x[1]['receita'], reverse=True)[:5]
    labels_servicos = [s[0] for s in servicos_top]
    dados_servicos = [s[1]['receita'] for s in servicos_top]

    saude_labels = ['Concluídos', 'Cancelados', 'Faltas']
    saude_dados = [
        len(concluidos),
        len([a for a in appts if a.status == 'cancelado']),
        len(faltas)
    ]

    return render_template(
        'relatorios.html', 
        establishment=est, start_date=start_date_str, end_date=end_date_str,
        faturamento_total=faturamento_total, ticket_medio=ticket_medio, 
        qtd_concluidos=qtd_concluidos, taxa_faltas=taxa_faltas,
        avaliacao_media=avaliacao_media, qtd_avaliacoes=qtd_avaliacoes,
        labels_tempo=labels_tempo, dados_tempo=dados_tempo,
        labels_servicos=labels_servicos, dados_servicos=dados_servicos,
        saude_labels=saude_labels, saude_dados=saude_dados,
        total_comissoes=total_comissoes, receita_liquida=receita_liquida, comissoes_por_profissional=comissoes_por_profissional
    )

@app.route('/admin/configurar', methods=['POST'])
@login_required
def update_settings():
    if not current_user.establishment.has_access: return redirect(url_for('planos'))
    est = current_user.establishment
    ft = request.form.get('form_type')
    if ft == 'contact':
        est.contact_phone = request.form.get('contact_phone')
        est.contact_email = request.form.get('contact_email')
        if est.plan_type == 'solo':
            try: est.capacity = int(request.form.get('capacity', 1))
            except: pass
        try: est.loyalty_points_goal = int(request.form.get('loyalty_points_goal', 0))
        except: est.loyalty_points_goal = 0
        est.loyalty_reward = request.form.get('loyalty_reward')
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
                p2s, p2e = request.form.get(f'pause2_start_{sid}'), request.form.get(f'pause2_end_{sid}')
                if ws and we: ds.work_start = datetime.strptime(ws, '%H:%M').time(); ds.work_end = datetime.strptime(we, '%H:%M').time()
                if ls and le: ds.lunch_start = datetime.strptime(ls, '%H:%M').time(); ds.lunch_end = datetime.strptime(le, '%H:%M').time()
                else: ds.lunch_start = None; ds.lunch_end = None
                if p2s and p2e: ds.pause2_start = datetime.strptime(p2s, '%H:%M').time(); ds.pause2_end = datetime.strptime(p2e, '%H:%M').time()
                else: ds.pause2_start = None; ds.pause2_end = None
        flash('Horários atualizados!', 'success')
    db.session.commit()
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/categoria/adicionar', methods=['POST'])
@login_required
def add_category():
    name = request.form.get('name')
    if name:
        c = Category(name=name, establishment_id=current_user.establishment_id)
        db.session.add(c); db.session.commit()
        flash('Categoria criada com sucesso!', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/categoria/excluir/<int:id>', methods=['POST'])
@login_required
def delete_category(id):
    c = Category.query.get_or_404(id)
    if c.establishment_id == current_user.establishment_id:
        geral = Category.query.filter_by(establishment_id=current_user.establishment_id, name='Geral').first()
        if c.id == geral.id:
            flash('A categoria Geral não pode ser excluída.', 'danger')
        else:
            Service.query.filter_by(category_id=c.id).update({'category_id': geral.id})
            db.session.delete(c); db.session.commit()
            flash('Categoria excluída. Os serviços foram movidos para a categoria Geral.', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/servico/adicionar', methods=['POST'])
@login_required
def add_service():
    name = request.form.get('name')
    price = request.form.get('price')
    duration = request.form.get('duration')
    category_id = request.form.get('category_id')
    is_combo = True if request.form.get('is_combo') == 'on' else False
    original_price = request.form.get('original_price')
    
    if name and price and duration and category_id:
        s = Service(name=name, price=float(price.replace(',', '.')), duration=int(duration), category_id=category_id, establishment_id=current_user.establishment_id)
        s.is_combo = is_combo
        if is_combo and original_price:
            s.original_price = float(original_price.replace(',', '.'))
            
        db.session.add(s); db.session.commit()
        flash('Serviço adicionado com sucesso!', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/servicos/excluir/<int:id>', methods=['POST'])
@login_required
def delete_service(id):
    s = Service.query.get(id); db.session.delete(s); db.session.commit()
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/agendamentos/excluir/<int:id>', methods=['POST'])
@login_required
def delete_appointment(id):
    a = Appointment.query.get_or_404(id)
    if a.establishment_id == current_user.establishment_id:
        a.status = 'cancelado'
        db.session.commit()
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/agendamentos/concluir/<int:id>', methods=['POST'])
@login_required
def complete_appointment(id):
    if not current_user.establishment.has_access: return redirect(url_for('planos'))
    a = Appointment.query.get_or_404(id)
    if a.establishment_id != current_user.establishment_id: return "Erro", 403
    
    est = a.establishment
    
    # Cálculo da comissão para o Plano Gestão
    if est.plan_type == 'gestao':
        prof_id = request.form.get('professional_id')
        if prof_id:
            a.professional_id = int(prof_id)
            
        if a.professional_id:
            prof = Professional.query.get(a.professional_id)
            if prof:
                a.commission_value = a.total_price * (prof.commission_rate / 100.0)
    
    # --- CÓDIGO ANTIGO VALIDADO DE CONCLUSÃO E E-MAIL ---
    a.status = 'concluido'
    
    # --- SISTEMA DE FIDELIDADE ---
    msg_fidelidade = ""
    if est.loyalty_points_goal and est.loyalty_points_goal > 0:
        cliente = Client.query.filter_by(establishment_id=est.id, phone=a.client_phone).first()
        if not cliente:
            cliente = Client(phone=a.client_phone, name=a.client_name, establishment_id=est.id, points=0)
            db.session.add(cliente)
        
        cliente.points += 1
        pontos_restantes = est.loyalty_points_goal - cliente.points
        
        if pontos_restantes > 0:
            msg_fidelidade = f"\n\n🎁 Fidelidade: Você ganhou 1 ponto! Faltam apenas {pontos_restantes} ponto(s) para resgatar o seu prémio: {est.loyalty_reward}."
        else:
            msg_fidelidade = f"\n\n🎉 PARABÉNS! Você atingiu {cliente.points} pontos e GANHOU O SEU PRÉMIO: {est.loyalty_reward}! Mostre este e-mail na sua próxima visita."
            
    db.session.commit()
    
    link_av = request.host_url.replace('http://', 'https://') + f'avaliar/{a.id}'
    subj = f"Como foi o atendimento no(a) {est.name}?"
    body = f"Olá {a.client_name}!\n\nO seu atendimento foi concluído. Queremos saber a sua opinião!\n\nÉ super rápido: você só precisa clicar no link abaixo e dar uma nota de 1 a 5 estrelas.\n\n{link_av}{msg_fidelidade}\n\nObrigado!"
    send_email(subj, a.client_email, body)
    
    flash('Atendimento concluído! Ponto contabilizado.', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/agendamentos/arquivar/<int:id>', methods=['POST'])
@login_required
def archive_appointment(id):
    a = Appointment.query.get_or_404(id)
    if a.establishment_id == current_user.establishment_id:
        a.status = 'arquivado'
        db.session.commit()
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/cliente/<int:id>/zerar_pontos', methods=['POST'])
@login_required
def reset_loyalty(id):
    cliente = Client.query.get_or_404(id)
    if cliente.establishment_id == current_user.establishment_id:
        cliente.points = 0
        db.session.commit()
        flash('Prémio entregue! Os pontos do cliente foram zerados.', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/avaliar/<int:id>', methods=['GET', 'POST'])
def rate_appointment(id):
    a = Appointment.query.get_or_404(id)
    if a.rating: return "<div style='text-align:center; padding:50px; font-family:sans-serif;'><h1>Você já avaliou!</h1><p>Muito obrigado pela sua nota.</p></div>"
    if request.method == 'POST':
        a.rating = int(request.form.get('rating', 5))
        db.session.commit()
        return "<div style='text-align:center; padding:50px; font-family:sans-serif; color:green;'><h1>⭐⭐⭐⭐⭐<br>Avaliação enviada!</h1><p>Obrigado por nos ajudar a melhorar.</p></div>"
    
    html = f"""
    <html><meta name="viewport" content="width=device-width, initial-scale=1.0"><body style="font-family:sans-serif; text-align:center; padding:20px; background:#f8f9fa;">
    <div style="background:white; padding:30px; border-radius:10px; box-shadow:0 4px 6px rgba(0,0,0,0.1); max-width:400px; margin:auto;">
        <h2 style="color:#333;">Avalie o seu atendimento</h2>
        <p style="color:#666; font-size:14px;">Serviço: <b>{a.service_names}</b><br>Local: <b>{a.establishment.name}</b></p>
        <p style="font-size:13px; color:#888; margin-bottom:20px;">* Apenas escolha as estrelas abaixo. Não é preciso escrever comentários.</p>
        <form method="POST">
            <select name="rating" style="font-size:18px; padding:12px; width:100%; border-radius:5px; margin-bottom:20px; border:1px solid #ccc;">
                <option value="5">⭐⭐⭐⭐⭐ Excelente</option>
                <option value="4">⭐⭐⭐⭐ Muito Bom</option>
                <option value="3">⭐⭐⭐ Bom</option>
                <option value="2">⭐⭐ Regular</option>
                <option value="1">⭐ Ruim</option>
            </select>
            <button type="submit" style="padding:15px; width:100%; background:#0d6efd; color:white; border:none; border-radius:8px; font-size:16px; font-weight:bold; cursor:pointer;">Enviar Nota</button>
        </form>
    </div>
    </body></html>
    """
    return html

@app.route('/admin/agendamentos/falta/<int:id>', methods=['POST'])
@login_required
def mark_no_show(id):
    if not current_user.establishment.has_access: return redirect(url_for('planos'))
    a = Appointment.query.get_or_404(id)
    if a.establishment_id != current_user.establishment_id: return "Erro", 403
    if not Blacklist.query.filter_by(establishment_id=a.establishment_id, client_phone=a.client_phone).first():
        bl = Blacklist(establishment_id=a.establishment_id, client_phone=a.client_phone)
        db.session.add(bl)
    a.status = 'falta'
    db.session.commit()
    flash('Cliente marcado como Falta e bloqueado.', 'warning')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/blacklist/add', methods=['POST'])
@login_required
def add_blacklist():
    if not current_user.establishment.has_access: return redirect(url_for('planos'))
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

@app.route('/admin/profissional/adicionar', methods=['POST'])
@login_required
def add_professional():
    if current_user.establishment.plan_type != 'gestao': return redirect(url_for('admin_dashboard'))
    name = request.form.get('name')
    commission = request.form.get('commission_rate')
    if name and commission:
        p = Professional(name=name, commission_rate=float(commission.replace(',', '.')), establishment_id=current_user.establishment_id)
        db.session.add(p); db.session.commit()
        est = current_user.establishment
        count = Professional.query.filter_by(establishment_id=est.id).count()
        est.capacity = max(1, count)
        db.session.commit()
        flash('Profissional adicionado à equipe!', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/profissional/excluir/<int:id>', methods=['POST'])
@login_required
def delete_professional(id):
    p = Professional.query.get_or_404(id)
    if p.establishment_id == current_user.establishment_id:
        db.session.delete(p); db.session.commit()
        est = current_user.establishment
        count = Professional.query.filter_by(establishment_id=est.id).count()
        est.capacity = max(1, count)
        db.session.commit()
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/ativar-gestao')
@login_required
def ativar_gestao():
    if current_user.id == 1:
        current_user.establishment.plan_type = 'gestao'
        db.session.commit()
        flash('Modo DEV: Plano Gestão ativado!', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/desativar-gestao')
@login_required
def desativar_gestao():
    if current_user.id == 1:
        current_user.establishment.plan_type = 'solo'
        db.session.commit()
        flash('Modo DEV: Plano Solo ativado!', 'info')
    return redirect(url_for('admin_dashboard'))

@app.route('/api/horarios_disponiveis')
def get_available_times():
    est_id = request.args.get('est_id')
    d_str = request.args.get('date')
    dur_str = request.args.get('duration')
    prof_id = request.args.get('prof_id') 
    
    if not est_id or not d_str or not dur_str: return jsonify([])
    try: 
        sel_date = datetime.strptime(d_str, '%Y-%m-%d').date()
        total_dur = int(dur_str)
    except: return jsonify([])
    
    est = Establishment.query.get(est_id)
    day_sched = DaySchedule.query.filter_by(establishment_id=est.id, day_index=sel_date.weekday()).first()
    if not day_sched or not day_sched.is_active: return jsonify([])
    
    query = Appointment.query.filter_by(appointment_date=sel_date, establishment_id=est.id).filter(Appointment.status == 'pendente')
    if prof_id and prof_id != 'any':
        query = query.filter_by(professional_id=int(prof_id))
    appts = query.all()
    
    avail = []
    curr = datetime.combine(sel_date, day_sched.work_start)
    limit = datetime.combine(sel_date, day_sched.work_end)
    now = get_now_brazil()
    
    current_capacity = 1 if (prof_id and prof_id != 'any') else est.capacity
    
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
               
        in_pause2 = False
        if day_sched.pause2_start and day_sched.pause2_end:
            p2_s = datetime.combine(sel_date, day_sched.pause2_start)
            p2_e = datetime.combine(sel_date, day_sched.pause2_end)
            if (curr >= p2_s and curr < p2_e) or (end > p2_s and end <= p2_e) or (curr < p2_s and end > p2_e):
               in_pause2 = True

        if in_lunch or in_pause2:
            curr += timedelta(minutes=15); continue

        overlap_count = 0
        for a in appts:
            s = datetime.combine(sel_date, a.appointment_time)
            e = s + timedelta(minutes=a.total_duration)
            if max(curr, s) < min(end, e): overlap_count += 1
        
        if overlap_count < current_capacity: avail.append(curr.strftime('%H:%M'))
        
        curr += timedelta(minutes=15)
    return jsonify(avail)

if __name__ == '__main__':
    app.run(debug=True)