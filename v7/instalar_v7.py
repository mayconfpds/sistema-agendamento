import os

# --- APP.PY (Cópia exata da Versão 5 que você gostou) ---
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
app.config['SECRET_KEY'] = 'chave-secreta-v5-retorno'
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'agendamento.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Faça login para continuar.'

# MODELOS (Estrutura da V5)
class Establishment(db.Model):
    __tablename__ = 'establishments'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    url_prefix = db.Column(db.String(50), nullable=False, unique=True)
    contact_phone = db.Column(db.String(20), nullable=True)
    work_start = db.Column(db.Time, nullable=False, default=time(9, 0))
    work_end = db.Column(db.Time, nullable=False, default=time(18, 0))
    lunch_start = db.Column(db.Time, nullable=True)
    lunch_end = db.Column(db.Time, nullable=True)
    work_days = db.Column(db.String(20), default="0,1,2,3,4")
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

# ROTAS
@app.route('/')
def index(): return render_template('index.html')

@app.route('/cadastro-negocio', methods=['GET', 'POST'])
def register_business():
    if current_user.is_authenticated: return redirect(url_for('admin_dashboard'))
    if request.method == 'POST':
        est = Establishment(
            name=request.form.get('business_name'),
            url_prefix=request.form.get('url_prefix').lower().strip(),
            contact_phone=request.form.get('contact_phone'),
            work_days="0,1,2,3,4"
        )
        db.session.add(est)
        db.session.commit()
        adm = Admin(username=request.form.get('username'), establishment_id=est.id)
        adm.set_password(request.form.get('password'))
        db.session.add(adm)
        db.session.commit()
        flash('Conta criada!', 'success')
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
    active_days = est.work_days.split(',') if est.work_days else []
    return render_template('admin.html', appointments=appts, services=services, establishment=est, active_days=active_days)

