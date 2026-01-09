import os

# --- CONTEÚDO ATUALIZADO (V4 - Dias, Almoço, Notificações e Imagem) ---

APP_PY = r'''import os
import re
import threading
import time as time_module
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, time, timedelta

# --- CONFIGURAÇÃO ---
app = Flask(__name__)
app.config['SECRET_KEY'] = 'uma-chave-secreta-muito-segura-trocar-em-producao'
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'agendamento.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Faça login para gerenciar seu negócio.'

# --- MODELOS ---

class Establishment(db.Model):
    __tablename__ = 'establishments'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    url_prefix = db.Column(db.String(50), nullable=False, unique=True)
    contact_phone = db.Column(db.String(20), nullable=True) # Contato do Profissional
    
    # Horário de Funcionamento
    work_start = db.Column(db.Time, nullable=False, default=time(9, 0))
    work_end = db.Column(db.Time, nullable=False, default=time(18, 0))
    
    # Pausa para Almoço (Opcional)
    lunch_start = db.Column(db.Time, nullable=True)
    lunch_end = db.Column(db.Time, nullable=True)

    # Dias da Semana (String armazenando IDs: 0=Seg, 1=Ter... ex: "0,1,2,3,4")
    work_days = db.Column(db.String(20), default="0,1,2,3,4") 
    
    admins = db.relationship('Admin', backref='establishment', lazy=True)
    services = db.relationship('Service', backref='establishment', lazy=True)
    appointments = db.relationship('Appointment', backref='establishment', lazy=True)

    def __repr__(self):
        return f'<Establishment {self.name}>'

class Admin(UserMixin, db.Model):
    __tablename__ = 'admins'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    establishment_id = db.Column(db.Integer, db.ForeignKey('establishments.id'), nullable=False)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class Service(db.Model):
    __tablename__ = 'services'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    duration = db.Column(db.Integer, nullable=False)
    establishment_id = db.Column(db.Integer, db.ForeignKey('establishments.id'), nullable=False)
    
    appointments = db.relationship('Appointment', backref='service_info', lazy=True, cascade="all, delete-orphan")

class Appointment(db.Model):
    __tablename__ = 'appointments'
    id = db.Column(db.Integer, primary_key=True)
    client_name = db.Column(db.String(150), nullable=False)
    client_phone = db.Column(db.String(20), nullable=False)
    appointment_date = db.Column(db.Date, nullable=False)
    appointment_time = db.Column(db.Time, nullable=False)
    notified = db.Column(db.Boolean, default=False) # Controle de notificação
    
    service_id = db.Column(db.Integer, db.ForeignKey('services.id'), nullable=False)
    establishment_id = db.Column(db.Integer, db.ForeignKey('establishments.id'), nullable=False)

@login_manager.user_loader
def load_user(user_id):
    return Admin.query.get(int(user_id))


# --- SISTEMA DE NOTIFICAÇÃO (Simulação em Background) ---
def notification_worker():
    """Verifica agendamentos próximos a cada minuto e simula envio."""
    while True:
        with app.app_context():
            now = datetime.now()
            # Busca agendamentos daqui a 1 hora (margem de erro de alguns minutos)
            upcoming = Appointment.query.filter(
                Appointment.notified == False,
                Appointment.appointment_date == now.date()
            ).all()

            for appt in upcoming:
                appt_dt = datetime.combine(appt.appointment_date, appt.appointment_time)
                time_diff = appt_dt - now
                
                # Se faltar entre 55 e 65 minutos
                if timedelta(minutes=55) <= time_diff <= timedelta(minutes=65):
                    # SIMULAÇÃO DE ENVIO (Aqui entraria a API de WhatsApp/SMS)
                    print("\n" + "="*50)
                    print(f"🔔 NOTIFICAÇÃO ENVIADA!")
                    print(f"Para Cliente: {appt.client_name} ({appt.client_phone})")
                    print(f"Para Profissional: {appt.establishment.name}")
                    print(f"Mensagem: Seu agendamento é em 1 hora ({appt.appointment_time})")
                    print("="*50 + "\n")
                    
                    appt.notified = True
                    db.session.commit()
        
        time_module.sleep(60) # Verifica a cada 60 segundos

# Inicia a thread de notificação se for o processo principal
if not app.debug or os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
    threading.Thread(target=notification_worker, daemon=True).start()


# --- ROTAS PÚBLICAS ---

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/cadastro-negocio', methods=['GET', 'POST'])
def register_business():
    if current_user.is_authenticated:
        return redirect(url_for('admin_dashboard'))

    if request.method == 'POST':
        business_name = request.form.get('business_name')
        url_prefix = request.form.get('url_prefix').lower().strip()
        contact_phone = request.form.get('contact_phone')
        username = request.form.get('username')
        password = request.form.get('password')

        if not all([business_name, url_prefix, username, password]):
            flash('Preencha os campos obrigatórios.', 'danger')
            return render_template('register.html')

        if not re.match("^[a-z0-9-]+$", url_prefix):
            flash('Link inválido.', 'danger')
            return render_template('register.html')

        if Establishment.query.filter_by(url_prefix=url_prefix).first():
            flash('Link indisponível.', 'danger')
            return render_template('register.html')
        
        # Cria Estabelecimento
        new_establishment = Establishment(
            name=business_name, 
            url_prefix=url_prefix,
            contact_phone=contact_phone,
            work_days="0,1,2,3,4" # Padrão: Seg a Sex
        )
        db.session.add(new_establishment)
        db.session.commit()

        new_admin = Admin(username=username, establishment_id=new_establishment.id)
        new_admin.set_password(password)
        db.session.add(new_admin)
        db.session.commit()

        flash('Conta criada com sucesso!', 'success')
        return redirect(url_for('login'))

    return render_template('register.html')


# --- ROTAS DE AGENDAMENTO ---

@app.route('/b/<url_prefix>')
def establishment_services(url_prefix):
    establishment = Establishment.query.filter_by(url_prefix=url_prefix).first_or_404()
    services = Service.query.filter_by(establishment_id=establishment.id).order_by(Service.name).all()
    return render_template('lista_servicos.html', services=services, establishment=establishment)

@app.route('/b/<url_prefix>/agendar/<int:service_id>')
def schedule_service(url_prefix, service_id):
    establishment = Establishment.query.filter_by(url_prefix=url_prefix).first_or_404()
    service = Service.query.get_or_404(service_id)
    
    if service.establishment_id != establishment.id:
        return "Erro", 404

    return render_template('agendamento.html', service=service, establishment=establishment)

@app.route('/b/<url_prefix>/confirmar', methods=['POST'])
def create_appointment(url_prefix):
    establishment = Establishment.query.filter_by(url_prefix=url_prefix).first_or_404()
    
    client_name = request.form.get('client_name')
    client_phone = request.form.get('client_phone')
    service_id = request.form.get('service_id')
    appointment_date_str = request.form.get('appointment_date')
    appointment_time_str = request.form.get('appointment_time')

    if not all([client_name, client_phone, service_id, appointment_date_str, appointment_time_str]):
        flash('Preencha todos os campos.', 'danger')
        return redirect(url_for('schedule_service', url_prefix=url_prefix, service_id=service_id))
    
    appointment_date = datetime.strptime(appointment_date_str, '%Y-%m-%d').date()
    appointment_time = datetime.strptime(appointment_time_str, '%H:%M').time()
    
    agendamento_dt = datetime.combine(appointment_date, appointment_time)
    if agendamento_dt < datetime.now():
        flash('Data/Hora inválida (passado).', 'danger')
        return redirect(url_for('schedule_service', url_prefix=url_prefix, service_id=service_id))

    service = Service.query.get(service_id)
    
    new_appointment = Appointment(
        client_name=client_name,
        client_phone=client_phone,
        service_id=service_id,
        appointment_date=appointment_date,
        appointment_time=appointment_time,
        establishment_id=establishment.id
    )
    db.session.add(new_appointment)
    db.session.commit()
    flash('Agendamento confirmado!', 'success')
    return redirect(url_for('establishment_services', url_prefix=url_prefix))


# --- PAINEL ADMIN E CONFIGURAÇÕES ---

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('admin_dashboard'))
    
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        admin = Admin.query.filter_by(username=username).first()

        if admin and admin.check_password(password):
            login_user(admin)
            return redirect(url_for('admin_dashboard'))
        else:
            flash('Login inválido.', 'danger')

    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/admin')
@login_required
def admin_dashboard():
    est = current_user.establishment
    today = datetime.now().date()
    
    appointments = Appointment.query.filter(
        Appointment.establishment_id == est.id,
        Appointment.appointment_date >= today
    ).order_by(Appointment.appointment_date, Appointment.appointment_time).all()
    
    services = Service.query.filter_by(establishment_id=est.id).order_by(Service.name).all()
    
    # Processa dias da semana para o checkbox
    active_days = est.work_days.split(',') if est.work_days else []
    
    return render_template('admin.html', 
                         appointments=appointments, 
                         services=services, 
                         establishment=est,
                         active_days=active_days)

@app.route('/admin/configurar', methods=['POST'])
@login_required
def update_settings():
    est = current_user.establishment
    
    # Horário Geral
    est.work_start = datetime.strptime(request.form.get('work_start'), '%H:%M').time()
    est.work_end = datetime.strptime(request.form.get('work_end'), '%H:%M').time()
    est.contact_phone = request.form.get('contact_phone')
    
    # Almoço
    lunch_start_str = request.form.get('lunch_start')
    lunch_end_str = request.form.get('lunch_end')
    
    if lunch_start_str and lunch_end_str:
        est.lunch_start = datetime.strptime(lunch_start_str, '%H:%M').time()
        est.lunch_end = datetime.strptime(lunch_end_str, '%H:%M').time()
    else:
        est.lunch_start = None
        est.lunch_end = None

    # Dias da Semana
    selected_days = request.form.getlist('work_days') # Lista ex: ['0', '1', '4']
    est.work_days = ",".join(selected_days)
    
    db.session.commit()
    flash('Configurações atualizadas!', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/servicos/novo', methods=['POST'])
@login_required
def add_service():
    name = request.form.get('name')
    duration = request.form.get('duration')
    if name and duration:
        new_service = Service(name=name, duration=int(duration), establishment_id=current_user.establishment_id)
        db.session.add(new_service)
        db.session.commit()
        flash('Serviço criado.', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/servicos/excluir/<int:service_id>', methods=['POST'])
@login_required
def delete_service(service_id):
    service = Service.query.get_or_404(service_id)
    if service.establishment_id != current_user.establishment_id: return "Erro", 403
    db.session.delete(service)
    db.session.commit()
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/agendamentos/excluir/<int:appointment_id>', methods=['POST'])
@login_required
def delete_appointment(appointment_id):
    appt = Appointment.query.get_or_404(appointment_id)
    if appt.establishment_id != current_user.establishment_id: return "Erro", 403
    db.session.delete(appt)
    db.session.commit()
    flash('Agendamento cancelado.', 'success')
    return redirect(url_for('admin_dashboard'))


# --- API INTELIGENTE (Dias + Almoço) ---

@app.route('/api/horarios_disponiveis')
def get_available_times():
    service_id = request.args.get('service_id', type=int)
    date_str = request.args.get('date')

    if not service_id or not date_str:
        return jsonify({'error': 'Dados incompletos'}), 400

    try:
        selected_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        return jsonify({'error': 'Data inválida'}), 400

    service = Service.query.get(service_id)
    est = service.establishment
    
    # 1. Verifica se o estabelecimento trabalha neste dia da semana
    weekday = str(selected_date.weekday()) # 0=Seg, 6=Dom
    active_days = est.work_days.split(',') if est.work_days else []
    
    if weekday not in active_days:
        return jsonify([]) # Retorna vazio, não trabalha hoje

    # Agendamentos existentes
    appointments = Appointment.query.filter_by(
        appointment_date=selected_date,
        establishment_id=est.id
    ).all()
    
    busy_slots = []
    
    # Adiciona Pausa de Almoço como "horário ocupado"
    if est.lunch_start and est.lunch_end:
        l_start = datetime.combine(selected_date, est.lunch_start)
        l_end = datetime.combine(selected_date, est.lunch_end)
        busy_slots.append((l_start, l_end))
        
    for appt in appointments:
        dur = appt.service_info.duration if appt.service_info else 30
        start = datetime.combine(selected_date, appt.appointment_time)
        end = start + timedelta(minutes=dur)
        busy_slots.append((start, end))

    available = []
    service_dur = timedelta(minutes=service.duration)
    
    curr = datetime.combine(selected_date, est.work_start)
    limit = datetime.combine(selected_date, est.work_end)
    step = timedelta(minutes=15)
    now = datetime.now()

    while curr + service_dur <= limit:
        end_slot = curr + service_dur
        
        # Filtra passado
        if selected_date == now.date() and curr < now:
            curr += step
            continue

        is_free = True
        for b_start, b_end in busy_slots:
            if max(curr, b_start) < min(end_slot, b_end):
                is_free = False
                break
        
        if is_free:
            available.append(curr.strftime('%H:%M'))
        
        curr += step

    return jsonify(available)

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)
'''

