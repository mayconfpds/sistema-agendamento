import os
import json
import urllib.request
import random
import string
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import threading
import time as time_module
import socket
import requests
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime, time, timedelta
from sqlalchemy import inspect
from flask_migrate import Migrate
from flask_migrate import upgrade
import stripe
import uuid
from sqlalchemy import func
from sqlalchemy import text

def enviar_notificacao_telegram(nome_estabelecimento, telefone_responsavel):
    # ATENÇÃO: Confirme se colou os seus códigos reais aqui em baixo
    TOKEN = "8690359557:AAG5ZgOS1ay4oXDwvuh98mb-6IA7brehpI0"
    CHAT_ID = "5445877792"
    
    mensagem = (
        f"🚀 *NOVA ASSINATURA - TESTE GRÁTIS!*\n\n"
        f"💈 *Estabelecimento:* {nome_estabelecimento}\n"
        f"📱 *Contato:* {telefone_responsavel}\n\n"
        f"A base de clientes está a crescer! 💰"
    )
    
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    
    try:
        resposta = requests.post(url, data={'chat_id': CHAT_ID, 'text': mensagem, 'parse_mode': 'Markdown'}, timeout=5)
        
        # Se o Telegram recusar a mensagem, ele vai explicar o porquê aqui:
        if resposta.status_code != 200:
            print(f"ERRO DO TELEGRAM: {resposta.status_code} - {resposta.text}")
        else:
            print("Sucesso: Notificação do Telegram enviada!")
            
    except Exception as e:
        print(f"ERRO DE CONEXÃO (Python): {e}")

socket.setdefaulttimeout(15)

app = Flask(__name__)
app.config['SECRET_KEY'] = 'chave-v47-trial-br'
basedir = os.path.abspath(os.path.dirname(__file__))

app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)
app.config['REMEMBER_COOKIE_DURATION'] = timedelta(days=30)

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
    state = db.Column(db.String(2), default='PE')
    
    schedules = db.relationship('DaySchedule', backref='establishment', lazy=True, cascade="all, delete-orphan")
    admins = db.relationship('Admin', backref='establishment', lazy=True)
    services = db.relationship('Service', backref='establishment', lazy=True)
    appointments = db.relationship('Appointment', backref='establishment', lazy=True)
    blacklists = db.relationship('Blacklist', backref='establishment', lazy=True)
    products = db.relationship('Product', backref='establishment', lazy=True)
    sales = db.relationship('ProductSale', backref='establishment', lazy=True)
    subscriptions = db.relationship('ClientSubscription', backref='establishment', lazy=True)
    subscription_plans = db.relationship('SubscriptionPlan', backref='establishment', lazy=True)

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

class Product(db.Model):
    __tablename__ = 'products'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    price = db.Column(db.Float, nullable=False, default=0.0)
    description = db.Column(db.Text, nullable=True)
    image_filename = db.Column(db.String(100), nullable=True)
    stock_quantity = db.Column(db.Integer, default=0)
    establishment_id = db.Column(db.Integer, db.ForeignKey('establishments.id'), nullable=False)

