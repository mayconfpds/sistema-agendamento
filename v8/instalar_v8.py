import os

# --- APP.PY (Com Tabela de Horários por Dia, Correção de Salvamento e Preços) ---
APP_PY = r'''import os
import re
import threading
import time as time_module
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, time, timedelta

app = Flask(__name__)
app.config['SECRET_KEY'] = 'chave-secreta-v8-pro'
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'agendamento.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Faça login para continuar.'

# --- MODELOS ---

class Establishment(db.Model):
    __tablename__ = 'establishments'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    url_prefix = db.Column(db.String(50), nullable=False, unique=True)
    contact_phone = db.Column(db.String(20), nullable=True)
    
    # Relacionamentos
    schedules = db.relationship('DaySchedule', backref='establishment', lazy=True, cascade="all, delete-orphan")
    admins = db.relationship('Admin', backref='establishment', lazy=True)
    services = db.relationship('Service', backref='establishment', lazy=True)
    appointments = db.relationship('Appointment', backref='establishment', lazy=True)

class DaySchedule(db.Model):
    """Tabela para armazenar horário de cada dia da semana (0=Seg, 6=Dom)"""
    __tablename__ = 'day_schedules'
    id = db.Column(db.Integer, primary_key=True)
    establishment_id = db.Column(db.Integer, db.ForeignKey('establishments.id'), nullable=False)
    day_index = db.Column(db.Integer, nullable=False) # 0 a 6
    is_active = db.Column(db.Boolean, default=True)   # Trabalha nesse dia?
    
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
    price = db.Column(db.Float, nullable=False, default=0.0) # NOVO CAMPO: PREÇO
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

# NOTIFICAÇÕES
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

# --- ROTAS ---

@app.route('/')
def index(): return render_template('index.html')

@app.route('/cadastro-negocio', methods=['GET', 'POST'])
def register_business():
    if current_user.is_authenticated: return redirect(url_for('admin_dashboard'))
    if request.method == 'POST':
        # Cria Estabelecimento
        est = Establishment(
            name=request.form.get('business_name'),
            url_prefix=request.form.get('url_prefix').lower().strip(),
            contact_phone=request.form.get('contact_phone')
        )
        db.session.add(est)
        db.session.commit() # ID gerado
        
        # Cria os 7 dias da semana com horário padrão
        for i in range(7):
            day_schedule = DaySchedule(
                establishment_id=est.id,
                day_index=i,
                is_active=(i < 5), # Seg-Sex ativos por padrão
                work_start=time(9,0),
                work_end=time(18,0)
            )
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
        client_name=request.form.get('client_name'),
        client_phone=request.form.get('client_phone'),
        service_id=request.form.get('service_id'),
        appointment_date=d, appointment_time=t, establishment_id=est.id
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
    
    # Busca horários ordenados (0=Segunda)
    schedules = DaySchedule.query.filter_by(establishment_id=est.id).order_by(DaySchedule.day_index).all()
    
    return render_template('admin.html', appointments=appts, services=services, establishment=est, schedules=schedules)

@app.route('/admin/configurar', methods=['POST'])
@login_required
def update_settings():
    est = current_user.establishment
    
    # Verifica qual formulário foi enviado para não sobrescrever dados
    form_type = request.form.get('form_type')
    
    if form_type == 'contact':
        est.contact_phone = request.form.get('contact_phone')
        flash('Contato atualizado com sucesso!', 'success')

    elif form_type == 'schedule':
        # Atualiza cada dia da semana
        schedule_ids = request.form.getlist('schedule_id')
        for sid in schedule_ids:
            day_sched = DaySchedule.query.get(sid)
            if day_sched and day_sched.establishment_id == est.id:
                # Checkbox 'Ativo'
                day_sched.is_active = (request.form.get(f'active_{sid}') == 'on')
                
                # Horários
                ws = request.form.get(f'work_start_{sid}')
                we = request.form.get(f'work_end_{sid}')
                ls = request.form.get(f'lunch_start_{sid}')
                le = request.form.get(f'lunch_end_{sid}')
                
                if ws and we:
                    day_sched.work_start = datetime.strptime(ws, '%H:%M').time()
                    day_sched.work_end = datetime.strptime(we, '%H:%M').time()
                
                if ls and le:
                    day_sched.lunch_start = datetime.strptime(ls, '%H:%M').time()
                    day_sched.lunch_end = datetime.strptime(le, '%H:%M').time()
                else:
                    day_sched.lunch_start = None
                    day_sched.lunch_end = None
        flash('Horários de funcionamento salvos!', 'success')

    db.session.commit()
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/servicos/novo', methods=['POST'])
@login_required
def add_service():
    # Processa o preço (substitui vírgula por ponto)
    price_str = request.form.get('price', '0').replace(',', '.')
    try:
        price = float(price_str)
    except ValueError:
        price = 0.0

    svc = Service(
        name=request.form.get('name'), 
        duration=int(request.form.get('duration')), 
        price=price, # Adiciona o preço
        establishment_id=current_user.establishment_id
    )
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
    sid = request.args.get('service_id'); d_str = request.args.get('date')
    if not sid or not d_str: return jsonify([])
    
    sel_date = datetime.strptime(d_str, '%Y-%m-%d').date()
    svc = Service.query.get(sid)
    est = svc.establishment
    
    # 1. Busca configurações ESPECÍFICAS deste dia da semana
    day_idx = sel_date.weekday()
    day_sched = DaySchedule.query.filter_by(establishment_id=est.id, day_index=day_idx).first()
    
    if not day_sched or not day_sched.is_active:
        return jsonify([]) # Dia fechado

    # 2. Busca agendamentos do dia
    appts = Appointment.query.filter_by(appointment_date=sel_date, establishment_id=est.id).all()
    busy = []
    
    # Adiciona almoço do dia (se houver)
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
        if sel_date == now.date() and curr < now: 
            curr += timedelta(minutes=15); continue
        
        collision = False
        for bs, be in busy:
            if max(curr, bs) < min(end, be): collision = True; break
        
        if not collision: avail.append(curr.strftime('%H:%M'))
        curr += timedelta(minutes=15)
        
    return jsonify(avail)

if __name__ == '__main__':
    with app.app_context(): db.create_all()
    if not app.debug or os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
        threading.Thread(target=notification_worker, daemon=True).start()
    app.run(debug=True)
'''