# --- TEMPLATES ---

LAYOUT_HTML = r'''<!DOCTYPE html>
<html lang="pt-br">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{% block title %}Agenda Fácil{% endblock %}</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css">
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        body { font-family: 'Inter', sans-serif; background-color: #f8f9fa; min-height: 100vh; display: flex; flex-direction: column; }
        .tailwind-scope { font-family: 'Inter', sans-serif; }
        a { text-decoration: none; }
        main { flex: 1; }
    </style>
</head>
<body>
    <nav class="navbar navbar-expand-lg navbar-light bg-white shadow-sm sticky-top">
        <div class="container">
            <a class="navbar-brand fw-bold" href="{{ url_for('index') }}">
                <i class="bi bi-calendar-check text-primary"></i> Agenda Fácil
            </a>
            <button class="navbar-toggler" type="button" data-bs-toggle="collapse" data-bs-target="#mainNavbar">
                <span class="navbar-toggler-icon"></span>
            </button>
            <div class="collapse navbar-collapse" id="mainNavbar">
                <ul class="navbar-nav ms-auto mb-2 mb-lg-0 align-items-lg-center">
                    <li class="nav-item"><a class="nav-link" href="{{ url_for('index') }}">Home</a></li>
                    {% if current_user.is_authenticated %}
                        <li class="nav-item dropdown">
                            <a class="nav-link dropdown-toggle btn btn-light ms-2 px-3" href="#" id="adminMenu" data-bs-toggle="dropdown">
                                <i class="bi bi-person-circle me-1"></i> {{ current_user.establishment.name }}
                            </a>
                            <ul class="dropdown-menu dropdown-menu-end">
                                <li><a class="dropdown-item" href="{{ url_for('admin_dashboard') }}">Painel Admin</a></li>
                                <li><hr class="dropdown-divider"></li>
                                <li><a class="dropdown-item text-danger" href="{{ url_for('logout') }}">Sair</a></li>
                            </ul>
                        </li>
                    {% else %}
                        <li class="nav-item"><a class="nav-link fw-semibold" href="{{ url_for('login') }}">Login</a></li>
                        <li class="nav-item ms-lg-2"><a href="{{ url_for('register_business') }}" class="btn btn-primary btn-sm rounded-pill px-3">Criar Conta</a></li>
                    {% endif %}
                </ul>
            </div>
        </div>
    </nav>
    <main class="container-fluid p-0">
        {% with messages = get_flashed_messages(with_categories=true) %}
            {% if messages %}
                <div class="container mt-3">
                {% for category, message in messages %}
                    <div class="alert alert-{{ category }} alert-dismissible fade show shadow-sm" role="alert">
                        {{ message }} <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
                    </div>
                {% endfor %}
                </div>
            {% endif %}
        {% endwith %}
        {% block content %}{% endblock %}
    </main>
    <footer class="bg-white border-top pt-4 pb-3 mt-auto">
        <div class="container text-center"><p class="text-muted small mb-0">© 2025 Agenda Fácil.</p></div>
    </footer>
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
    {% block scripts %}{% endblock %}
</body>
</html>
'''