class ProductSale(db.Model):
    __tablename__ = 'product_sales'
    id = db.Column(db.Integer, primary_key=True)
    product_name = db.Column(db.String(100), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    unit_price = db.Column(db.Float, nullable=False)
    total_price = db.Column(db.Float, nullable=False)
    sale_date = db.Column(db.DateTime, default=get_now_brazil)
    establishment_id = db.Column(db.Integer, db.ForeignKey('establishments.id'), nullable=False)
    
class SubscriptionPlan(db.Model):
    __tablename__ = 'subscription_plans'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    price = db.Column(db.Float, nullable=False)
    description = db.Column(db.Text, nullable=True)
    services_limit = db.Column(db.Integer, default=0)
    establishment_id = db.Column(db.Integer, db.ForeignKey('establishments.id'), nullable=False)

class ClientSubscription(db.Model):
    __tablename__ = 'client_subscriptions'
    id = db.Column(db.Integer, primary_key=True)
    client_name = db.Column(db.String(150), nullable=False)
    client_phone = db.Column(db.String(20), nullable=False)
    plan_id = db.Column(db.Integer, db.ForeignKey('subscription_plans.id'), nullable=False)
    expiry_date = db.Column(db.DateTime, nullable=False)
    status = db.Column(db.String(20), default='ativo')
    establishment_id = db.Column(db.Integer, db.ForeignKey('establishments.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=get_now_brazil)
    plan = db.relationship('SubscriptionPlan')
    
class BlockedDay(db.Model):
    __tablename__ = 'blocked_days'
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False)
    reason = db.Column(db.String(255), nullable=True)
    establishment_id = db.Column(db.Integer, db.ForeignKey('establishments.id'), nullable=False)

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
    is_club_included = db.Column(db.Boolean, default=False)
    is_hidden = db.Column(db.Boolean, default=False)

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
    edit_token = db.Column(db.String(100), unique=True, nullable=True)

    @property
    def service_names(self):
        return " + ".join([s.name for s in self.services])
    
    @property
    def client_loyalty(self):
        return Client.query.filter_by(establishment_id=self.establishment_id, phone=self.client_phone).first()
    
    @property
    def active_subscription(self):
        now = get_now_brazil()
        return ClientSubscription.query.filter_by(establishment_id=self.establishment_id, client_phone=self.client_phone, status='ativo').filter(ClientSubscription.expiry_date >= now).first()

class Blacklist(db.Model):
    __tablename__ = 'blacklists'
    id = db.Column(db.Integer, primary_key=True)
    client_phone = db.Column(db.String(20), nullable=False)
    establishment_id = db.Column(db.Integer, db.ForeignKey('establishments.id'), nullable=False)

@login_manager.user_loader
def load_user(user_id): return Admin.query.get(int(user_id))

try:
    with app.app_context():
        inspector = inspect(db.engine)
        
        if not inspector.has_table("establishments"): 
            db.create_all()
        else:
            if not inspector.has_table("products"):
                Product.__table__.create(db.engine)
            if not inspector.has_table("product_sales"):
                ProductSale.__table__.create(db.engine)
                
            if not inspector.has_table("subscription_plans"):
                SubscriptionPlan.__table__.create(db.engine)
            if not inspector.has_table("client_subscriptions"):
                ClientSubscription.__table__.create(db.engine)
                
            if not inspector.has_table("blocked_days"):
                BlockedDay.__table__.create(db.engine)
                
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
                    
            if 'is_club_included' not in columns_srv:
                with db.engine.connect() as conn:
                    conn.execute(db.text("ALTER TABLE services ADD COLUMN is_club_included BOOLEAN DEFAULT FALSE;"))
                    conn.commit()
                    
            columns_sub = [c['name'] for c in inspector.get_columns('client_subscriptions')]
            if 'created_at' not in columns_sub:
                with db.engine.connect() as conn:
                    conn.execute(db.text("ALTER TABLE client_subscriptions ADD COLUMN created_at DATETIME;"))
                    conn.commit()
                ClientSubscription.query.update({'created_at': get_now_brazil()})
                db.session.commit()
                
            columns_appts = [c['name'] for c in inspector.get_columns('appointments')]
            if 'edit_token' not in columns_appts:
                with db.engine.connect() as conn:
                    conn.execute(db.text("ALTER TABLE appointments ADD COLUMN edit_token VARCHAR(100);"))
                    conn.commit()
                    
            columns_est = [c['name'] for c in inspector.get_columns('establishments')]
            if 'state' not in columns_est:
                with db.engine.connect() as conn:
                    conn.execute(db.text("ALTER TABLE establishments ADD COLUMN state VARCHAR(2) DEFAULT 'PE';"))
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

if not os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
    threading.Thread(target=notification_worker, daemon=True).start()

@app.route('/pagamento')
@login_required
def payment():
    if current_user.establishment.is_active: return redirect(url_for('admin_dashboard'))
    plan_chosen = request.args.get('plan')
    if plan_chosen in ['solo', 'gestao']:
        current_user.establishment.plan_type = plan_chosen
        db.session.commit()
    try:
        success_url = request.host_url.replace('http://', 'https://') + 'pagamento/sucesso'
        cancel_url = request.host_url.replace('http://', 'https://') + 'pagamento/cancelado'
        price_id = STRIPE_PRICE_GESTAO if current_user.establishment.plan_type == 'gestao' else STRIPE_PRICE_SOLO
        if not price_id: return "Erro: IDs da Stripe não configurados.", 500
        session = stripe.checkout.Session.create(
            payment_method_types=['card'], line_items=[{'price': price_id, 'quantity': 1}],
            mode='subscription', allow_promotion_codes=True, success_url=success_url, cancel_url=cancel_url, customer_email=current_user.establishment.contact_email,
        )
        return redirect(session.url, code=303)
    except Exception as e:
        return f"<div style='padding:40px; text-align:center;'> <h2 style='color:red;'>Erro na Stripe</h2> <p><b>{str(e)}</b></p> <a href='/logout'>Sair</a> </div>", 500

@app.route('/pagamento/sucesso')
@login_required
def payment_success():
    current_user.establishment.is_active = True; db.session.commit()
    return redirect(url_for('admin_dashboard'))

@app.route('/pagamento/cancelado')
@login_required
def payment_cancel(): return redirect(url_for('logout'))

@app.route('/')
def index(): return render_template('index.html')

@app.route('/planos')
def planos():
    if current_user.is_authenticated and current_user.establishment.has_access: return redirect(url_for('admin_dashboard'))
    return render_template('planos.html')

@app.route('/cadastro-negocio', methods=['GET', 'POST'])
def register_business():
    if current_user.is_authenticated: return redirect(url_for('admin_dashboard'))
    if request.method == 'POST':
        username = request.form.get('username')
        is_master = (username == 'admin_demo') 
        plan_chosen = request.form.get('plan_type') or request.args.get('plan') or 'solo'
        
        est = Establishment(
            name=request.form.get('business_name'), url_prefix=request.form.get('url_prefix').lower().strip(), 
            contact_phone=request.form.get('contact_phone'), contact_email=request.form.get('contact_email'), 
            is_active=is_master, capacity=1, plan_type=plan_chosen, trial_ends=get_now_brazil() + timedelta(days=7)
        )
        db.session.add(est); db.session.commit()
        
        enviar_notificacao_telegram(est.name, est.contact_phone)
        
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
    services = Service.query.filter_by(establishment_id=est.id, is_hidden=False).all()
    categories = Category.query.filter_by(establishment_id=est.id).all()
    return render_template('lista_servicos.html', services=services, categories=categories, establishment=est)

@app.route('/b/<url_prefix>/agendar/<int:service_id>')
def schedule_service(url_prefix, service_id):
    est = Establishment.query.filter_by(url_prefix=url_prefix).first_or_404()
    if not est.has_access: return "Inativo", 403
    main_service = Service.query.get_or_404(service_id)
    other_services = Service.query.filter(
        Service.establishment_id == est.id, 
        Service.id != service_id, 
        Service.is_hidden == False
    ).all()
    professionals = Professional.query.filter_by(establishment_id=est.id).all() if est.plan_type == 'gestao' else []
    return render_template('agendamento.html', main_service=main_service, other_services=other_services, establishment=est, professionals=professionals)

@app.route('/b/<url_prefix>/confirmar', methods=['POST'])
def create_appointment(url_prefix):
    est = Establishment.query.filter_by(url_prefix=url_prefix).first_or_404()
    if not est.has_access: return "Inativo", 403
    client_phone = request.form.get('client_phone').strip()
    
    if Blacklist.query.filter_by(establishment_id=est.id, client_phone=client_phone).first():
        flash('Agendamento bloqueado. Por favor, entre em contato com o estabelecimento.', 'danger'); return redirect(url_for('establishment_services', url_prefix=url_prefix))

    now = get_now_brazil()
    user_appts = Appointment.query.filter_by(establishment_id=est.id, client_phone=client_phone).all()
    futuros_pendentes = [a for a in user_appts if datetime.combine(a.appointment_date, a.appointment_time) >= now and a.status == 'pendente']
    if len(futuros_pendentes) >= 4:
        flash('Limite de horários simultâneos atingido (Máx: 4).', 'warning'); return redirect(url_for('establishment_services', url_prefix=url_prefix))

    d = datetime.strptime(request.form.get('appointment_date'), '%Y-%m-%d').date()
    t = datetime.strptime(request.form.get('appointment_time'), '%H:%M').time()
    service_ids = request.form.getlist('services')
    selected_services = Service.query.filter(Service.id.in_(service_ids)).all()
    
    if not selected_services: flash('Nenhum serviço selecionado.', 'danger'); return redirect(url_for('establishment_services', url_prefix=url_prefix))

    total_dur = sum(s.duration for s in selected_services)
    total_price = sum(s.price for s in selected_services)
    start_dt = datetime.combine(d, t)
    end_dt = start_dt + timedelta(minutes=total_dur)
    
    active_sub = ClientSubscription.query.filter_by(establishment_id=est.id, client_phone=client_phone, status='ativo').filter(ClientSubscription.expiry_date >= now).first()
    is_subscriber = False
    if active_sub:
        is_subscriber = True
        total_price = sum(s.price for s in selected_services if not s.is_club_included)
    
    if start_dt < now: flash('Horário inválido.', 'danger'); return redirect(url_for('establishment_services', url_prefix=url_prefix))

    prof_id = request.form.get('professional_id')
    professional_id = None
    commission_value = 0.0
    
    if prof_id and prof_id != 'any':
        prof = Professional.query.get(int(prof_id))
        if prof and prof.establishment_id == est.id:
            professional_id = prof.id
            commission_value = total_price * (prof.commission_rate / 100.0)

    appts_on_day = Appointment.query.filter_by(appointment_date=d, establishment_id=est.id).filter(Appointment.status == 'pendente')
    if professional_id: appts_on_day = appts_on_day.filter_by(professional_id=professional_id)
    
    overlap_count = 0
    for a in appts_on_day.all():
        s = datetime.combine(d, a.appointment_time)
        e = s + timedelta(minutes=a.total_duration)
        if max(start_dt, s) < min(end_dt, e): overlap_count += 1
            
    current_capacity = 1 if professional_id else est.capacity
    if overlap_count >= current_capacity:
        flash('Ops! Esse horário acabou de ser ocupado. Tente outro.', 'danger'); return redirect(url_for('schedule_service', url_prefix=url_prefix, service_id=selected_services[0].id))

    token = str(uuid.uuid4())
    
    appt = Appointment(client_name=request.form.get('client_name'), client_phone=client_phone, client_email=request.form.get('client_email'), appointment_date=d, appointment_time=t, establishment_id=est.id, total_duration=total_dur, total_price=total_price, professional_id=professional_id, commission_value=commission_value, edit_token=token)
    for s in selected_services: appt.services.append(s)
    db.session.add(appt); db.session.commit()
    
    send_email(f"Confirmado: {est.name}", appt.client_email, f"Agendado para {d.strftime('%d/%m')} às {t.strftime('%H:%M')}.")
    reagendar_link = f"{request.host_url.rstrip('/')}{url_for('reagendar_view', token=token)}"

    email_body = f"Seu horário na {est.name} está confirmado para {d.strftime('%d/%m')} às {t.strftime('%H:%M')}.\n\nCaso precise alterar o horário, acesse o seu link exclusivo: {reagendar_link}"
    send_email(f"Horário Confirmado: {est.name}", appt.client_email, email_body)

    if is_subscriber:
        zap_msg = f"Olá, confirmo meu agendamento pelo Clube ({active_sub.plan.name}) para: {d.strftime('%d/%m')} às {t.strftime('%H:%M')}.\n\n📅 Precisa alterar o horário? Acesse: {reagendar_link}"
    else:
        zap_msg = f"Olá, confirmo meu agendamento para: {d.strftime('%d/%m')} às {t.strftime('%H:%M')}.\n\n📅 Precisa alterar o horário? Acesse: {reagendar_link}"
        
    zap_link = f"https://wa.me/55{est.contact_phone}?text={zap_msg}" if est.contact_phone else "#"
    
    return render_template('success_appointment.html', appointment=appt, zap_link=zap_link, reagendar_link=reagendar_link)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        adm = Admin.query.filter_by(username=request.form.get('username')).first()
        if adm and adm.check_password(request.form.get('password')):
            session.permanent = True
            login_user(adm, remember=True)
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
    
    search_query = request.args.get('search', '').strip()
    filter_date_str = request.args.get('filter_date', '')
    
    query = Appointment.query.filter(Appointment.establishment_id == est.id, Appointment.status.notin_(['arquivado', 'falta', 'cancelado']))
    if filter_date_str:
        try: filter_date = datetime.strptime(filter_date_str, '%Y-%m-%d').date(); query = query.filter(Appointment.appointment_date == filter_date)
        except: pass
    elif not search_query: query = query.filter(Appointment.appointment_date >= today)
        
    if search_query:
        search_term = f"%{search_query}%"
        query = query.filter(db.or_(Appointment.client_name.ilike(search_term), Appointment.client_phone.ilike(search_term)))
        
    appts = query.order_by(Appointment.appointment_date.asc(), Appointment.appointment_time.asc()).all()
    services = Service.query.filter_by(establishment_id=est.id).all()
    categories = Category.query.filter_by(establishment_id=est.id).all()
    schedules = DaySchedule.query.filter_by(establishment_id=est.id).order_by(DaySchedule.day_index).all()
    blacklists = Blacklist.query.filter_by(establishment_id=est.id).all()
    professionals = Professional.query.filter_by(establishment_id=est.id).all() 
    today_count = Appointment.query.filter(Appointment.establishment_id == est.id, Appointment.appointment_date == today).count()
    
    recent_sales = ProductSale.query.filter_by(establishment_id=est.id).order_by(ProductSale.sale_date.desc()).limit(15).all()
    
    return render_template('admin.html', appointments=appts, services=services, categories=categories, establishment=est, schedules=schedules, blacklists=blacklists, professionals=professionals, today_count=today_count)

@app.route('/admin/produto/adicionar', methods=['POST'])
@login_required
def add_product():
    try:
        name = request.form.get('name')
        price_str = request.form.get('price', '0')
        stock_str = request.form.get('stock_quantity', '0')
        desc = request.form.get('description', '')
        
        if not name or not price_str:
            return jsonify({'success': False, 'error': 'Nome e preço são obrigatórios.'})

        price = float(str(price_str).replace(',', '.'))
        stock = int(stock_str) if str(stock_str).strip() else 0

        p = Product(name=name, price=price, stock_quantity=stock, description=desc, establishment_id=current_user.establishment_id)

        if 'image' in request.files:
            file = request.files['image']
            if file and allowed_file(file.filename):
                fname = secure_filename(file.filename)
                uid = f"prod_{current_user.establishment_id}_{int(time_module.time())}_{fname}"
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], uid))
                p.image_filename = uid
        
        db.session.add(p); db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': f'Erro interno: {str(e)}'})

