import os

# --- CONTEÚDO ATUALIZADO (V3 - Monetização + Horários Reais) ---

APP_PY = r'''import os
import re
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
    
    # NOVOS CAMPOS: Horário de Funcionamento
    work_start = db.Column(db.Time, nullable=False, default=time(9, 0)) # Padrão 09:00
    work_end = db.Column(db.Time, nullable=False, default=time(18, 0))  # Padrão 18:00
    
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
    
    service_id = db.Column(db.Integer, db.ForeignKey('services.id'), nullable=False)
    establishment_id = db.Column(db.Integer, db.ForeignKey('establishments.id'), nullable=False)

@login_manager.user_loader
def load_user(user_id):
    return Admin.query.get(int(user_id))


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
        username = request.form.get('username')
        password = request.form.get('password')

        if not all([business_name, url_prefix, username, password]):
            flash('Todos os campos são obrigatórios.', 'danger')
            return render_template('register.html')

        if not re.match("^[a-z0-9-]+$", url_prefix):
            flash('Link inválido. Use apenas letras minúsculas e números.', 'danger')
            return render_template('register.html')

        if Establishment.query.filter_by(url_prefix=url_prefix).first():
            flash('Este link já está em uso.', 'danger')
            return render_template('register.html')
        
        if Admin.query.filter_by(username=username).first():
            flash('Usuário já existe.', 'danger')
            return render_template('register.html')

        # Cria com horário padrão 09:00 as 18:00
        new_establishment = Establishment(name=business_name, url_prefix=url_prefix)
        db.session.add(new_establishment)
        db.session.commit()

        new_admin = Admin(username=username, establishment_id=new_establishment.id)
        new_admin.set_password(password)
        db.session.add(new_admin)
        db.session.commit()

        flash('Conta criada! Faça login para configurar.', 'success')
        return redirect(url_for('login'))

    return render_template('register.html')


# --- FLUXO DE AGENDAMENTO ---

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
        return "Serviço inválido", 404

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
    
    # Validação extra: não permitir agendar no passado
    agendamento_dt = datetime.combine(appointment_date, appointment_time)
    if agendamento_dt < datetime.now():
        flash('Não é possível agendar em horários passados.', 'danger')
        return redirect(url_for('schedule_service', url_prefix=url_prefix, service_id=service_id))

    service = Service.query.get(service_id)
    if not service or service.establishment_id != establishment.id:
        flash('Erro no serviço.', 'danger')
        return redirect(url_for('establishment_services', url_prefix=url_prefix))

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
            flash('Credenciais inválidas.', 'danger')

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
    
    return render_template('admin.html', appointments=appointments, services=services, establishment=est)

@app.route('/admin/configurar', methods=['POST'])
@login_required
def update_settings():
    """Rota para atualizar horário de funcionamento"""
    start_str = request.form.get('work_start')
    end_str = request.form.get('work_end')
    
    est = current_user.establishment
    
    if start_str and end_str:
        try:
            est.work_start = datetime.strptime(start_str, '%H:%M').time()
            est.work_end = datetime.strptime(end_str, '%H:%M').time()
            db.session.commit()
            flash('Horário de funcionamento atualizado!', 'success')
        except ValueError:
            flash('Formato de hora inválido.', 'danger')
            
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
    if service.establishment_id != current_user.establishment_id:
        return "Erro", 403
    db.session.delete(service)
    db.session.commit()
    flash('Serviço removido.', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/agendamentos/excluir/<int:appointment_id>', methods=['POST'])
@login_required
def delete_appointment(appointment_id):
    appt = Appointment.query.get_or_404(appointment_id)
    if appt.establishment_id != current_user.establishment_id:
        return "Erro", 403
    db.session.delete(appt)
    db.session.commit()
    flash('Agendamento cancelado.', 'success')
    return redirect(url_for('admin_dashboard'))


# --- API INTELIGENTE (Bloqueia Passado + Usa Horário do Cliente) ---

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
    if not service:
        return jsonify({'error': 'Serviço não encontrado'}), 404

    # Pega configurações REAIS do estabelecimento
    establishment = service.establishment
    work_start = establishment.work_start
    work_end = establishment.work_end

    # Agendamentos existentes
    appointments_on_date = Appointment.query.filter_by(
        appointment_date=selected_date,
        establishment_id=service.establishment_id
    ).all()
    
    busy_slots = []
    for appt in appointments_on_date:
        dur = appt.service_info.duration if appt.service_info else 30
        start = datetime.combine(selected_date, appt.appointment_time)
        end = start + timedelta(minutes=dur)
        busy_slots.append((start, end))

    available = []
    service_dur = timedelta(minutes=service.duration)
    
    curr = datetime.combine(selected_date, work_start)
    limit = datetime.combine(selected_date, work_end)
    step = timedelta(minutes=15)
    
    # Data e Hora atual do servidor
    now = datetime.now()

    while curr + service_dur <= limit:
        end_slot = curr + service_dur
        
        # CORREÇÃO CRÍTICA: Se for hoje, pular horários que já passaram
        if selected_date == now.date() and curr < now:
            curr += step
            continue

        is_free = True
        for b_start, b_end in busy_slots:
            # Verifica colisão de horário
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

# --- TEMPLATES ATUALIZADOS ---

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
                <i class="bi bi-calendar-check text-primary"></i>
                Agenda Fácil
            </a>
            <button class="navbar-toggler" type="button" data-bs-toggle="collapse" data-bs-target="#mainNavbar">
                <span class="navbar-toggler-icon"></span>
            </button>
            <div class="collapse navbar-collapse" id="mainNavbar">
                <ul class="navbar-nav ms-auto mb-2 mb-lg-0 align-items-lg-center">
                    <li class="nav-item">
                        <a class="nav-link" href="{{ url_for('index') }}">Home</a>
                    </li>
                    
                    {% if current_user.is_authenticated %}
                        <li class="nav-item dropdown">
                            <a class="nav-link dropdown-toggle btn btn-light ms-2 px-3" href="#" id="adminMenu" data-bs-toggle="dropdown">
                                <i class="bi bi-person-circle me-1"></i> {{ current_user.establishment.name }}
                            </a>
                            <ul class="dropdown-menu dropdown-menu-end">
                                <li><a class="dropdown-item" href="{{ url_for('admin_dashboard') }}">Painel & Configurações</a></li>
                                <li><hr class="dropdown-divider"></li>
                                <li><a class="dropdown-item text-danger" href="{{ url_for('logout') }}">Sair</a></li>
                            </ul>
                        </li>
                    {% else %}
                         <li class="nav-item">
                            <a class="nav-link fw-semibold" href="{{ url_for('login') }}">Login</a>
                        </li>
                        <li class="nav-item ms-lg-2">
                            <a href="{{ url_for('register_business') }}" class="btn btn-primary btn-sm rounded-pill px-3">
                                Criar Conta
                            </a>
                        </li>
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
                        {{ message }}
                        <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
                    </div>
                {% endfor %}
                </div>
            {% endif %}
        {% endwith %}
        
        {% block content %}{% endblock %}
    </main>

    <footer class="bg-white border-top pt-4 pb-3 mt-auto">
        <div class="container text-center">
            <p class="text-muted small mb-0">© 2025 Agenda Fácil. Plataforma Profissional.</p>
        </div>
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
    <!-- Seção Hero -->
    <section class="relative w-full overflow-hidden bg-white">
        <div class="max-w-7xl mx-auto px-6 lg:px-8">
            <div class="relative grid grid-cols-1 lg:grid-cols-2 gap-12 items-center py-16 lg:py-24">
                <div class="text-center lg:text-left">
                    <h1 class="text-4xl lg:text-5xl font-bold tracking-tight text-gray-900">
                        Sua agenda online, <span class="text-blue-600">organizada e profissional.</span>
                    </h1>
                    <p class="mt-6 text-lg leading-8 text-gray-600">
                        Automatize seus agendamentos. Ideal para barbearias, salões, clínicas e autônomos. Gerencie horários e clientes em um só lugar.
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
                    <div class="w-full max-w-lg bg-blue-100 rounded-xl aspect-video flex items-center justify-center text-blue-500 font-bold shadow-xl">
                        Painel Administrativo
                    </div>
                </div>
            </div>
        </div>
    </section>

    <!-- Como funciona -->
    <section class="py-12 bg-gray-50 text-center">
        <div class="max-w-4xl mx-auto px-6">
            <h2 class="text-3xl font-bold text-gray-900">Potencialize seu negócio</h2>
            <div class="mt-8 grid grid-cols-1 md:grid-cols-3 gap-8">
                <div class="bg-white p-6 rounded-lg shadow-sm">
                    <div class="text-blue-600 text-2xl font-bold mb-2">1</div>
                    <h3 class="font-semibold">Cadastre-se</h3>
                    <p class="text-gray-600 text-sm">Crie sua conta profissional e defina seus horários de atendimento.</p>
                </div>
                <div class="bg-white p-6 rounded-lg shadow-sm">
                    <div class="text-blue-600 text-2xl font-bold mb-2">2</div>
                    <h3 class="font-semibold">Seu Link Exclusivo</h3>
                    <p class="text-gray-600 text-sm">Receba um link (ex: agendafacil.com/b/sua-marca) para enviar aos clientes.</p>
                </div>
                <div class="bg-white p-6 rounded-lg shadow-sm">
                    <div class="text-blue-600 text-2xl font-bold mb-2">3</div>
                    <h3 class="font-semibold">Agenda Cheia</h3>
                    <p class="text-gray-600 text-sm">Seus clientes agendam 24h por dia e você recebe as notificações.</p>
                </div>
            </div>
        </div>
    </section>
</div>
{% endblock %}
'''