# --- TEMPLATES ---

# LAYOUT, LOGIN, REGISTER IGUAIS V7 - Replicando para garantir integridade
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
    <style>body{font-family:'Inter',sans-serif;background-color:#f8f9fa} .tailwind-scope{font-family:'Inter',sans-serif} a{text-decoration:none} main{flex:1} body{min-height:100vh;display:flex;flex-direction:column}</style>
</head>
<body>
    <nav class="navbar navbar-expand-lg navbar-light bg-white shadow-sm sticky-top">
        <div class="container">
            <a class="navbar-brand fw-bold" href="{{ url_for('index') }}"><i class="bi bi-calendar-check text-primary"></i> Agenda Fácil</a>
            <button class="navbar-toggler" type="button" data-bs-toggle="collapse" data-bs-target="#nav"><span class="navbar-toggler-icon"></span></button>
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
            {% if m %}
                <div class="container mt-3">
                {% for c, msg in m %}
                    <div class="alert alert-{{ c }} alert-dismissible fade show shadow-sm">{{ msg }} <button type="button" class="btn-close" data-bs-dismiss="alert"></button></div>
                {% endfor %}
                </div>
            {% endif %}
        {% endwith %}
        {% block content %}{% endblock %}
    </main>
    <footer class="bg-white border-top pt-4 pb-3 mt-auto"><div class="container text-center"><p class="text-muted small">© 2025 Agenda Fácil.</p></div></footer>
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
                    <div class="relative w-full max-w-lg rounded-xl shadow-2xl ring-1 ring-gray-900/10 overflow-hidden bg-gray-50 aspect-video flex items-center justify-center">
                        <img src="{{ url_for('static', filename='painel.png') }}" 
                             alt="Painel Administrativo" 
                             class="w-full h-auto object-cover"
                             onerror="this.onerror=null; this.src='{{ url_for('static', filename='painel.jpg') }}'; this.nextElementSibling.style.display='flex'; this.style.display='none';">
                        <div class="absolute inset-0 bg-gray-200 flex-col items-center justify-center text-gray-500 hidden">
                            <span class="text-sm font-bold">Coloque "painel.png" na pasta static</span>
                        </div>
                    </div>
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
                    <div class="mb-3"><label class="form-label">Nome do Negócio</label><input type="text" class="form-control" name="business_name" required></div>
                    <div class="mb-3"><label class="form-label">Link Personalizado</label><div class="input-group"><span class="input-group-text bg-light">/b/</span><input type="text" class="form-control" name="url_prefix" pattern="[a-z0-9-]+" required></div></div>
                    <div class="mb-4"><label class="form-label">Seu Contato (WhatsApp)</label><input type="text" class="form-control" name="contact_phone"></div>
                    <h5 class="mb-3 text-primary border-top pt-3">Acesso</h5>
                    <div class="mb-3"><label class="form-label">Usuário</label><input type="text" class="form-control" name="username" required></div>
                    <div class="mb-3"><label class="form-label">Senha</label><input type="password" class="form-control" name="password" required></div>
                    <button class="btn btn-success w-100 mt-3 fw-bold">Criar Conta</button>
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
    <div class="col-md-5">
        <div class="card shadow-sm border-0 rounded-3 p-4">
            <h2 class="text-center mb-4 fw-bold">Login</h2>
            <form method="POST">
                <div class="mb-3"><label class="form-label">Usuário</label><input type="text" class="form-control" name="username" required></div>
                <div class="mb-3"><label class="form-label">Senha</label><input type="password" class="form-control" name="password" required></div>
                <button class="btn btn-primary w-100 fw-bold">Entrar</button>
            </form>
        </div>
    </div>
</div>
{% endblock %}
'''

# --- PAINEL ADMIN COM CAMPO DE PREÇO ---
ADMIN_HTML = r'''{% extends 'layout.html' %}
{% block title %}Painel Admin{% endblock %}
{% block content %}
<div class="container py-4">
    <div class="d-flex justify-content-between align-items-center mb-4">
        <h1 class="h3">Painel: {{ establishment.name }}</h1>
        <a href="{{ url_for('establishment_services', url_prefix=establishment.url_prefix) }}" target="_blank" class="btn btn-outline-primary btn-sm">Ver Minha Página <i class="bi bi-box-arrow-up-right"></i></a>
    </div>
    
    <!-- FORMULÁRIO 1: APENAS CONTATO -->
    <div class="card shadow-sm border-0 mb-4 p-3 bg-light">
        <form action="{{ url_for('update_settings') }}" method="POST" class="row align-items-center g-2">
            <input type="hidden" name="form_type" value="contact"> <!-- IDENTIFICADOR DO FORM -->
            <div class="col-auto"><label class="fw-bold">WhatsApp do Negócio:</label></div>
            <div class="col"><input type="text" name="contact_phone" class="form-control" value="{{ establishment.contact_phone or '' }}"></div>
            <div class="col-auto"><button class="btn btn-primary">Salvar Contato</button></div>
        </form>
    </div>

    <div class="row">
        <!-- FORMULÁRIO 2: APENAS HORÁRIOS -->
        <div class="col-12 mb-4">
            <div class="card shadow-sm border-0">
                <div class="card-header bg-white fw-bold">Configurar Horários por Dia</div>
                <div class="card-body p-0">
                    <form action="{{ url_for('update_settings') }}" method="POST">
                        <input type="hidden" name="form_type" value="schedule"> <!-- IDENTIFICADOR DO FORM -->
                        <div class="table-responsive">
                            <table class="table table-bordered mb-0 align-middle text-center">
                                <thead class="table-light">
                                    <tr>
                                        <th style="width: 50px;">Ativo</th>
                                        <th>Dia</th>
                                        <th>Abertura</th>
                                        <th>Fechamento</th>
                                        <th>Almoço Início</th>
                                        <th>Almoço Fim</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {% set day_names = ['Segunda', 'Terça', 'Quarta', 'Quinta', 'Sexta', 'Sábado', 'Domingo'] %}
                                    {% for d in schedules %}
                                    <tr class="{% if not d.is_active %}bg-light text-muted{% endif %}">
                                        <input type="hidden" name="schedule_id" value="{{ d.id }}">
                                        <td>
                                            <div class="form-check d-flex justify-content-center">
                                                <input class="form-check-input" type="checkbox" name="active_{{ d.id }}" {% if d.is_active %}checked{% endif %}>
                                            </div>
                                        </td>
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
                        <div class="p-3 bg-light border-top text-end">
                            <button class="btn btn-success fw-bold px-4">Salvar Todos os Horários</button>
                        </div>
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
                            <input type="text" name="price" class="form-control" placeholder="Preço (R$)" style="max-width: 100px;" required>
                            <button class="btn btn-success">+</button>
                        </div>
                    </form>
                    <ul class="list-group list-group-flush small">
                        {% for s in services %}
                        <li class="list-group-item d-flex justify-content-between px-0">
                            <span>{{ s.name }} ({{ s.duration }}min) - <span class="fw-bold text-success">R$ {{ "%.2f"|format(s.price) }}</span></span>
                            <form method="POST" action="{{ url_for('delete_service', id=s.id) }}" onsubmit="return confirm('Excluir?');"><button class="btn btn-link text-danger p-0 border-0"><i class="bi bi-trash"></i></button></form>
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

# --- LISTA DE SERVIÇOS DO CLIENTE (Mostra o preço) ---
LISTA_SERVICOS_HTML = r'''{% extends 'layout.html' %}
{% block title %}{{ establishment.name }}{% endblock %}
{% block content %}
<div class="container py-5">
    <div class="text-center mb-5">
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

# --- AGENDAMENTO (Mostra o preço) ---
AGENDAMENTO_HTML = r'''{% extends 'layout.html' %}
{% block title %}Agendar{% endblock %}
{% block content %}
<div class="container py-5">
    <div class="row justify-content-center">
        <div class="col-lg-6">
            <div class="card shadow-sm border-0 p-4">
                <h4 class="text-center mb-4">Agendar: {{ service.name }}</h4>
                <p class="text-center text-success fw-bold mb-4">Valor: R$ {{ "%.2f"|format(service.price) }}</p>
                <form id="form" method="POST" action="{{ url_for('create_appointment', url_prefix=establishment.url_prefix) }}">
                    <input type="hidden" name="service_id" value="{{ service.id }}">
                    <div class="mb-2"><label class="fw-bold small">Seu Nome</label><input type="text" name="client_name" class="form-control" required></div>
                    <div class="mb-3"><label class="fw-bold small">Seu WhatsApp</label><input type="tel" name="client_phone" class="form-control" required></div>
                    <div class="mb-3"><label class="fw-bold small">Data</label><input type="date" id="date" name="appointment_date" class="form-control" required></div>
                    <div class="mb-4">
                        <label class="fw-bold small">Horários Disponíveis</label>
                        <div id="slots" class="d-flex flex-wrap gap-2 mt-2"><small class="text-muted">Selecione a data...</small></div>
                        <input type="hidden" id="time" name="appointment_time" required>
                    </div>
                    <button id="btn" class="btn btn-primary w-100 fw-bold" disabled>Confirmar Agendamento</button>
                </form>
            </div>
        </div>
    </div>
</div>
{% endblock %}
{% block scripts %}
<script>
document.getElementById('date').min = new Date().toISOString().split('T')[0];
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

def atualizar():
    if not os.path.exists('templates'): os.makedirs('templates')
    if not os.path.exists('static'): os.makedirs('static')
    if os.path.exists('agendamento.db'):
        try: os.remove('agendamento.db')
        except: pass

    files = {
        'app.py': APP_PY,
        'templates/layout.html': LAYOUT_HTML,
        'templates/index.html': INDEX_HTML,
        'templates/register.html': REGISTER_HTML,
        'templates/login.html': LOGIN_HTML,
        'templates/admin.html': ADMIN_HTML,
        'templates/lista_servicos.html': LISTA_SERVICOS_HTML,
        'templates/agendamento.html': AGENDAMENTO_HTML
    }
    for n, c in files.items():
        with open(n, 'w', encoding='utf-8') as f: f.write(c.strip())
        print(f"Atualizado: {n}")
    
    print("\n[SUCESSO] Versão 8.2 (Preços e Correções) Instalada!")
    print("Coloque a imagem 'painel.png' na pasta 'static' e rode: python app.py")

if __name__ == "__main__":
    atualizar()