@app.route('/admin/produto/editar/<int:id>', methods=['POST'])
@login_required
def edit_product(id):
    try:
        p = Product.query.get_or_404(id)
        if p.establishment_id != current_user.establishment_id: return jsonify({'success': False, 'error':'Acesso negado'}), 403
        
        p.name = request.form.get('name', p.name)
        p.description = request.form.get('description', p.description)
        
        stock_str = request.form.get('stock_quantity')
        if stock_str and str(stock_str).strip(): p.stock_quantity = int(stock_str)
        
        price_str = request.form.get('price')
        if price_str and str(price_str).strip(): p.price = float(str(price_str).replace(',', '.'))
        
        if 'image' in request.files:
            file = request.files['image']
            if file and allowed_file(file.filename):
                fname = secure_filename(file.filename)
                uid = f"prod_{p.id}_{int(time_module.time())}_{fname}"
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], uid))
                p.image_filename = uid
                
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': f'Erro interno: {str(e)}'})

@app.route('/admin/produto/excluir/<int:id>', methods=['POST'])
@login_required
def delete_product(id):
    p = Product.query.get_or_404(id)
    if p.establishment_id == current_user.establishment_id:
        db.session.delete(p); db.session.commit()
        return jsonify({'success': True})
    return jsonify({'success': False}), 403

@app.route('/admin/produto/dar_baixa/<int:id>', methods=['POST'])
@login_required
def reduce_stock(id):
    p = Product.query.get_or_404(id)
    if p.establishment_id != current_user.establishment_id: return jsonify({'success': False}), 403
    try:
        quantidade = int(request.form.get('quantidade', 1))
        if p.stock_quantity >= quantidade:
            p.stock_quantity -= quantidade
            
            sale = ProductSale(product_name=p.name, quantity=quantidade, unit_price=p.price, total_price=p.price * quantidade, establishment_id=p.establishment_id)
            db.session.add(sale)
            db.session.commit()
            
            return jsonify({'success': True, 'new_stock': p.stock_quantity})
        return jsonify({'success': False, 'error': 'Estoque insuficiente para esta baixa.'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)})


@app.route('/admin/assinaturas/plano/adicionar', methods=['POST'])
@login_required
def add_subscription_plan():
    if current_user.establishment.plan_type != 'gestao':
        return jsonify({'success': False, 'error': 'Funcionalidade exclusiva do Plano Gestão. Faça o upgrade!'}), 403
    try:
        name = request.form.get('name')
        price = float(request.form.get('price', '0').replace(',', '.'))
        limit = int(request.form.get('services_limit', 0))
        desc = request.form.get('description', '')
        
        plan = SubscriptionPlan(name=name, price=price, services_limit=limit, description=desc, establishment_id=current_user.establishment_id)
        db.session.add(plan); db.session.commit()
        
        return jsonify({'success': True, 'id': plan.id, 'name': plan.name})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})
    
@app.route('/admin/assinaturas/plano/editar/<int:id>', methods=['POST'])
@login_required
def edit_subscription_plan(id):
    plan = SubscriptionPlan.query.get_or_404(id)
    if plan.establishment_id != current_user.establishment_id: return jsonify({'success': False}), 403
    
    plan.name = request.form.get('name', plan.name)
    plan.price = float(str(request.form.get('price', plan.price)).replace(',', '.'))
    plan.services_limit = int(request.form.get('services_limit', plan.services_limit))
    plan.description = request.form.get('description', plan.description)
    
    db.session.commit()

    return jsonify({'success': True, 'id': plan.id, 'name': plan.name})