REGISTER_HTML = r'''{% extends 'layout.html' %}

{% block title %}Criar Conta - Agenda Fácil{% endblock %}

{% block content %}
<div class="row justify-content-center mt-5">
    <div class="col-md-8 col-lg-6">
        <div class="card shadow-sm border-0 rounded-3">
            <div class="card-body p-4 p-md-5">
                <h1 class="card-title h3 mb-4 text-center fw-bold">Comece seu Teste</h1>
                
                <form method="POST" action="{{ url_for('register_business') }}">
                    <h5 class="mb-3 text-primary">Dados do Estabelecimento</h5>
                    <div class="mb-3">
                        <label for="business_name" class="form-label">Nome do Negócio</label>
                        <input type="text" class="form-control" id="business_name" name="business_name" placeholder="Ex: Barbearia do Zé" required>
                    </div>
                    
                    <div class="mb-4">
                        <label for="url_prefix" class="form-label">Link Personalizado</label>
                        <div class="input-group">
                            <span class="input-group-text bg-light">/b/</span>
                            <input type="text" class="form-control" id="url_prefix" name="url_prefix" placeholder="barbearia-do-ze" pattern="[a-z0-9-]+" title="Apenas letras minúsculas, números e hífens" required>
                        </div>
                    </div>

                    <h5 class="mb-3 text-primary border-top pt-3">Dados de Acesso</h5>
                    <div class="mb-3">
                        <label for="username" class="form-label">Usuário</label>
                        <input type="text" class="form-control" id="username" name="username" required>
                    </div>
                    <div class="mb-3">
                        <label for="password" class="form-label">Senha</label>
                        <input type="password" class="form-control" id="password" name="password" required>
                    </div>

                    <div class="d-grid mt-4">
                        <button type="submit" class="btn btn-success btn-lg fw-semibold">Criar Conta</button>
                    </div>
                    <p class="text-center mt-3">
                        Já tem conta? <a href="{{ url_for('login') }}">Fazer Login</a>
                    </p>
                </form>
            </div>
        </div>
    </div>
</div>
{% endblock %}
'''

