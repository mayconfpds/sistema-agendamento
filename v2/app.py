import os
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
login_manager.login_message = 'Por favor, faça login para acessar esta página.'

# --- MODELOS DO BANCO DE DADOS ---

class Establishment(db.Model):
    __tablename__ = 'establishments'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    url_prefix = db.Column(db.String(50), nullable=False, unique=True)
    
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
    client_phone = db.Column(db.String(20), nullable=False)  # CAMPO NOVO
    appointment_date = db.Column(db.Date, nullable=False)
    appointment_time = db.Column(db.Time, nullable=False)
    
    service_id = db.Column(db.Integer, db.ForeignKey('services.id'), nullable=False)
    establishment_id = db.Column(db.Integer, db.ForeignKey('establishments.id'), nullable=False)

@login_manager.user_loader
def load_user(user_id):
    return Admin.query.get(int(user_id))


# --- ROTAS GERAIS ---

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
            flash('O link deve conter apenas letras minúsculas, números e hífens.', 'danger')
            return render_template('register.html')

        if Establishment.query.filter_by(url_prefix=url_prefix).first():
            flash('Este link já está em uso.', 'danger')
            return render_template('register.html')
        
        if Admin.query.filter_by(username=username).first():
            flash('Usuário já existe.', 'danger')
            return render_template('register.html')

        new_establishment = Establishment(name=business_name, url_prefix=url_prefix)
        db.session.add(new_establishment)
        db.session.commit()

        new_admin = Admin(username=username, establishment_id=new_establishment.id)
        new_admin.set_password(password)
        db.session.add(new_admin)
        db.session.commit()

        flash('Conta criada! Faça login.', 'success')
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
        return "Serviço inválido", 404

    return render_template('agendamento.html', service=service, establishment=establishment)

@app.route('/b/<url_prefix>/confirmar', methods=['POST'])
def create_appointment(url_prefix):
    establishment = Establishment.query.filter_by(url_prefix=url_prefix).first_or_404()
    
    client_name = request.form.get('client_name')
    client_phone = request.form.get('client_phone') # CAPTURA O TELEFONE
    service_id = request.form.get('service_id')
    appointment_date_str = request.form.get('appointment_date')
    appointment_time_str = request.form.get('appointment_time')

    if not all([client_name, client_phone, service_id, appointment_date_str, appointment_time_str]):
        flash('Todos os campos são obrigatórios!', 'danger')
        return redirect(url_for('schedule_service', url_prefix=url_prefix, service_id=service_id))
    
    appointment_date = datetime.strptime(appointment_date_str, '%Y-%m-%d').date()
    appointment_time = datetime.strptime(appointment_time_str, '%H:%M').time()
    
    service = Service.query.get(service_id)
    if not service or service.establishment_id != establishment.id:
        flash('Serviço inválido.', 'danger')
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
    flash('Agendamento realizado com sucesso!', 'success')
    return redirect(url_for('establishment_services', url_prefix=url_prefix))


# --- ROTAS ADMIN ---

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
    flash('Desconectado.', 'success')
    return redirect(url_for('login'))

@app.route('/admin')
@login_required
def admin_dashboard():
    est_id = current_user.establishment_id
    today = datetime.now().date()
    
    appointments = Appointment.query.filter(
        Appointment.establishment_id == est_id,
        Appointment.appointment_date >= today
    ).order_by(Appointment.appointment_date, Appointment.appointment_time).all()
    
    services = Service.query.filter_by(establishment_id=est_id).order_by(Service.name).all()
    
    return render_template('admin.html', appointments=appointments, services=services, establishment=current_user.establishment)

@app.route('/admin/servicos/novo', methods=['POST'])
@login_required
def add_service():
    name = request.form.get('name')
    duration = request.form.get('duration')
    if name and duration:
        new_service = Service(name=name, duration=int(duration), establishment_id=current_user.establishment_id)
        db.session.add(new_service)
        db.session.commit()
        flash('Serviço adicionado!', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/servicos/excluir/<int:service_id>', methods=['POST'])
@login_required
def delete_service(service_id):
    service = Service.query.get_or_404(service_id)
    if service.establishment_id != current_user.establishment_id:
        return "Acesso Negado", 403
    db.session.delete(service)
    db.session.commit()
    flash('Serviço excluído!', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/agendamentos/excluir/<int:appointment_id>', methods=['POST'])
@login_required
def delete_appointment(appointment_id):
    appt = Appointment.query.get_or_404(appointment_id)
    if appt.establishment_id != current_user.establishment_id:
        return "Acesso Negado", 403
    db.session.delete(appt)
    db.session.commit()
    flash('Agendamento excluído!', 'success')
    return redirect(url_for('admin_dashboard'))


# --- API ---

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

    appointments_on_date = Appointment.query.filter_by(
        appointment_date=selected_date,
        establishment_id=service.establishment_id
    ).all()
    
    work_start = time(9, 0)
    work_end = time(18, 0)
    
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

    while curr + service_dur <= limit:
        end_slot = curr + service_dur
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