@app.route('/admin/assinaturas/plano/excluir/<int:id>', methods=['POST'])
@login_required
def delete_subscription_plan(id):
    plan = SubscriptionPlan.query.get_or_404(id)
    if plan.establishment_id != current_user.establishment_id:
        return jsonify({'success': False, 'error': 'Acesso negado.'}), 403
    
    try:
        db.session.delete(plan)
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': 'Não é possível excluir um plano que já possui clientes assinantes vinculados a ele.'})

@app.route('/admin/assinaturas/cliente/adicionar', methods=['POST'])
@login_required
def add_subscriber():
    try:
        name = request.form.get('client_name')
        phone = request.form.get('client_phone').strip()
        plan_id = request.form.get('plan_id')
        meses = int(request.form.get('months', 1))
        
        expiry = get_now_brazil() + timedelta(days=30 * meses)
        
        sub = ClientSubscription(client_name=name, client_phone=phone, plan_id=plan_id, expiry_date=expiry, establishment_id=current_user.establishment_id)
        db.session.add(sub); db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})
    
@app.route('/api/check_subscription')
def check_sub():
    est_id = request.args.get('est_id')
    phone = request.args.get('phone')
    if not est_id or not phone: return jsonify({'is_subscriber': False})
    
    phone = ''.join(filter(str.isdigit, phone))
    sub = ClientSubscription.query.filter_by(establishment_id=est_id, client_phone=phone, status='ativo').filter(ClientSubscription.expiry_date >= get_now_brazil()).first()
    
    if sub: return jsonify({'is_subscriber': True, 'plan_name': sub.plan.name})
    return jsonify({'is_subscriber': False})

@app.route('/admin/assinaturas/cliente/editar/<int:id>', methods=['POST'])
@login_required
def edit_subscriber(id):
    sub = ClientSubscription.query.get_or_404(id)
    if sub.establishment_id != current_user.establishment_id: return jsonify({'success': False}), 403
    
    sub.client_name = request.form.get('client_name', sub.client_name)
    sub.client_phone = request.form.get('client_phone', sub.client_phone).strip()
    sub.plan_id = request.form.get('plan_id', sub.plan_id)
    sub.status = request.form.get('status', sub.status)
    
    expiry_str = request.form.get('expiry_date')
    if expiry_str:
        try: sub.expiry_date = datetime.strptime(expiry_str, '%Y-%m-%d')
        except: pass
        
    db.session.commit()
    return jsonify({'success': True})

@app.route('/admin/assinaturas/cliente/excluir/<int:id>', methods=['POST'])
@login_required
def delete_subscriber(id):
    sub = ClientSubscription.query.get_or_404(id)
    if sub.establishment_id == current_user.establishment_id:
        db.session.delete(sub)
        db.session.commit()
        return jsonify({'success': True})
    return jsonify({'success': False}), 403

@app.route('/admin/alterar-senha', methods=['GET', 'POST'])
@login_required
def alterar_senha():
    if request.method == 'POST':
        senha_atual = request.form.get('senha_atual')
        nova_senha = request.form.get('nova_senha')
        confirmar_senha = request.form.get('confirmar_senha')
        if not check_password_hash(current_user.password_hash, senha_atual): flash('A senha atual está incorreta.', 'danger')
        elif nova_senha != confirmar_senha: flash('As novas senhas não coincidem. Tente novamente.', 'danger')
        elif len(nova_senha) < 6: flash('A nova senha deve ter pelo menos 6 caracteres.', 'danger')
        else:
            current_user.password_hash = generate_password_hash(nova_senha)
            db.session.commit()
            flash('Senha alterada com sucesso!', 'success')
            return redirect(url_for('admin_dashboard'))
    return render_template('alterar_senha.html')

@app.route('/admin/historico')
@login_required
def historico_atendimentos():
    if not current_user.establishment.has_access: return redirect(url_for('planos'))
    est = current_user.establishment
    start_date_str = request.args.get('start_date'); end_date_str = request.args.get('end_date')
    query = Appointment.query.filter_by(establishment_id=est.id)
    if start_date_str:
        try: start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date(); query = query.filter(Appointment.appointment_date >= start_date)
        except: pass
    if end_date_str:
        try: end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date(); query = query.filter(Appointment.appointment_date <= end_date)
        except: pass
    appts = query.order_by(Appointment.appointment_date.desc(), Appointment.appointment_time.desc()).all()
    total_revenue = sum(a.total_price for a in appts if a.status in ['concluido', 'arquivado'])
    total_appts = len(appts)
    return render_template('historico.html', appointments=appts, establishment=est, start_date=start_date_str, end_date=end_date_str, total_revenue=total_revenue, total_appts=total_appts)

@app.route('/api/clientes_inativos')
@login_required
def api_clientes_inativos():
    
    if current_user.establishment.plan_type != 'gestao':
        return jsonify({'success': False, 'error': 'Funcionalidade exclusiva do Plano Gestão.'}), 403

    limite = get_now_brazil().date() - timedelta(days=45)
    est_id = current_user.establishment_id

    subquery = db.session.query(
        Appointment.client_phone,
        func.max(Appointment.appointment_date).label('ultima_data')
    ).filter(
        Appointment.establishment_id == est_id, 
        Appointment.status.in_(['concluido', 'arquivado'])
    ).group_by(Appointment.client_phone).subquery()

    clientes_inativos = db.session.query(
        Appointment.client_name, 
        subquery.c.client_phone, 
        subquery.c.ultima_data
    ).join(
        subquery, 
        db.and_(
            Appointment.client_phone == subquery.c.client_phone, 
            Appointment.appointment_date == subquery.c.ultima_data
        )
    ).filter(
        subquery.c.ultima_data <= limite, 
        Appointment.establishment_id == est_id
    ).group_by(
        subquery.c.client_phone, 
        Appointment.client_name, 
        subquery.c.ultima_data
    ).all()

    resultado = []
    hoje = get_now_brazil().date()
    
    for nome, telefone, ultima_data in clientes_inativos:
        dias_ausente = (hoje - ultima_data).days
  
        telefone_limpo = ''.join(filter(str.isdigit, telefone))
        
        resultado.append({
            'nome': nome,
            'telefone_exibicao': telefone,
            'telefone_limpo': telefone_limpo,
            'ultima_visita': ultima_data.strftime('%d/%m/%Y'),
            'dias_ausente': dias_ausente
        })
        
    resultado.sort(key=lambda x: x['dias_ausente'], reverse=True)

    return jsonify({'success': True, 'clientes': resultado})