INDEX_HTML = r'''{% extends 'layout.html' %}
{% block title %}Agenda Fácil - Plataforma para seu Negócio{% endblock %}
{% block content %}
<div class="tailwind-scope">
    <section class="relative w-full overflow-hidden bg-white">
        <div class="max-w-7xl mx-auto px-6 lg:px-8">
            <div class="relative grid grid-cols-1 lg:grid-cols-2 gap-12 items-center py-16 lg:py-24">
                <div class="text-center lg:text-left">
                    <h1 class="text-4xl lg:text-5xl font-bold tracking-tight text-gray-900">
                        Sua agenda online, <span class="text-blue-600">organizada e profissional.</span>
                    </h1>
                    <p class="mt-6 text-lg leading-8 text-gray-600">
                        Automatize seus agendamentos. Defina horários, pausas e dias de trabalho. Seus clientes agendam 24h por dia e você é notificado.
                    </p>
                    <div class="mt-10 flex items-center justify-center lg:justify-start gap-x-6">
                        <a href="{{ url_for('register_business') }}" class="rounded-md bg-green-600 px-5 py-3 text-base font-semibold text-white shadow-sm hover:bg-green-700 transition-all duration-150">
                            Começar Agora
                        </a>
                        <a href="{{ url_for('login') }}" class="text-sm font-semibold leading-6 text-gray-900">
                            Já tenho conta <span aria-hidden="true">→</span>
                        </a>
                    </div>
                </div>
                <div class="relative mt-8 lg:mt-0 flex justify-center">
                    <!-- IMAGEM DE DASHBOARD REAL ADICIONADA -->
                    <img src="https://images.unsplash.com/photo-1460925895917-afdab827c52f?q=80&w=2426&auto=format&fit=crop" 
                         alt="Painel Administrativo" 
                         class="w-full max-w-lg rounded-xl shadow-2xl ring-1 ring-gray-900/10 hover:scale-105 transition-transform duration-500">
                </div>
            </div>
        </div>
    </section>
</div>
{% endblock %}
'''