LOGIN_HTML = r'''{% extends 'layout.html' %}

{% block title %}Login - Agenda Fácil{% endblock %}

{% block content %}
<div class="row justify-content-center mt-5">
    <div class="col-md-6 col-lg-4">
        <div class="card shadow-sm border-0 rounded-3">
            <div class="card-body p-4">
                <h2 class="text-center mb-4 fw-bold">Área do Cliente</h2>
                <form method="POST">
                    <div class="mb-3">
                        <label for="username" class="form-label">Usuário</label>
                        <input type="text" class="form-control" id="username" name="username" required>
                    </div>
                    <div class="mb-3">
                        <label for="password" class="form-label">Senha</label>
                        <input type="password" class="form-control" id="password" name="password" required>
                    </div>
                    <div class="d-grid gap-2">
                        <button type="submit" class="btn btn-primary fw-semibold">Entrar</button>
                    </div>
                    <div class="text-center mt-3">
                        <a href="{{ url_for('register_business') }}">Ainda não possui conta? Cadastre-se</a>
                    </div>
                </form>
            </div>
        </div>
    </div>
</div>
{% endblock %}
'''

ADMIN_HTML = r'''{% extends 'layout.html' %}

{% block title %}Painel Admin - {{ establishment.name }}{% endblock %}

{% block content %}
<div class="container py-4">
    <div class="d-flex justify-content-between align-items-center mb-4">
        <div>
            <h1 class="h3 mb-0">Painel de Controle</h1>
            <p class="text-muted mb-0">Gerenciando: <strong>{{ establishment.name }}</strong></p>
        </div>
        <div class="text-end bg-white p-2 rounded border shadow-sm">
            <small class="d-block text-muted">Link de Agendamento:</small>
            <a href="{{ url_for('establishment_services', url_prefix=establishment.url_prefix, _external=True) }}" target="_blank" class="fw-bold text-primary text-decoration-none">
                {{ url_for('establishment_services', url_prefix=establishment.url_prefix, _external=True) }} <i class="bi bi-box-arrow-up-right small"></i>
            </a>
        </div>
    </div>

    <div class="row">
        <!-- Coluna Esquerda: Agendamentos -->
        <div class="col-lg-8">
            <div class="card shadow-sm mb-4 border-0 rounded-3">
                <div class="card-header bg-white border-bottom-0 pt-3">
                    <h2 class="h5 mb-0">Próximos Agendamentos</h2>
                </div>
                <div class="card-body">
                    <div class="table-responsive">
                        <table class="table table-hover align-middle">
                            <thead>
                                <tr>
                                    <th>Data</th>
                                    <th>Hora</th>
                                    <th>Cliente</th>
                                    <th>Contato</th>
                                    <th>Serviço</th>
                                    <th>Ações</th>
                                </tr>
                            </thead>
                            <tbody>
                                {% for appt in appointments %}
                                <tr>
                                    <td class="fw-bold">{{ appt.appointment_date.strftime('%d/%m') }}</td>
                                    <td>{{ appt.appointment_time.strftime('%H:%M') }}</td>
                                    <td>{{ appt.client_name }}</td>
                                    <td>
                                        <i class="bi bi-whatsapp text-success me-1"></i> 
                                        {{ appt.client_phone }}
                                    </td>
                                    <td><span class="badge bg-light text-dark border">{{ appt.service_info.name }}</span></td>
                                    <td>
                                        <form action="{{ url_for('delete_appointment', appointment_id=appt.id) }}" method="POST" onsubmit="return confirm('Cancelar agendamento?');" class="d-inline">
                                            <button type="submit" class="btn btn-sm btn-outline-danger" title="Cancelar"><i class="bi bi-trash"></i></button>
                                        </form>
                                    </td>
                                </tr>
                                {% else %}
                                <tr><td colspan="6" class="text-center py-4 text-muted">Nenhum agendamento futuro.</td></tr>
                                {% endfor %}
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
        </div>

        <!-- Coluna Direita: Configurações e Serviços -->
        <div class="col-lg-4">
            
            <!-- Configuração de Horário (NOVO) -->
            <div class="card shadow-sm mb-4 border-0 rounded-3">
                <div class="card-header bg-white border-bottom-0 pt-3">
                    <h2 class="h5 mb-0"><i class="bi bi-clock"></i> Horário de Funcionamento</h2>
                </div>
                <div class="card-body">
                    <form action="{{ url_for('update_settings') }}" method="POST">
                        <div class="row g-2">
                            <div class="col-6">
                                <label class="small text-muted">Abertura</label>
                                <input type="time" class="form-control" name="work_start" value="{{ establishment.work_start.strftime('%H:%M') }}" required>
                            </div>
                            <div class="col-6">
                                <label class="small text-muted">Fechamento</label>
                                <input type="time" class="form-control" name="work_end" value="{{ establishment.work_end.strftime('%H:%M') }}" required>
                            </div>
                        </div>
                        <button type="submit" class="btn btn-outline-primary w-100 mt-3 btn-sm">Salvar Horário</button>
                    </form>
                </div>
            </div>

            <!-- Serviços -->
            <div class="card shadow-sm mb-4 border-0 rounded-3">
                <div class="card-header bg-white border-bottom-0 pt-3">
                    <h2 class="h5 mb-0"><i class="bi bi-scissors"></i> Serviços</h2>
                </div>
                <div class="card-body">
                    <form action="{{ url_for('add_service') }}" method="POST" class="mb-4">
                        <div class="mb-2">
                            <input type="text" class="form-control" name="name" placeholder="Nome (ex: Corte)" required>
                        </div>
                        <div class="mb-2">
                            <input type="number" class="form-control" name="duration" placeholder="Duração (min)" required min="1">
                        </div>
                        <button type="submit" class="btn btn-primary w-100 fw-semibold">Adicionar</button>
                    </form>
                    <hr>
                    <ul class="list-group list-group-flush">
                        {% for service in services %}
                        <li class="list-group-item d-flex justify-content-between align-items-center px-0">
                            <div>
                                <span class="fw-semibold">{{ service.name }}</span> <br>
                                <small class="text-muted">{{ service.duration }} min</small>
                            </div>
                            <form action="{{ url_for('delete_service', service_id=service.id) }}" method="POST" onsubmit="return confirm('Remover serviço?');">
                                <button type="submit" class="btn btn-sm btn-outline-danger"><i class="bi bi-trash"></i></button>
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

{% block title %}Agendar em {{ establishment.name }}{% endblock %}

{% block content %}
<div class="container py-5">
    <div class="text-center mb-5">
        <span class="badge bg-primary mb-2">Agendamento Online</span>
        <h1 class="display-5 fw-bold">{{ establishment.name }}</h1>
        <p class="lead text-muted">Selecione um serviço abaixo.</p>
    </div>

    <div class="row row-cols-1 row-cols-md-2 row-cols-lg-3 g-4 justify-content-center">
        {% for service in services %}
        <div class="col">
            <div class="card h-100 shadow-sm border-0 rounded-3 overflow-hidden hover-shadow transition">
                <div class="card-body d-flex flex-column p-4">
                    <h5 class="card-title h4 text-dark">{{ service.name }}</h5>
                    <p class="card-text text-muted mb-4">
                        <i class="bi bi-clock-history me-1"></i> 
                        {{ service.duration }} minutos
                    </p>
                    <div class="mt-auto">
                        <a href="{{ url_for('schedule_service', url_prefix=establishment.url_prefix, service_id=service.id) }}" class="btn btn-outline-primary w-100 fw-semibold">
                            Agendar
                        </a>
                    </div>
                </div>
            </div>
        </div>
        {% else %}
        <div class="col-12 text-center">
            <div class="alert alert-light border" role="alert">
                <i class="bi bi-info-circle me-2"></i>
                Nenhum serviço disponível no momento.
            </div>
        </div>
        {% endfor %}
    </div>
</div>
{% endblock %}
'''