@app.route('/admin/configurar', methods=['POST'])
@login_required
def update_settings():
    if not current_user.establishment.has_access: return redirect(url_for('planos'))
    est = current_user.establishment; ft = request.form.get('form_type')
    if ft == 'contact':
        est.contact_phone = request.form.get('contact_phone'); est.contact_email = request.form.get('contact_email')
        est.state = request.form.get('state', est.state)
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
    db.session.commit()
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest': return jsonify({'success': True})
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/categoria/adicionar', methods=['POST'])
@login_required
def add_category():
    name = request.form.get('name')
    if name:
        c = Category(name=name, establishment_id=current_user.establishment_id)
        db.session.add(c); db.session.commit()
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest': 
            return jsonify({'success': True, 'id': c.id, 'name': c.name})
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/categoria/excluir/<int:id>', methods=['POST'])
@login_required
def delete_category(id):
    c = Category.query.get_or_404(id)
    if c.establishment_id == current_user.establishment_id:
        geral = Category.query.filter_by(establishment_id=current_user.establishment_id, name='Geral').first()
        if not geral:
            geral = Category(name='Geral', establishment_id=current_user.establishment_id)
            db.session.add(geral); db.session.commit()
        if c.id == geral.id:
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest': return jsonify({'success': False, 'error': 'A categoria Geral não pode ser excluída.'})
        else:
            Service.query.filter_by(category_id=c.id).update({'category_id': geral.id})
            db.session.delete(c); db.session.commit()
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest': return jsonify({'success': True})
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/servico/adicionar', methods=['POST'])
@login_required
def add_service():
    name = request.form.get('name')
    price_val = request.form.get('price')
    duration = request.form.get('duration')
    category_id = request.form.get('category_id')
    is_combo = str(request.form.get('is_combo')).lower() in ['true', 'on', '1']
    is_hidden = request.form.get('is_hidden') == 'true'
    original_price = request.form.get('original_price')
    if name and price_val and duration and category_id:
        s = Service(name=name, price=float(str(price_val).replace(',', '.')), duration=int(duration), category_id=category_id, is_hidden=is_hidden, establishment_id=current_user.establishment_id)
        s.is_combo = is_combo
        s.is_club_included = str(request.form.get('is_club_included')).lower() in ['true', 'on', '1']
        if is_combo and original_price and str(original_price).strip() != '': s.original_price = float(str(original_price).replace(',', '.'))
        db.session.add(s); db.session.commit()
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest': return jsonify({'success': True})
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/servico/editar/<int:id>', methods=['POST'])
@login_required
def edit_service(id):
    s = Service.query.get_or_404(id)
    if s.establishment_id != current_user.establishment_id: return jsonify({'success': False, 'error': 'Acesso negado'}), 403
    s.name = request.form.get('name', s.name)
    price_val = request.form.get('price')
    if price_val: s.price = float(str(price_val).replace(',', '.'))
    duration = request.form.get('duration')
    if duration: s.duration = int(duration)
    category_id = request.form.get('category_id')
    if category_id: s.category_id = int(category_id)
    s.is_combo = str(request.form.get('is_combo')).lower() in ['true', 'on', '1']
    s.is_hidden = request.form.get('is_hidden') == 'true'
    s.is_club_included = str(request.form.get('is_club_included')).lower() in ['true', 'on', '1']
    original_price = request.form.get('original_price')
    if s.is_combo and original_price and str(original_price).strip() != '': s.original_price = float(str(original_price).replace(',', '.'))
    else: s.original_price = None
    db.session.commit()
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest': return jsonify({'success': True})
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/servicos/excluir/<int:id>', methods=['POST'])
@login_required
def delete_service(id):
    s = Service.query.get_or_404(id)
    if s.establishment_id == current_user.establishment_id: db.session.delete(s); db.session.commit()
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest': return jsonify({'success': True})
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/agendamentos/excluir/<int:id>', methods=['POST'])
@login_required
def delete_appointment(id):
    a = Appointment.query.get_or_404(id)
    if a.establishment_id == current_user.establishment_id: a.status = 'cancelado'; db.session.commit()
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest': return jsonify({'success': True})
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/agendamentos/concluir/<int:id>', methods=['POST'])
@login_required
def complete_appointment(id):
    if not current_user.establishment.has_access: return redirect(url_for('planos'))
    a = Appointment.query.get_or_404(id)
    if a.establishment_id != current_user.establishment_id: return jsonify({'success': False, 'error': 'Erro de acesso'}), 403
    est = a.establishment
    if est.plan_type == 'gestao':
            prof_id = request.form.get('professional_id')
            if prof_id: a.professional_id = int(prof_id)
            if a.professional_id:
                prof = Professional.query.get(a.professional_id)
                if prof: 
                    valor_real_servicos = sum(s.price for s in a.services)
                    a.commission_value = valor_real_servicos * (prof.commission_rate / 100.0)
    a.status = 'concluido'
    msg_fidelidade = ""
    if est.loyalty_points_goal and est.loyalty_points_goal > 0:
        cliente = Client.query.filter_by(establishment_id=est.id, phone=a.client_phone).first()
        if not cliente:
            cliente = Client(phone=a.client_phone, name=a.client_name, establishment_id=est.id, points=0)
            db.session.add(cliente)
        cliente.points += 1; pontos_restantes = est.loyalty_points_goal - cliente.points
        if pontos_restantes > 0: msg_fidelidade = f"\n\n🎁 Fidelidade: Você ganhou 1 ponto! Faltam apenas {pontos_restantes} ponto(s) para resgatar o seu prêmio: {est.loyalty_reward}."
        else: msg_fidelidade = f"\n\n🎉 PARABÉNS! Você atingiu {cliente.points} pontos e GANHOU O SEU PRÊMIO: {est.loyalty_reward}! Mostre este e-mail na sua próxima visita."
    db.session.commit()
    link_av = request.host_url.replace('http://', 'https://') + f'avaliar/{a.id}'
    subj = f"Como foi o atendimento no(a) {est.name}?"
    body = f"Olá {a.client_name}!\n\nO seu atendimento foi concluído. Queremos saber a sua opinião!\n\nÉ super rápido: você só precisa clicar no link abaixo e dar uma nota de 1 a 5 estrelas.\n\n{link_av}{msg_fidelidade}\n\nObrigado!"
    send_email(subj, a.client_email, body)
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest': return jsonify({'success': True})
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/agendamentos/arquivar/<int:id>', methods=['POST'])
@login_required
def archive_appointment(id):
    a = Appointment.query.get_or_404(id)
    if a.establishment_id == current_user.establishment_id: a.status = 'arquivado'; db.session.commit()
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest': return jsonify({'success': True})
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/cliente/<int:id>/zerar_pontos', methods=['POST'])
@login_required
def reset_loyalty(id):
    cliente = Client.query.get_or_404(id)
    if cliente.establishment_id == current_user.establishment_id: cliente.points = 0; db.session.commit()
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest': return jsonify({'success': True})
    return redirect(url_for('admin_dashboard'))

@app.route('/avaliar/<int:id>', methods=['GET', 'POST'])
def rate_appointment(id):
    a = Appointment.query.get_or_404(id)
    if a.rating: return "<div style='text-align:center; padding:50px; font-family:sans-serif;'><h1>Você já avaliou!</h1><p>Muito obrigado pela sua nota.</p></div>"
    if request.method == 'POST':
        a.rating = int(request.form.get('rating', 5)); db.session.commit()
        return "<div style='text-align:center; padding:50px; font-family:sans-serif; color:green;'><h1>⭐⭐⭐⭐⭐<br>Avaliação enviada!</h1><p>Obrigado por nos ajudar a melhorar.</p></div>"
    html = f"""<html><meta name="viewport" content="width=device-width, initial-scale=1.0"><body style="font-family:sans-serif; text-align:center; padding:20px; background:#f8f9fa;">
    <div style="background:white; padding:30px; border-radius:10px; box-shadow:0 4px 6px rgba(0,0,0,0.1); max-width:400px; margin:auto;">
        <h2 style="color:#333;">Avalie o seu atendimento</h2><p style="color:#666; font-size:14px;">Serviço: <b>{a.service_names}</b><br>Local: <b>{a.establishment.name}</b></p>
        <p style="font-size:13px; color:#888; margin-bottom:20px;">* Apenas escolha as estrelas abaixo. Não é preciso escrever comentários.</p>
        <form method="POST">
            <select name="rating" style="font-size:18px; padding:12px; width:100%; border-radius:5px; margin-bottom:20px; border:1px solid #ccc;">
                <option value="5">⭐⭐⭐⭐⭐ Excelente</option><option value="4">⭐⭐⭐⭐ Muito Bom</option><option value="3">⭐⭐⭐ Bom</option><option value="2">⭐⭐ Regular</option><option value="1">⭐ Ruim</option>
            </select><button type="submit" style="padding:15px; width:100%; background:#0d6efd; color:white; border:none; border-radius:8px; font-size:16px; font-weight:bold; cursor:pointer;">Enviar Nota</button>
        </form></div></body></html>"""
    return html

