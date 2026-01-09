import os

# --- APP.PY (Com Preços e Lógica de Fim de Semana) ---
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
    <style>body{font-family:'Inter',sans-serif;background-color:#f8f9fa} .tailwind-scope{font-family:'Inter',sans-serif}</style>
</head>
<body class="d-flex flex-column min-vh-100">
    <nav class="navbar navbar-expand-lg navbar-light bg-white shadow-sm sticky-top">
        <div class="container">
            <a class="navbar-brand fw-bold text-primary" href="{{ url_for('index') }}"><i class="bi bi-calendar-check-fill"></i> Agenda Fácil</a>
            {% if current_user.is_authenticated %}
            <div class="ms-auto">
                <a href="{{ url_for('admin_dashboard') }}" class="btn btn-sm btn-outline-primary me-2">Painel</a>
                <a href="{{ url_for('logout') }}" class="btn btn-sm btn-link text-danger text-decoration-none">Sair</a>
            </div>
            {% else %}
            <div class="ms-auto">
                <a href="{{ url_for('login') }}" class="fw-bold text-dark text-decoration-none me-3">Login</a>
                <a href="{{ url_for('register_business') }}" class="btn btn-primary btn-sm rounded-pill px-3">Criar Conta</a>
            </div>
            {% endif %}
        </div>
    </nav>
    <main class="flex-grow-1">
        {% block content %}{% endblock %}
    </main>
    <footer class="bg-white border-top py-3 text-center text-muted small mt-auto">© 2025 Agenda Fácil SaaS.</footer>
</body>
</html>
'''

# AQUI ESTÁ A MÁGICA DA IMAGEM REAL: Criamos um HTML que IMITA o painel admin
INDEX_HTML = r'''{% extends 'layout.html' %}
{% block title %}Agenda Profissional{% endblock %}
{% block content %}
<div class="tailwind-scope">
    <section class="bg-white overflow-hidden">
        <div class="max-w-7xl mx-auto px-6 py-16 lg:py-24 grid lg:grid-cols-2 gap-12 items-center">
            <div>
                <h1 class="text-4xl lg:text-5xl font-bold text-gray-900 mb-6">
                    Organize seu negócio com <span class="text-blue-600">inteligência.</span>
                </h1>
                <p class="text-lg text-gray-600 mb-8">
                    Gestão completa de horários, preços e clientes. Diferencie horários de fim de semana, controle o almoço e receba pagamentos.
                </p>
                <div class="flex gap-4">
                    <a href="{{ url_for('register_business') }}" class="bg-blue-600 text-white px-6 py-3 rounded-lg font-semibold hover:bg-blue-700 transition">Testar Grátis</a>
                </div>
            </div>
            
            <!-- SIMULAÇÃO VISUAL DO PAINEL (No lugar da imagem "irreal") -->
            <div class="relative rounded-xl bg-gray-900 p-2 shadow-2xl transform rotate-1 hover:rotate-0 transition duration-500">
                <div class="rounded-lg bg-gray-100 overflow-hidden h-[300px] lg:h-[400px] flex flex-col">
                    <!-- Barra Topo Fake -->
                    <div class="bg-white border-b p-3 flex justify-between items-center">
                        <div class="flex items-center gap-2">
                            <div class="w-3 h-3 rounded-full bg-red-400"></div>
                            <div class="w-3 h-3 rounded-full bg-yellow-400"></div>
                            <div class="w-3 h-3 rounded-full bg-green-400"></div>
                        </div>
                        <div class="text-xs font-bold text-gray-500">Painel Administrativo</div>
                    </div>
                    <!-- Conteúdo Fake -->
                    <div class="p-4 flex gap-4 h-full bg-slate-50">
                        <div class="w-2/3 bg-white rounded shadow-sm p-3 border">
                            <div class="text-xs font-bold text-gray-700 mb-2 border-b pb-1">Próximos Agendamentos</div>
                            <div class="space-y-2">
                                <div class="flex justify-between items-center text-xs p-2 bg-blue-50 rounded border border-blue-100">
                                    <span class="font-bold">09:00</span>
                                    <span>João Silva</span>
                                    <span class="bg-white px-1 rounded border">Corte (R$ 40)</span>
                                </div>
                                <div class="flex justify-between items-center text-xs p-2 bg-gray-50 rounded border">
                                    <span class="font-bold">10:30</span>
                                    <span>Maria Souza</span>
                                    <span class="bg-white px-1 rounded border">Hidratação (R$ 90)</span>
                                </div>
                                <div class="flex justify-between items-center text-xs p-2 bg-gray-50 rounded border">
                                    <span class="font-bold">14:00</span>
                                    <span>Pedro H.</span>
                                    <span class="bg-white px-1 rounded border">Barba (R$ 35)</span>
                                </div>
                            </div>
                        </div>
                        <div class="w-1/3 space-y-3">
                            <div class="bg-white p-3 rounded shadow-sm border h-1/2">
                                <div class="text-xs font-bold mb-2">Configuração</div>
                                <div class="w-full bg-gray-200 h-2 rounded mb-1"></div>
                                <div class="w-2/3 bg-gray-200 h-2 rounded mb-1"></div>
                            </div>
                            <div class="bg-blue-600 p-3 rounded shadow-sm h-1/3 flex items-center justify-center text-white text-xs font-bold">
                                + Novo Serviço
                            </div>
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
{% block content %}
<div class="row justify-content-center mt-5">
    <div class="col-md-5">
        <div class="card border-0 shadow-sm p-4">
            <h3 class="fw-bold mb-4 text-center">Criar Conta</h3>
            <form method="POST">
                <label class="form-label small fw-bold">NOME DO NEGÓCIO</label>
                <input type="text" name="business_name" class="form-control mb-2" required>
                
                <label class="form-label small fw-bold">LINK (ex: barbearia-top)</label>
                <input type="text" name="url_prefix" class="form-control mb-2" required>
                
                <label class="form-label small fw-bold">WHATSAPP</label>
                <input type="text" name="contact_phone" class="form-control mb-3">
                
                <div class="row g-2">
                    <div class="col"><input type="text" name="username" placeholder="Usuário" class="form-control" required></div>
                    <div class="col"><input type="password" name="password" placeholder="Senha" class="form-control" required></div>
                </div>
                <button class="btn btn-primary w-100 mt-4 fw-bold">Cadastrar</button>
            </form>
        </div>
    </div>
</div>
{% endblock %}
'''

LOGIN_HTML = r'''{% extends 'layout.html' %}
{% block content %}
<div class="row justify-content-center mt-5">
    <div class="col-md-4">
        <div class="card border-0 shadow-sm p-4">
            <h3 class="fw-bold mb-4 text-center">Entrar</h3>
            <form method="POST">
                <input type="text" name="username" placeholder="Usuário" class="form-control mb-2" required>
                <input type="password" name="password" placeholder="Senha" class="form-control mb-3" required>
                <button class="btn btn-dark w-100 fw-bold">Login</button>
            </form>
        </div>
    </div>
</div>
{% endblock %}
'''

ADMIN_HTML = r'''{% extends 'layout.html' %}
{% block content %}
<div class="container py-4">
    <div class="d-flex justify-content-between align-items-center mb-4 bg-white p-3 rounded shadow-sm">
        <h4 class="mb-0 fw-bold">{{ establishment.name }}</h4>
        <a href="{{ url_for('establishment_services', url_prefix=establishment.url_prefix) }}" target="_blank" class="text-decoration-none small fw-bold">
            Ver Página do Cliente <i class="bi bi-box-arrow-up-right"></i>
        </a>
    </div>

    <div class="row">
        <!-- AGENDAMENTOS -->
        <div class="col-lg-8">
            <div class="card border-0 shadow-sm mb-4">
                <div class="card-body">
                    <h5 class="fw-bold mb-3">Agenda do Dia</h5>
                    <table class="table table-hover align-middle">
                        <thead class="table-light"><tr><th>Hora</th><th>Cliente</th><th>Serviço</th><th>Preço</th><th>Ação</th></tr></thead>
                        <tbody>
                            {% for a in appointments %}
                            <tr>
                                <td class="fw-bold">{{ a.appointment_time.strftime('%H:%M') }}</td>
                                <td>{{ a.client_name }} <br> <small class="text-success"><i class="bi bi-whatsapp"></i> {{ a.client_phone }}</small></td>
                                <td>{{ a.service_info.name }}</td>
                                <td class="text-muted">R$ {{ "%.2f"|format(a.service_info.price) }}</td>
                                <td>
                                    <form action="{{ url_for('delete_appointment', id=a.id) }}" method="POST">
                                        <button class="btn btn-sm text-danger"><i class="bi bi-trash"></i></button>
                                    </form>
                                </td>
                            </tr>
                            {% else %}
                            <tr><td colspan="5" class="text-center text-muted py-3">Sem agendamentos hoje.</td></tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>

        <!-- SIDEBAR -->
        <div class="col-lg-4">
            <!-- SERVIÇOS -->
            <div class="card border-0 shadow-sm mb-3">
                <div class="card-body">
                    <h6 class="fw-bold mb-3">Adicionar Serviço</h6>
                    <form action="{{ url_for('add_service') }}" method="POST">
                        <input type="text" name="name" class="form-control form-control-sm mb-2" placeholder="Nome (ex: Corte)" required>
                        <div class="row g-2 mb-2">
                            <div class="col"><input type="number" name="duration" class="form-control form-control-sm" placeholder="Minutos" required></div>
                            <div class="col"><input type="text" name="price" class="form-control form-control-sm" placeholder="R$ 0.00" required></div>
                        </div>
                        <button class="btn btn-dark btn-sm w-100">Salvar Serviço</button>
                    </form>
                    <hr>
                    <ul class="list-group list-group-flush small">
                        {% for s in services %}
                        <li class="list-group-item d-flex justify-content-between px-0">
                            <span>{{ s.name }} (R$ {{ "%.2f"|format(s.price) }})</span>
                            <form action="{{ url_for('delete_service', id=s.id) }}" method="POST"><button class="btn btn-link text-danger p-0"><i class="bi bi-trash"></i></button></form>
                        </li>
                        {% endfor %}
                    </ul>
                </div>
            </div>

            <!-- CONFIGURAÇÃO -->
            <div class="card border-0 shadow-sm">
                <div class="card-body">
                    <h6 class="fw-bold mb-3">Horários & Dias</h6>
                    <form action="{{ url_for('update_settings') }}" method="POST">
                        <label class="small fw-bold text-muted">SEMANA (SEG-SEX)</label>
                        <div class="row g-2 mb-2">
                            <div class="col"><input type="time" name="work_start" class="form-control form-control-sm" value="{{ establishment.work_start.strftime('%H:%M') }}"></div>
                            <div class="col"><input type="time" name="work_end" class="form-control form-control-sm" value="{{ establishment.work_end.strftime('%H:%M') }}"></div>
                        </div>

                        <label class="small fw-bold text-muted text-primary">FIM DE SEMANA (SÁB-DOM)</label>
                        <div class="row g-2 mb-2">
                            <div class="col"><input type="time" name="weekend_start" class="form-control form-control-sm" value="{{ establishment.weekend_start.strftime('%H:%M') }}"></div>
                            <div class="col"><input type="time" name="weekend_end" class="form-control form-control-sm" value="{{ establishment.weekend_end.strftime('%H:%M') }}"></div>
                        </div>

                        <label class="small fw-bold text-muted">ALMOÇO</label>
                        <div class="row g-2 mb-2">
                            <div class="col"><input type="time" name="lunch_start" class="form-control form-control-sm" value="{{ establishment.lunch_start.strftime('%H:%M') if establishment.lunch_start else '' }}"></div>
                            <div class="col"><input type="time" name="lunch_end" class="form-control form-control-sm" value="{{ establishment.lunch_end.strftime('%H:%M') if establishment.lunch_end else '' }}"></div>
                        </div>

                        <div class="mb-2">
                            <label class="small fw-bold text-muted">DIAS ATIVOS</label><br>
                            {% set days = ['Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb', 'Dom'] %}
                            {% for i in range(7) %}
                                <div class="form-check form-check-inline m-0">
                                    <input class="form-check-input" type="checkbox" name="work_days" value="{{ i }}" {% if i|string in active_days %}checked{% endif %}>
                                    <label class="form-check-label" style="font-size: 0.75rem;">{{ days[i] }}</label>
                                </div>
                            {% endfor %}
                        </div>

                        <input type="text" name="contact_phone" class="form-control form-control-sm mb-2" value="{{ establishment.contact_phone or '' }}" placeholder="WhatsApp">
                        <button class="btn btn-outline-primary btn-sm w-100">Atualizar</button>
                    </form>
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
        <h1 class="fw-bold">{{ establishment.name }}</h1>
        <p class="text-muted">Agendamento Online</p>
    </div>
    <div class="row justify-content-center gap-4">
        {% for s in services %}
        <div class="col-md-5 col-lg-4">
            <div class="card border-0 shadow-sm h-100 p-3">
                <div class="card-body text-center">
                    <h4 class="fw-bold">{{ s.name }}</h4>
                    <h5 class="text-success fw-bold mb-3">R$ {{ "%.2f"|format(s.price) }}</h5>
                    <p class="text-muted small"><i class="bi bi-clock"></i> {{ s.duration }} minutos</p>
                    <a href="{{ url_for('schedule_service', url_prefix=establishment.url_prefix, service_id=s.id) }}" class="btn btn-primary w-100 fw-bold">Agendar</a>
                </div>
            </div>
        </div>
        {% else %}
        <div class="text-center text-muted">Nenhum serviço cadastrado.</div>
        {% endfor %}
    </div>
</div>
{% endblock %}
'''

AGENDAMENTO_HTML = r'''{% extends 'layout.html' %}
{% block content %}
<div class="container py-5">
    <div class="row justify-content-center">
        <div class="col-md-6">
            <div class="card border-0 shadow-sm p-4">
                <div class="text-center mb-4">
                    <h4 class="fw-bold">{{ establishment.name }}</h4>
                    <h5 class="text-muted">{{ service.name }} - R$ {{ "%.2f"|format(service.price) }}</h5>
                </div>
                <form id="form" method="POST" action="{{ url_for('create_appointment', url_prefix=establishment.url_prefix) }}">
                    <input type="hidden" name="service_id" value="{{ service.id }}">
                    <input type="text" name="client_name" class="form-control mb-2" placeholder="Seu Nome" required>
                    <input type="tel" name="client_phone" class="form-control mb-3" placeholder="Seu Celular" required>
                    
                    <label class="fw-bold small mb-1">Data</label>
                    <input type="date" id="date" name="appointment_date" class="form-control mb-3" required>
                    
                    <label class="fw-bold small mb-1">Horários</label>
                    <div id="slots" class="d-flex flex-wrap gap-2 mb-3">
                        <small class="text-muted w-100 text-center py-2 bg-light rounded">Selecione uma data</small>
                    </div>
                    <input type="hidden" id="time" name="appointment_time" required>
                    
                    <button id="btn" class="btn btn-success w-100 fw-bold" disabled>Confirmar Agendamento</button>
                </form>
            </div>
        </div>
    </div>
</div>
<script>
    const sid = {{ service.id }};
    const dateInp = document.getElementById('date');
    dateInp.min = new Date().toISOString().split('T')[0];
    
    dateInp.addEventListener('change', async (e) => {
        if(!e.target.value) return;
        const slotsDiv = document.getElementById('slots');
        slotsDiv.innerHTML = '<small class="w-100 text-center text-muted">Carregando...</small>';
        
        const res = await fetch(`/api/horarios_disponiveis?service_id=${sid}&date=${e.target.value}`);
        const times = await res.json();
        
        slotsDiv.innerHTML = '';
        if(times.length === 0) slotsDiv.innerHTML = '<small class="w-100 text-center text-danger">Sem horários.</small>';
        
        times.forEach(t => {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'btn btn-outline-dark btn-sm';
            btn.innerText = t;
            btn.onclick = () => {
                document.querySelectorAll('#slots button').forEach(b => b.classList.replace('btn-dark','btn-outline-dark'));
                btn.classList.replace('btn-outline-dark','btn-dark');
                document.getElementById('time').value = t;
                document.getElementById('btn').disabled = false;
            };
            slotsDiv.appendChild(btn);
        });
    });
</script>
{% endblock %}
'''

def atualizar():
    if not os.path.exists('templates'): os.makedirs('templates')
    if os.path.exists('agendamento.db'):
        try: os.remove('agendamento.db'); print("Banco resetado.")
        except: print("Erro ao apagar banco. Feche o python e tente de novo.")

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
    
    print("\n[SUCESSO] V6 Instalada! Rode: python app.py")

if __name__ == "__main__": atualizar()