@app.route('/admin/configurar', methods=['POST'])
@login_required
def update_settings():
    est = current_user.establishment
    est.work_start = datetime.strptime(request.form.get('work_start'), '%H:%M').time()
    est.work_end = datetime.strptime(request.form.get('work_end'), '%H:%M').time()
    est.contact_phone = request.form.get('contact_phone')
    
    ls = request.form.get('lunch_start')
    le = request.form.get('lunch_end')
    if ls and le:
        est.lunch_start = datetime.strptime(ls, '%H:%M').time()
        est.lunch_end = datetime.strptime(le, '%H:%M').time()
    else: est.lunch_start = None; est.lunch_end = None
    
    est.work_days = ",".join(request.form.getlist('work_days'))
    db.session.commit()
    flash('Configurações salvas.', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/servicos/novo', methods=['POST'])
@login_required
def add_service():
    svc = Service(name=request.form.get('name'), duration=int(request.form.get('duration')), establishment_id=current_user.establishment_id)
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
    
    if str(sel_date.weekday()) not in (est.work_days or ""): return jsonify([])

    appts = Appointment.query.filter_by(appointment_date=sel_date, establishment_id=est.id).all()
    busy = []
    if est.lunch_start and est.lunch_end:
        busy.append((datetime.combine(sel_date, est.lunch_start), datetime.combine(sel_date, est.lunch_end)))
    for a in appts:
        s = datetime.combine(sel_date, a.appointment_time)
        busy.append((s, s + timedelta(minutes=a.service_info.duration)))

    avail = []
    curr = datetime.combine(sel_date, est.work_start)
    limit = datetime.combine(sel_date, est.work_end)
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

# --- INDEX ATUALIZADO (Correção da Imagem) ---
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
                    <!-- ÁREA DA IMAGEM CORRIGIDA -->
                    <div class="relative w-full max-w-lg rounded-xl shadow-2xl ring-1 ring-gray-900/10 overflow-hidden bg-gray-50 aspect-video flex items-center justify-center">
                        
                        <!-- Tenta carregar painel.png -->
                        <img src="{{ url_for('static', filename='painel.png') }}" 
                             alt="Painel Administrativo" 
                             class="w-full h-auto object-cover"
                             onerror="this.onerror=null; this.src='{{ url_for('static', filename='painel.jpg') }}'; this.nextElementSibling.style.display='flex'; this.style.display='none';">
                        
                        <!-- Fallback se a imagem não existir -->
                        <div class="absolute inset-0 bg-gray-200 flex-col items-center justify-center text-gray-500 hidden">
                            <svg class="w-12 h-12 mb-2" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z"></path></svg>
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

# --- DEMAIS ARQUIVOS DA V5 ---
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

REGISTER_HTML = r'''{% extends 'layout.html' %}
{% block title %}Criar Conta{% endblock %}
{% block content %}
<div class="row justify-content-center mt-5">
    <div class="col-md-8 col-lg-6">
        <div class="card shadow-sm border-0 rounded-3">
            <div class="card-body p-4 p-md-5">
                <h1 class="card-title h3 mb-4 text-center fw-bold">Comece seu Teste</h1>
                <form method="POST">
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

ADMIN_HTML = r'''{% extends 'layout.html' %}
{% block title %}Painel Admin{% endblock %}
{% block content %}
<div class="container py-4">
    <div class="d-flex justify-content-between align-items-center mb-4">
        <h1 class="h3">Painel: {{ establishment.name }}</h1>
        <a href="{{ url_for('establishment_services', url_prefix=establishment.url_prefix) }}" target="_blank" class="btn btn-outline-primary btn-sm">Ver Minha Página <i class="bi bi-box-arrow-up-right"></i></a>
    </div>
    <div class="row">
        <div class="col-lg-7">
            <div class="card shadow-sm border-0 mb-4">
                <div class="card-header bg-white"><h5 class="mb-0">Próximos Agendamentos</h5></div>
                <div class="card-body p-0">
                    <table class="table table-hover mb-0">
                        <thead><tr><th>Data/Hora</th><th>Cliente</th><th>Contato</th><th>Ação</th></tr></thead>
                        <tbody>
                            {% for a in appointments %}
                            <tr>
                                <td><b>{{ a.appointment_date.strftime('%d/%m') }}</b> {{ a.appointment_time.strftime('%H:%M') }}<br><small>{{ a.service_info.name }}</small></td>
                                <td>{{ a.client_name }}</td>
                                <td><i class="bi bi-whatsapp text-success"></i> {{ a.client_phone }}</td>
                                <td><form method="POST" action="{{ url_for('delete_appointment', appointment_id=a.id) }}" onsubmit="return confirm('Cancelar?');"><button class="btn btn-sm btn-danger"><i class="bi bi-trash"></i></button></form></td>
                            </tr>
                            {% else %}<tr><td colspan="4" class="text-center py-4">Agenda livre.</td></tr>{% endfor %}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
        <div class="col-lg-5">
            <div class="card shadow-sm border-0 mb-4">
                <div class="card-header bg-white"><h5 class="mb-0">Configurações</h5></div>
                <div class="card-body">
                    <form action="{{ url_for('update_settings') }}" method="POST">
                        <label class="small fw-bold">Dias de Atendimento:</label>
                        <div class="mb-2">
                            {% for i, day in [(0,'Seg'),(1,'Ter'),(2,'Qua'),(3,'Qui'),(4,'Sex'),(5,'Sáb'),(6,'Dom')] %}
                            <div class="form-check form-check-inline"><input class="form-check-input" type="checkbox" name="work_days" value="{{ i }}" {% if i|string in active_days %}checked{% endif %}> <label class="form-check-label small">{{ day }}</label></div>
                            {% endfor %}
                        </div>
                        <div class="row g-2 mb-2"><div class="col"><label class="small">Abre</label><input type="time" class="form-control form-control-sm" name="work_start" value="{{ establishment.work_start.strftime('%H:%M') }}"></div><div class="col"><label class="small">Fecha</label><input type="time" class="form-control form-control-sm" name="work_end" value="{{ establishment.work_end.strftime('%H:%M') }}"></div></div>
                        <div class="row g-2 mb-2"><div class="col"><label class="small">Almoço Início</label><input type="time" class="form-control form-control-sm" name="lunch_start" value="{{ establishment.lunch_start.strftime('%H:%M') if establishment.lunch_start else '' }}"></div><div class="col"><label class="small">Almoço Fim</label><input type="time" class="form-control form-control-sm" name="lunch_end" value="{{ establishment.lunch_end.strftime('%H:%M') if establishment.lunch_end else '' }}"></div></div>
                        <div class="mb-2"><label class="small">WhatsApp</label><input type="text" class="form-control form-control-sm" name="contact_phone" value="{{ establishment.contact_phone or '' }}"></div>
                        <button class="btn btn-primary btn-sm w-100">Salvar</button>
                    </form>
                </div>
            </div>
            <div class="card shadow-sm border-0">
                <div class="card-header bg-white"><h5 class="mb-0">Serviços</h5></div>
                <div class="card-body">
                    <form action="{{ url_for('add_service') }}" method="POST" class="mb-3 d-flex gap-2">
                        <input type="text" name="name" class="form-control form-control-sm" placeholder="Nome" required>
                        <input type="number" name="duration" class="form-control form-control-sm" placeholder="Min" style="width:70px" required>
                        <button class="btn btn-success btn-sm">+</button>
                    </form>
                    <ul class="list-group list-group-flush small">
                        {% for s in services %}
                        <li class="list-group-item d-flex justify-content-between px-0"><span>{{ s.name }} ({{ s.duration }}min)</span><form method="POST" action="{{ url_for('delete_service', service_id=s.id) }}" onsubmit="return confirm('Excluir?');"><button class="btn btn-link text-danger p-0 border-0"><i class="bi bi-trash"></i></button></form></li>
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
        {% if establishment.contact_phone %}<p class="text-muted"><i class="bi bi-whatsapp text-success"></i> {{ establishment.contact_phone }}</p>{% endif %}
    </div>
    <div class="row justify-content-center gap-3">
        {% for s in services %}
        <div class="col-md-4">
            <div class="card shadow-sm border-0 h-100 p-3 text-center">
                <h4 class="fw-bold">{{ s.name }}</h4>
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
                <h4 class="text-center mb-4">Agendar: {{ service.name }}</h4>
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
    # CRIA A PASTA STATIC SE NÃO EXISTIR
    if not os.path.exists('static'): 
        os.makedirs('static')
        print("Pasta 'static' criada. Coloque a imagem 'painel.png' aqui.")

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
    
    print("\n[SUCESSO] Versão 7 Instalada!")
    print("Agora coloque sua imagem na pasta 'static' com o nome 'painel.png' e rode o app.")

if __name__ == "__main__":
    atualizar()