@app.route('/admin/agendamentos/falta/<int:id>', methods=['POST'])
@login_required
def mark_no_show(id):
    if not current_user.establishment.has_access: return redirect(url_for('planos'))
    a = Appointment.query.get_or_404(id)
    if a.establishment_id != current_user.establishment_id: return jsonify({'success': False, 'error': 'Erro de acesso'}), 403
    if not Blacklist.query.filter_by(establishment_id=a.establishment_id, client_phone=a.client_phone).first():
        bl = Blacklist(establishment_id=a.establishment_id, client_phone=a.client_phone)
        db.session.add(bl)
    a.status = 'falta'; db.session.commit()
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest': return jsonify({'success': True})
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/blacklist/add', methods=['POST'])
@login_required
def add_blacklist():
    if not current_user.establishment.has_access: return redirect(url_for('planos'))
    phone = request.form.get('phone', '').strip()
    if phone:
        exists = Blacklist.query.filter_by(establishment_id=current_user.establishment_id, client_phone=phone).first()
        if not exists:
            db.session.add(Blacklist(client_phone=phone, establishment_id=current_user.establishment_id)); db.session.commit()
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest': return jsonify({'success': True})
        else:
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest': return jsonify({'success': False, 'error': 'Número já bloqueado.'})
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/blacklist/remove/<int:id>', methods=['POST'])
@login_required
def remove_blacklist(id):
    b = Blacklist.query.get_or_404(id)
    if b.establishment_id == current_user.establishment_id: db.session.delete(b); db.session.commit()
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest': return jsonify({'success': True})
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/profissional/adicionar', methods=['POST'])
@login_required
def add_professional():
    if current_user.establishment.plan_type != 'gestao': return redirect(url_for('admin_dashboard'))
    name = request.form.get('name'); commission = request.form.get('commission_rate')
    if name and commission:
        p = Professional(name=name, commission_rate=float(str(commission).replace(',', '.')), establishment_id=current_user.establishment_id)
        db.session.add(p); db.session.commit()
        est = current_user.establishment; est.capacity = max(1, Professional.query.filter_by(establishment_id=est.id).count()); db.session.commit()
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest': 
            return jsonify({'success': True, 'id': p.id, 'name': p.name})
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/profissional/editar/<int:id>', methods=['POST'])
@login_required
def edit_professional(id):
    p = Professional.query.get_or_404(id)
    if p.establishment_id != current_user.establishment_id: return jsonify({'success': False, 'error': 'Acesso negado'}), 403
    p.name = request.form.get('name', p.name)
    commission = request.form.get('commission_rate')
    if commission: p.commission_rate = float(str(commission).replace(',', '.'))
    db.session.commit()
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest': 

        return jsonify({'success': True, 'id': p.id, 'name': p.name})
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/profissional/excluir/<int:id>', methods=['POST'])
@login_required
def delete_professional(id):
    p = Professional.query.get_or_404(id)
    if p.establishment_id == current_user.establishment_id:
        db.session.delete(p); db.session.commit()
        est = current_user.establishment; est.capacity = max(1, Professional.query.filter_by(establishment_id=est.id).count()); db.session.commit()
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest': return jsonify({'success': True})
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/ativar-gestao')
@login_required
def ativar_gestao():
    if current_user.id == 1: current_user.establishment.plan_type = 'gestao'; db.session.commit()
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/desativar-gestao')
@login_required
def desativar_gestao():
    if current_user.id == 1: current_user.establishment.plan_type = 'solo'; db.session.commit()
    return redirect(url_for('admin_dashboard'))

@app.route('/api/horarios_disponiveis')
def get_available_times():
    est_id = request.args.get('est_id'); d_str = request.args.get('date'); dur_str = request.args.get('duration'); prof_id = request.args.get('prof_id') 
    if not est_id or not d_str or not dur_str: return jsonify([])
    try: sel_date = datetime.strptime(d_str, '%Y-%m-%d').date(); total_dur = int(dur_str)
    except: return jsonify([])
    
    is_blocked = BlockedDay.query.filter_by(establishment_id=est_id, date=sel_date).first()
    if is_blocked:
        return jsonify([])
    
    est = Establishment.query.get(est_id)
    day_sched = DaySchedule.query.filter_by(establishment_id=est.id, day_index=sel_date.weekday()).first()
    if not day_sched or not day_sched.is_active: return jsonify([])
    
    query = Appointment.query.filter_by(appointment_date=sel_date, establishment_id=est.id).filter(Appointment.status == 'pendente')
    if prof_id and prof_id != 'any': query = query.filter_by(professional_id=int(prof_id))
    appts = query.all()
    
    avail = []; curr = datetime.combine(sel_date, day_sched.work_start); limit = datetime.combine(sel_date, day_sched.work_end); now = get_now_brazil()
    current_capacity = 1 if (prof_id and prof_id != 'any') else est.capacity
    
    while curr + timedelta(minutes=total_dur) <= limit:
        end = curr + timedelta(minutes=total_dur)
        if sel_date == now.date() and curr < now: curr += timedelta(minutes=15); continue
        in_lunch = False
        if day_sched.lunch_start and day_sched.lunch_end:
            lunch_s = datetime.combine(sel_date, day_sched.lunch_start); lunch_e = datetime.combine(sel_date, day_sched.lunch_end)
            if (curr >= lunch_s and curr < lunch_e) or (end > lunch_s and end <= lunch_e) or (curr < lunch_s and end > lunch_e): in_lunch = True
        in_pause2 = False
        if day_sched.pause2_start and day_sched.pause2_end:
            p2_s = datetime.combine(sel_date, day_sched.pause2_start); p2_e = datetime.combine(sel_date, day_sched.pause2_end)
            if (curr >= p2_s and curr < p2_e) or (end > p2_s and end <= p2_e) or (curr < p2_s and end > p2_e): in_pause2 = True

        if in_lunch or in_pause2: curr += timedelta(minutes=15); continue

        overlap_count = 0
        for a in appts:
            s = datetime.combine(sel_date, a.appointment_time); e = s + timedelta(minutes=a.total_duration)
            if max(curr, s) < min(end, e): overlap_count += 1
        
        if overlap_count < current_capacity: avail.append(curr.strftime('%H:%M'))
        curr += timedelta(minutes=15)
    return jsonify(avail)

@app.route('/admin/ajuda')
@login_required
def ajuda(): 
    est = current_user.establishment
    return render_template('ajuda.html', establishment=est)

