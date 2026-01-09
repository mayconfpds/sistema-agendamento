import os
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