AGENDAMENTO_HTML = r'''{% extends 'layout.html' %}

{% block title %}Agendar {{ service.name }} - {{ establishment.name }}{% endblock %}

{% block content %}
<div class="container py-5">
    <div class="row justify-content-center">
        <div class="col-lg-8">
            <a href="{{ url_for('establishment_services', url_prefix=establishment.url_prefix) }}" class="text-decoration-none mb-3 d-inline-block">
                <i class="bi bi-arrow-left"></i> Voltar
            </a>
            
            <div class="card shadow-sm border-0 rounded-3">
                <div class="card-body p-4 p-md-5">
                    <div class="text-center mb-4">
                        <h2 class="h4 text-muted">{{ establishment.name }}</h2>
                        <h1 class="card-title h3 mb-1">Agendar {{ service.name }}</h1>
                        <span class="badge bg-light text-dark border">{{ service.duration }} min</span>
                    </div>
                    
                    <form id="scheduleForm" action="{{ url_for('create_appointment', url_prefix=establishment.url_prefix) }}" method="POST">
                        <input type="hidden" name="service_id" value="{{ service.id }}">
                        
                        <div class="row g-3">
                            <div class="col-md-6">
                                <label for="client_name" class="form-label fw-semibold">Seu Nome</label>
                                <input type="text" class="form-control" id="client_name" name="client_name" placeholder="Nome Completo" required>
                            </div>

                            <div class="col-md-6">
                                <label for="client_phone" class="form-label fw-semibold">Celular / WhatsApp</label>
                                <input type="tel" class="form-control" id="client_phone" name="client_phone" placeholder="(XX) 9XXXX-XXXX" required>
                            </div>
                            
                            <div class="col-md-12">
                                <label for="appointment_date" class="form-label fw-semibold">Data</label>
                                <input type="date" class="form-control" id="appointment_date" name="appointment_date" required>
                            </div>

                            <div class="col-12">
                                <label class="form-label fw-semibold">Horários Disponíveis</label>
                                <div id="time-slots-container" class="border rounded-3 p-3 bg-light" style="min-height: 100px;">
                                    <p id="time-slots-placeholder" class="text-muted mb-0 text-center">Selecione uma data para ver a agenda.</p>
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

    // Bloqueia datas passadas
    const today = new Date().toISOString().split('T')[0];
    dateInput.setAttribute('min', today);

    dateInput.addEventListener('change', function() {
        const selectedDate = this.value;
        timeSlotsDiv.innerHTML = '';
        hiddenTimeInput.value = '';
        submitButton.disabled = true;

        if (!selectedDate) return;

        timeSlotsPlaceholder.textContent = 'Verificando agenda...';
        timeSlotsPlaceholder.style.display = 'block';

        fetch(`/api/horarios_disponiveis?service_id=${serviceId}&date=${selectedDate}`)
            .then(response => response.json())
            .then(data => {
                if (data.error) {
                    timeSlotsPlaceholder.textContent = data.error;
                    return;
                }
                
                if (data.length === 0) {
                    timeSlotsPlaceholder.textContent = 'Agenda lotada ou encerrada para hoje.';
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
            })
            .catch(error => {
                console.error(error);
                timeSlotsPlaceholder.textContent = 'Erro de conexão.';
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

# --- ATUALIZADOR ---

def atualizar_sistema():
    # Cria pastas se não existirem
    if not os.path.exists('templates'):
        os.makedirs('templates')

    # Limpa banco para recriar com novas colunas (work_start/end)
    if os.path.exists('agendamento.db'):
        try:
            os.remove('agendamento.db')
            print("Banco de dados resetado (necessário para atualização de colunas).")
        except:
            print("Aviso: Não foi possível apagar o banco. Se der erro, apague manualmente.")

    # Mapeamento
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

    print("\n[SUCESSO] Sistema V3 instalado!")
    print("Agora execute: python app.py")

if __name__ == "__main__":
    atualizar_sistema()