@app.route('/recuperar-senha', methods=['GET', 'POST'])
def recuperar_senha():
    if request.method == 'POST':
        email = request.form.get('email')
        est = Establishment.query.filter_by(contact_email=email).first()
        if est:
            admin = Admin.query.filter_by(establishment_id=est.id).first()
            if admin:
                nova_senha = ''.join(random.choices(string.digits, k=6))
                admin.password_hash = generate_password_hash(nova_senha); db.session.commit()
                try:
                    url = "https://api.brevo.com/v3/smtp/email"
                    headers = {"accept": "application/json", "api-key": os.environ.get("BREVO_API_KEY"), "content-type": "application/json"}
                    corpo_email = f"Olá, {est.name}!\n\nA sua nova senha temporária é: {nova_senha}\n\nO seu usuário de login é: {admin.username}\n\nAceda ao painel e anote a sua senha num local seguro."
                    data = {"sender": {"name": "Suporte Agenda Fácil", "email": os.environ.get("BREVO_SENDER_EMAIL")}, "to": [{"email": email, "name": est.name}], "subject": "Recuperação de Senha - Agenda Fácil", "textContent": corpo_email}
                    req = urllib.request.Request(url, data=json.dumps(data).encode('utf-8'), headers=headers, method='POST')
                    with urllib.request.urlopen(req) as response: response.read()
                    flash('Uma senha temporária foi enviada para o seu e-mail!', 'success')
                except Exception as e: flash('Erro ao conectar com o serviço de e-mail.', 'danger')
            else: flash('Erro interno: Administrador não encontrado.', 'danger')
        else: flash('E-mail não encontrado no sistema.', 'danger')
        return redirect(url_for('login'))
    return render_template('recuperar_senha.html')

@app.route('/api/check_novos_agendamentos')
@login_required
def check_novos_agendamentos():
    if not current_user.establishment.has_access: return jsonify({'max_id': 0})
    ultimo = Appointment.query.filter_by(establishment_id=current_user.establishment_id).order_by(Appointment.id.desc()).first()
    return jsonify({'max_id': ultimo.id if ultimo else 0})

@app.route('/admin/relatorio_bi')
@login_required
def relatorio_bi():
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

    appts = Appointment.query.filter(Appointment.establishment_id == est.id, Appointment.appointment_date >= start_date, Appointment.appointment_date <= end_date).all()
    concluidos = [a for a in appts if a.status in ['concluido', 'arquivado']]
    faltas = [a for a in appts if a.status == 'falta']
    
    fat_agenda = sum(a.total_price for a in concluidos)
    qtd_concluidos = len(concluidos)
    taxa_faltas = (len(faltas) / len(appts) * 100) if appts else 0

    avaliacoes = [a.rating for a in concluidos if a.rating]
    media_avaliacoes = (sum(avaliacoes) / len(avaliacoes)) if avaliacoes else 0
    qtd_avaliacoes = len(avaliacoes)

    comissoes = {}
    desempenho_profissionais = {} # NOVO: Dados detalhados da equipe
    ranking_servicos = {}
    
    grafico_datas = []
    grafico_agenda = {}
    grafico_loja = {}
    
    delta = end_date - start_date
    for i in range(delta.days + 1):
        d = start_date + timedelta(days=i)
        d_str = d.strftime('%d/%m')
        grafico_datas.append(d_str)
        grafico_agenda[d_str] = 0.0
        grafico_loja[d_str] = 0.0

    for a in concluidos:
        # NOVO: Agrupamento de Desempenho e Comissões da Equipe
        if a.professional:
            prof_name = a.professional.name
            if prof_name not in desempenho_profissionais:
                desempenho_profissionais[prof_name] = {'faturamento': 0.0, 'atendimentos': 0, 'comissao': 0.0, 'soma_notas': 0, 'qtd_notas': 0}
            
            desempenho_profissionais[prof_name]['faturamento'] += a.total_price
            desempenho_profissionais[prof_name]['atendimentos'] += 1
            
            if a.commission_value:
                comissoes[prof_name] = comissoes.get(prof_name, 0) + a.commission_value
                desempenho_profissionais[prof_name]['comissao'] += a.commission_value
                
            if a.rating:
                desempenho_profissionais[prof_name]['soma_notas'] += a.rating
                desempenho_profissionais[prof_name]['qtd_notas'] += 1

        # Ranking de Serviços
        for s in a.services:
            if s.name not in ranking_servicos: ranking_servicos[s.name] = {'qtd': 0, 'receita': 0}
            ranking_servicos[s.name]['qtd'] += 1
            ranking_servicos[s.name]['receita'] += s.price
        
        # Gráfico Agenda
        d_str = a.appointment_date.strftime('%d/%m')
        if d_str in grafico_agenda:
            grafico_agenda[d_str] += a.total_price
            
    # Finaliza os cálculos de notas por profissional
    for prof_name, dados in desempenho_profissionais.items():
        dados['nota_media'] = (dados['soma_notas'] / dados['qtd_notas']) if dados['qtd_notas'] > 0 else 0
            
    top_servicos = sorted(ranking_servicos.items(), key=lambda x: x[1]['receita'], reverse=True)[:5]

    # --- CÁLCULOS DA LOJA ---
    vendas = ProductSale.query.filter(ProductSale.establishment_id == est.id).all()
    vendas_periodo = [v for v in vendas if start_date <= v.sale_date.date() <= end_date]
    fat_loja = sum(v.total_price for v in vendas_periodo)
    itens_vendidos = sum(v.quantity for v in vendas_periodo)
    
    ranking_produtos = {}
    for v in vendas_periodo:
        if v.product_name not in ranking_produtos: ranking_produtos[v.product_name] = {'qtd': 0, 'receita': 0}
        ranking_produtos[v.product_name]['qtd'] += v.quantity
        ranking_produtos[v.product_name]['receita'] += v.total_price
        
        d_str = v.sale_date.strftime('%d/%m')
        if d_str in grafico_loja:
            grafico_loja[d_str] += v.total_price
            
    top_produtos = sorted(ranking_produtos.items(), key=lambda x: x[1]['receita'], reverse=True)[:5]
    
    estoque = Product.query.filter_by(establishment_id=est.id).all()
    capital_parado = sum(p.price * p.stock_quantity for p in estoque)

    # --- ASSINATURAS E INDICADORES DE FIDELIZAÇÃO (NOVO) ---
    now = get_now_brazil()
    subs_ativos = ClientSubscription.query.filter_by(establishment_id=est.id, status='ativo').filter(ClientSubscription.expiry_date >= now).all()
    mrr = sum(s.plan.price for s in subs_ativos)
    total_assinantes = len(subs_ativos)

    telefones_periodo = set(a.client_phone for a in concluidos)
    total_clientes_unicos_periodo = len(telefones_periodo)
    clientes_recorrentes = 0
    clientes_novos = 0
    soma_dias_retorno = 0
    qtd_retornos = 0

    # Total histórico de clientes para taxa de adesão VIP
    total_clientes_base = db.session.query(Appointment.client_phone).filter_by(establishment_id=est.id).filter(Appointment.status.in_(['concluido', 'arquivado'])).distinct().count()
    taxa_adesao_vip = (total_assinantes / total_clientes_base * 100) if total_clientes_base > 0 else 0

    for telefone in telefones_periodo:
        historico_cliente = Appointment.query.filter_by(establishment_id=est.id, client_phone=telefone).filter(Appointment.status.in_(['concluido', 'arquivado'])).order_by(Appointment.appointment_date.asc()).all()
        
        if historico_cliente:
            primeira_visita = historico_cliente[0].appointment_date
            if start_date <= primeira_visita <= end_date:
                clientes_novos += 1
            else:
                clientes_recorrentes += 1

            for i in range(1, len(historico_cliente)):
                delta_dias = (historico_cliente[i].appointment_date - historico_cliente[i-1].appointment_date).days
                if delta_dias > 0:
                    soma_dias_retorno += delta_dias
                    qtd_retornos += 1

    taxa_retencao = (clientes_recorrentes / total_clientes_unicos_periodo * 100) if total_clientes_unicos_periodo > 0 else 0
    frequencia_media = (soma_dias_retorno / qtd_retornos) if qtd_retornos > 0 else 0
    
    # --- TOTAIS GERAIS ---
    fat_total = fat_agenda + fat_loja
    total_transacoes = qtd_concluidos + len(vendas_periodo)
    ticket_medio = (fat_total / total_transacoes) if total_transacoes > 0 else 0

    chart_data_agenda = [grafico_agenda[d] for d in grafico_datas]
    chart_data_loja = [grafico_loja[d] for d in grafico_datas]

    return render_template('relatorio_bi.html', establishment=est, start_date=start_date_str, end_date=end_date_str,
                           fat_total=fat_total, fat_agenda=fat_agenda, fat_loja=fat_loja, mrr=mrr, total_assinantes=total_assinantes,
                           qtd_concluidos=qtd_concluidos, taxa_faltas=taxa_faltas, itens_vendidos=itens_vendidos,
                           top_servicos=top_servicos, top_produtos=top_produtos, comissoes=comissoes, capital_parado=capital_parado,
                           ticket_medio=ticket_medio, media_avaliacoes=media_avaliacoes, qtd_avaliacoes=qtd_avaliacoes,
                           chart_labels=grafico_datas, chart_data_agenda=chart_data_agenda, chart_data_loja=chart_data_loja,
                           concluidos=concluidos, vendas_periodo=vendas_periodo,
                           desempenho_profissionais=desempenho_profissionais, taxa_retencao=taxa_retencao,
                           frequencia_media=frequencia_media, taxa_adesao_vip=taxa_adesao_vip, 
                           clientes_novos=clientes_novos, clientes_recorrentes=clientes_recorrentes)