REGISTER_HTML = r'''{% extends 'layout.html' %}
{% block title %}Criar Conta{% endblock %}
{% block content %}
<div class="row justify-content-center mt-5">
    <div class="col-md-8 col-lg-6">
        <div class="card shadow-sm border-0 rounded-3">
            <div class="card-body p-4 p-md-5">
                <h1 class="card-title h3 mb-4 text-center fw-bold">Comece seu Teste</h1>
                <form method="POST" action="{{ url_for('register_business') }}">
                    <h5 class="mb-3 text-primary">Dados do Estabelecimento</h5>
                    <div class="mb-3">
                        <label class="form-label">Nome do Negócio</label>
                        <input type="text" class="form-control" name="business_name" placeholder="Ex: Barbearia do Zé" required>
                    </div>
                    <div class="mb-3">
                        <label class="form-label">Link Personalizado</label>
                        <div class="input-group">
                            <span class="input-group-text bg-light">/b/</span>
                            <input type="text" class="form-control" name="url_prefix" placeholder="barbearia-do-ze" pattern="[a-z0-9-]+" title="Apenas letras minúsculas e hífens" required>
                        </div>
                    </div>
                    <!-- CAMPO NOVO: CONTATO -->
                    <div class="mb-4">
                        <label class="form-label">Seu Contato (WhatsApp)</label>
                        <input type="text" class="form-control" name="contact_phone" placeholder="(99) 99999-9999">
                        <div class="form-text">Será exibido para seus clientes.</div>
                    </div>
                    <h5 class="mb-3 text-primary border-top pt-3">Dados de Acesso</h5>
                    <div class="mb-3">
                        <label class="form-label">Usuário</label>
                        <input type="text" class="form-control" name="username" required>
                    </div>
                    <div class="mb-3">
                        <label class="form-label">Senha</label>
                        <input type="password" class="form-control" name="password" required>
                    </div>
                    <div class="d-grid mt-4">
                        <button type="submit" class="btn btn-success btn-lg fw-semibold">Criar Conta</button>
                    </div>
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
    <div class="col-md-6 col-lg-4">
        <div class="card shadow-sm border-0 rounded-3">
            <div class="card-body p-4">
                <h2 class="text-center mb-4 fw-bold">Área do Cliente</h2>
                <form method="POST">
                    <div class="mb-3"><label class="form-label">Usuário</label><input type="text" class="form-control" name="username" required></div>
                    <div class="mb-3"><label class="form-label">Senha</label><input type="password" class="form-control" name="password" required></div>
                    <div class="d-grid gap-2"><button type="submit" class="btn btn-primary fw-semibold">Entrar</button></div>
                    <div class="text-center mt-3"><a href="{{ url_for('register_business') }}">Cadastre-se</a></div>
                </form>
            </div>
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
        <div>
            <h1 class="h3 mb-0">Painel de Controle</h1>
            <p class="text-muted mb-0"><strong>{{ establishment.name }}</strong></p>
        </div>
        <div class="text-end bg-white p-2 rounded border shadow-sm">
            <small class="d-block text-muted">Link de Agendamento:</small>
            <a href="{{ url_for('establishment_services', url_prefix=establishment.url_prefix, _external=True) }}" target="_blank" class="fw-bold text-primary text-decoration-none">
                {{ url_for('establishment_services', url_prefix=establishment.url_prefix, _external=True) }} <i class="bi bi-box-arrow-up-right small"></i>
            </a>
        </div>
    </div>

    <div class="row">
        <!-- Agendamentos -->
        <div class="col-lg-7">
            <div class="card shadow-sm mb-4 border-0 rounded-3">
                <div class="card-header bg-white border-bottom-0 pt-3">
                    <h2 class="h5 mb-0">Próximos Agendamentos</h2>
                </div>
                <div class="card-body">
                    <div class="table-responsive">
                        <table class="table table-hover align-middle">
                            <thead>
                                <tr><th>Data/Hora</th><th>Cliente</th><th>Contato</th><th>Ações</th></tr>
                            </thead>
                            <tbody>
                                {% for appt in appointments %}
                                <tr>
                                    <td>
                                        <div class="fw-bold">{{ appt.appointment_date.strftime('%d/%m') }}</div>
                                        <div class="small text-muted">{{ appt.appointment_time.strftime('%H:%M') }}</div>
                                        <span class="badge bg-light text-dark border">{{ appt.service_info.name }}</span>
                                    </td>
                                    <td>{{ appt.client_name }}</td>
                                    <td><i class="bi bi-whatsapp text-success"></i> {{ appt.client_phone }}</td>
                                    <td>
                                        <form action="{{ url_for('delete_appointment', appointment_id=appt.id) }}" method="POST" onsubmit="return confirm('Cancelar?');">
                                            <button type="submit" class="btn btn-sm btn-outline-danger"><i class="bi bi-x-lg"></i></button>
                                        </form>
                                    </td>
                                </tr>
                                {% else %}
                                <tr><td colspan="4" class="text-center py-4 text-muted">Agenda livre.</td></tr>
                                {% endfor %}
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
        </div>

        <!-- Configurações -->
        <div class="col-lg-5">
            <div class="card shadow-sm mb-4 border-0 rounded-3">
                <div class="card-header bg-white border-bottom-0 pt-3">
                    <h2 class="h5 mb-0"><i class="bi bi-gear"></i> Configurações</h2>
                </div>
                <div class="card-body">
                    <form action="{{ url_for('update_settings') }}" method="POST">
                        
                        <!-- Dias da Semana -->
                        <label class="form-label fw-semibold small mb-2">Dias de Atendimento:</label>
                        <div class="mb-3 d-flex flex-wrap gap-2">
                            {% set days = ['Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb', 'Dom'] %}
                            {% for i in range(7) %}
                            <div class="form-check form-check-inline m-0">
                                <input class="form-check-input" type="checkbox" name="work_days" value="{{ i }}" id="day{{ i }}"
                                       {% if i|string in active_days %}checked{% endif %}>
                                <label class="form-check-label small" for="day{{ i }}">{{ days[i] }}</label>
                            </div>
                            {% endfor %}
                        </div>

                        <!-- Horários -->
                        <div class="row g-2 mb-3">
                            <div class="col-6">
                                <label class="small text-muted">Abertura</label>
                                <input type="time" class="form-control form-control-sm" name="work_start" value="{{ establishment.work_start.strftime('%H:%M') }}" required>
                            </div>
                            <div class="col-6">
                                <label class="small text-muted">Fechamento</label>
                                <input type="time" class="form-control form-control-sm" name="work_end" value="{{ establishment.work_end.strftime('%H:%M') }}" required>
                            </div>
                        </div>

                        <!-- Pausa Almoço -->
                        <div class="row g-2 mb-3">
                            <div class="col-12"><label class="small text-muted fw-bold">Pausa de Almoço (Opcional)</label></div>
                            <div class="col-6">
                                <label class="small text-muted">Início</label>
                                <input type="time" class="form-control form-control-sm" name="lunch_start" value="{{ establishment.lunch_start.strftime('%H:%M') if establishment.lunch_start else '' }}">
                            </div>
                            <div class="col-6">
                                <label class="small text-muted">Fim</label>
                                <input type="time" class="form-control form-control-sm" name="lunch_end" value="{{ establishment.lunch_end.strftime('%H:%M') if establishment.lunch_end else '' }}">
                            </div>
                        </div>

                        <!-- Contato -->
                        <div class="mb-3">
                            <label class="small text-muted">Seu Contato (WhatsApp)</label>
                            <input type="text" class="form-control form-control-sm" name="contact_phone" value="{{ establishment.contact_phone or '' }}">
                        </div>

                        <button type="submit" class="btn btn-outline-primary w-100 btn-sm">Salvar Configurações</button>
                    </form>
                </div>
            </div>

            <div class="card shadow-sm border-0 rounded-3">
                <div class="card-header bg-white border-bottom-0 pt-3">
                    <h2 class="h5 mb-0">Adicionar Serviço</h2>
                </div>
                <div class="card-body">
                    <form action="{{ url_for('add_service') }}" method="POST">
                        <div class="input-group mb-2">
                            <input type="text" class="form-control" name="name" placeholder="Nome" required>
                            <input type="number" class="form-control" name="duration" placeholder="Min" style="max-width: 80px;" required>
                            <button class="btn btn-success" type="submit">+</button>
                        </div>
                    </form>
                    <ul class="list-group list-group-flush small">
                        {% for service in services %}
                        <li class="list-group-item d-flex justify-content-between px-0 py-1">
                            <span>{{ service.name }} ({{ service.duration }}m)</span>
                            <form action="{{ url_for('delete_service', service_id=service.id) }}" method="POST" class="d-inline" onsubmit="return confirm('Excluir?');">
                                <button class="btn btn-link text-danger p-0 border-0" style="font-size: 0.8rem;"><i class="bi bi-trash"></i></button>
                            </form>
                        </li>
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
        <h1 class="display-5 fw-bold">{{ establishment.name }}</h1>
        {% if establishment.contact_phone %}
            <p class="text-muted"><i class="bi bi-whatsapp text-success"></i> Contato: {{ establishment.contact_phone }}</p>
        {% endif %}
    </div>

    <div class="row row-cols-1 row-cols-md-2 row-cols-lg-3 g-4 justify-content-center">
        {% for service in services %}
        <div class="col">
            <div class="card h-100 shadow-sm border-0 rounded-3 overflow-hidden hover-shadow transition">
                <div class="card-body d-flex flex-column p-4">
                    <h5 class="card-title h4 text-dark">{{ service.name }}</h5>
                    <p class="card-text text-muted mb-4"><i class="bi bi-clock"></i> {{ service.duration }} min</p>
                    <div class="mt-auto">
                        <a href="{{ url_for('schedule_service', url_prefix=establishment.url_prefix, service_id=service.id) }}" class="btn btn-outline-primary w-100 fw-semibold">Agendar</a>
                    </div>
                </div>
            </div>
        </div>
        {% else %}
        <div class="col-12 text-center text-muted">Nenhum serviço disponível.</div>
        {% endfor %}
    </div>
</div>
{% endblock %}
'''