@app.route('/api/whatsapp/webhook', methods=['POST'])
def whatsapp_webhook():

    try:
        dados = request.json
        
        telefone_remetente = dados.get('phone')
        mensagem_texto = str(dados.get('text', '')).strip()
        
        if telefone_remetente and mensagem_texto:
            telefone_limpo = telefone_remetente.replace('55', '', 1) 
            
            est = Establishment.query.filter(Establishment.contact_phone.like(f"%{telefone_limpo}%")).first()
            
            if est:
                daqui_a_3_dias = get_now_brazil().date() + timedelta(days=3)
                
                if mensagem_texto == '2':
                    ja_bloqueado = BlockedDay.query.filter_by(establishment_id=est.id, date=daqui_a_3_dias).first()
                    if not ja_bloqueado:
                        novo_bloqueio = BlockedDay(
                            date=daqui_a_3_dias, 
                            reason="Feriado (Fechado via Bot)", 
                            establishment_id=est.id
                        )
                        db.session.add(novo_bloqueio)
                        db.session.commit()
                        
                        msg_confirma = "✅ Agenda bloqueada com sucesso! Seus clientes não poderão marcar horários neste feriado. Bom descanso!"
                        
                elif mensagem_texto == '1':
                    msg_confirma = "✅ Entendido! A agenda continuará aberta e recebendo marcações neste dia. Bom trabalho!"

        return jsonify({"status": "success"})
    except Exception as e:
        print("Erro no webhook:", e)
        return jsonify({"status": "error"}), 500

@app.route('/reagendar/<token>')
def reagendar_view(token):
    appt = Appointment.query.filter_by(edit_token=token).first_or_404()
    est = appt.establishment
    now = get_now_brazil()
    appt_dt = datetime.combine(appt.appointment_date, appt.appointment_time)
    
    can_reschedule = True
    msg_erro = ""
    
    if appt.status != 'pendente':
        can_reschedule = False
        msg_erro = "Este agendamento já foi finalizado ou cancelado."
    elif appt_dt < now + timedelta(hours=2):
        can_reschedule = False
        msg_erro = "Faltam menos de 2 horas para o seu agendamento. Para alterações de última hora, por favor, contate o estabelecimento diretamente pelo WhatsApp."
        
    return render_template('reagendamento.html', appointment=appt, establishment=est, can_reschedule=can_reschedule, msg_erro=msg_erro)

@app.route('/api/reagendar/<token>', methods=['POST'])
def processar_reagendamento(token):
    appt = Appointment.query.filter_by(edit_token=token).first()
    if not appt: return jsonify({'success': False, 'error': 'Agendamento não encontrado.'})
    
    now = get_now_brazil()
    appt_dt = datetime.combine(appt.appointment_date, appt.appointment_time)
    if appt.status != 'pendente' or appt_dt < now + timedelta(hours=2):
        return jsonify({'success': False, 'error': 'Não é mais possível reagendar este horário.'})
        
    new_date_str = request.form.get('appointment_date')
    new_time_str = request.form.get('appointment_time')
    
    if not new_date_str or not new_time_str:
        return jsonify({'success': False, 'error': 'Selecione a nova data e o novo horário.'})
        
    appt.appointment_date = datetime.strptime(new_date_str, '%Y-%m-%d').date()
    appt.appointment_time = datetime.strptime(new_time_str, '%H:%M').time()
    db.session.commit()
    
    return jsonify({'success': True})

@app.route('/admin/upgrade_plano', methods=['POST'])
@login_required
def upgrade_plano():
    est = current_user.establishment
    
    if est.plan_type == 'gestao':
        flash('Você já possui o melhor plano da plataforma!', 'info')
        return redirect(url_for('admin_dashboard'))
        
    novo_plano = request.form.get('plano_destino')
    
    if novo_plano == 'gestao':
        hoje = get_now_brazil().date()
        fim_teste = est.trial_ends
        
        if hasattr(fim_teste, 'date'):
            fim_teste = fim_teste.date()
            
        if not est.is_active and fim_teste and hoje <= fim_teste:
            est.plan_type = 'gestao'
            db.session.commit()
            flash('Parabéns! O seu plano foi atualizado para GESTÃO. Aproveite as novas ferramentas!', 'success')
            return redirect(url_for('admin_dashboard'))
            
        else:
            numero_suporte = "5587991001697"
            mensagem = f"Olá! Sou da barbearia *{est.name}*. Já sou assinante do Plano Solo e gostaria de fazer o UPGRADE para o *Plano Gestão* para liberar a equipe, comissões e o Clube VIP. Como podemos fazer o ajuste do meu pagamento?"
            url_zap = f"https://wa.me/{numero_suporte}?text={mensagem}"
            return redirect(url_zap)
            
    flash('Plano inválido.', 'danger')
    return redirect(url_for('planos'))

with app.app_context():
    db.create_all()
    
    try:
        db.session.execute(text("ALTER TABLE services ADD COLUMN is_hidden BOOLEAN DEFAULT FALSE;"))
        db.session.commit()
        print("Sucesso: Coluna is_hidden adicionada na marra no PostgreSQL!")
    except Exception as e:
        db.session.rollback() 
        print(f"Aviso (a coluna já deve existir): {e}")

if __name__ == '__main__':
    app.run(debug=True) 