AGENDAMENTO_HTML = r'''{% extends 'layout.html' %}
{% block title %}Agendar{% endblock %}
{% block content %}
<div class="container py-5">
    <div class="row justify-content-center">
        <div class="col-lg-8">
            <a href="{{ url_for('establishment_services', url_prefix=establishment.url_prefix) }}" class="text-decoration-none mb-3 d-inline-block"><i class="bi bi-arrow-left"></i> Voltar</a>
            <div class="card shadow-sm border-0 rounded-3">
                <div class="card-body p-4 p-md-5">
                    <div class="text-center mb-4">
                        <h2 class="h4 text-muted">{{ establishment.name }}</h2>
                        <h1 class="card-title h3 mb-1">Agendar {{ service.name }}</h1>
                        {% if establishment.contact_phone %}
                            <small class="text-muted d-block mt-2">Dúvidas? <i class="bi bi-whatsapp"></i> {{ establishment.contact_phone }}</small>
                        {% endif %}
                    </div>
                    
                    <form id="scheduleForm" action="{{ url_for('create_appointment', url_prefix=establishment.url_prefix) }}" method="POST">
                        <input type="hidden" name="service_id" value="{{ service.id }}">
                        
                        <div class="row g-3">
                            <div class="col-md-6">
                                <label class="form-label fw-semibold">Seu Nome</label>
                                <input type="text" class="form-control" name="client_name" required>
                            </div>
                            <div class="col-md-6">
                                <label class="form-label fw-semibold">Seu Contato</label>
                                <input type="tel" class="form-control" name="client_phone" required>
                            </div>
                            <div class="col-12">
                                <label class="form-label fw-semibold">Data</label>
                                <input type="date" class="form-control" id="appointment_date" name="appointment_date" required>
                            </div>
                            <div class="col-12">
                                <label class="form-label fw-semibold">Horários</label>
                                <div id="time-slots-container" class="border rounded-3 p-3 bg-light" style="min-height: 100px;">
                                    <p id="time-slots-placeholder" class="text-muted mb-0 text-center">Selecione uma data.</p>
                                    <div id="time-slots" class="d-flex flex-wrap gap-2 justify-content-center"></div>
                                </div>
                                <input type="hidden" id="appointment_time" name="appointment_time" required>
                            </div>
                        </div>
                        <hr class="my-4">
                        <button type="submit" id="submit-button" class="btn btn-primary btn-lg w-100 fw-semibold" disabled>Confirmar</button>
                    </form>
                </div>
            </div>
        </div>
    </div>
</div>
{% endblock %}
{% block scripts %}
<script>
document.addEventListener('DOMContentLoaded', function() {
    const dateInput = document.getElementById('appointment_date');
    const timeSlotsDiv = document.getElementById('time-slots');
    const timeSlotsPlaceholder = document.getElementById('time-slots-placeholder');
    const hiddenTimeInput = document.getElementById('appointment_time');
    const submitButton = document.getElementById('submit-button');
    const serviceId = '{{ service.id }}';

    dateInput.setAttribute('min', new Date().toISOString().split('T')[0]);

    dateInput.addEventListener('change', function() {
        const selectedDate = this.value;
        timeSlotsDiv.innerHTML = '';
        hiddenTimeInput.value = '';
        submitButton.disabled = true;
        if (!selectedDate) return;

        timeSlotsPlaceholder.textContent = 'Carregando...';
        timeSlotsPlaceholder.style.display = 'block';

        fetch(`/api/horarios_disponiveis?service_id=${serviceId}&date=${selectedDate}`)
            .then(response => response.json())
            .then(data => {
                if (data.length === 0) {
                    timeSlotsPlaceholder.textContent = 'Indisponível (Folga ou Lotado).';
                } else {
                    timeSlotsPlaceholder.style.display = 'none';
                    data.forEach(time => {
                        const button = document.createElement('button');
                        button.type = 'button';
                        button.className = 'btn btn-outline-primary time-slot-btn';
                        button.textContent = time;
                        button.dataset.time = time;
                        timeSlotsDiv.appendChild(button);
                    });
                }
            });
    });

    timeSlotsDiv.addEventListener('click', function(e) {
        if (e.target.classList.contains('time-slot-btn')) {
            document.querySelectorAll('.time-slot-btn').forEach(btn => {
                btn.classList.remove('active', 'btn-primary');
                btn.classList.add('btn-outline-primary');
            });
            e.target.classList.add('active', 'btn-primary');
            e.target.classList.remove('btn-outline-primary');
            hiddenTimeInput.value = e.target.dataset.time;
            submitButton.disabled = false;
        }
    });
});
</script>
{% endblock %}
'''

def atualizar_sistema():
    if not os.path.exists('templates'): os.makedirs('templates')
    if os.path.exists('agendamento.db'):
        try: os.remove('agendamento.db')
        except: pass

    arquivos = {
        'app.py': APP_PY,
        'templates/layout.html': LAYOUT_HTML,
        'templates/index.html': INDEX_HTML,
        'templates/register.html': REGISTER_HTML,
        'templates/login.html': LOGIN_HTML,
        'templates/admin.html': ADMIN_HTML,
        'templates/lista_servicos.html': LISTA_SERVICOS_HTML,
        'templates/agendamento.html': AGENDAMENTO_HTML
    }

    for caminho, conteudo in arquivos.items():
        with open(caminho, 'w', encoding='utf-8') as f:
            f.write(conteudo.strip())
        print(f"Atualizado: {caminho}")

    print("\n[SUCESSO] Sistema V4 instalado!")
    print("Agora execute: python app.py")

if __name__ == "__main__":
    atualizar_sistema()