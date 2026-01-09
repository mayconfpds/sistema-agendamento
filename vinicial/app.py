import os
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, time, timedelta

# --- CONFIGURAÇÃO ---
app = Flask(__name__)
# Criação de uma chave secreta para segurança da sessão
app.config['SECRET_KEY'] = 'uma-chave-secreta-muito-segura-trocar-em-producao'
# Configuração do caminho do banco de dados SQLite
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'agendamento.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Por favor, faça login para acessar esta página.'

# --- MODELOS DO BANCO DE DADOS (SQLAlchemy) ---

class Service(db.Model):
    __tablename__ = 'services'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    duration = db.Column(db.Integer, nullable=False) # Duração em minutos
    appointments = db.relationship('Appointment', backref='service_info', lazy=True, cascade="all, delete-orphan")

    def __repr__(self):
        return f'<Service {self.name}>'

class Appointment(db.Model):
    __tablename__ = 'appointments'
    id = db.Column(db.Integer, primary_key=True)
    client_name = db.Column(db.String(150), nullable=False)
    appointment_date = db.Column(db.Date, nullable=False)
    appointment_time = db.Column(db.Time, nullable=False)
    service_id = db.Column(db.Integer, db.ForeignKey('services.id'), nullable=False)

    def __repr__(self):
        return f'<Appointment for {self.client_name} at {self.appointment_date} {self.appointment_time}>'

# Modelo de Usuário para o painel de admin
class Admin(UserMixin, db.Model):
    __tablename__ = 'admins'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

# Função para carregar o usuário da sessão
@login_manager.user_loader
def load_user(user_id):
    return Admin.query.get(int(user_id))


# --- ROTAS PÚBLICAS (CLIENTES) ---

@app.route('/')
def index():
    """Página inicial (Landing Page) que apresenta o software 'Agenda Fácil'."""
    # Esta página agora é estática e não precisa de dados do banco
    return render_template('index.html')

@app.route('/agendar')
def agendar_lista_servicos():
    """Página que lista todos os serviços disponíveis para agendamento."""
    # Esta é a funcionalidade da sua ANTIGA página index.html
    services = Service.query.order_by(Service.name).all()
    return render_template('lista_servicos.html', services=services)

@app.route('/agendar/<int:service_id>')
def schedule_service(service_id):
    """Página para o cliente escolher a data e o horário de um serviço específico."""
    service = Service.query.get_or_404(service_id)
    return render_template('agendamento.html', service=service)

@app.route('/agendar', methods=['POST'])
def create_appointment():
    """Processa o formulário de agendamento enviado pelo cliente."""
    client_name = request.form.get('client_name')
    service_id = request.form.get('service_id')
    appointment_date_str = request.form.get('appointment_date')
    appointment_time_str = request.form.get('appointment_time')

    if not all([client_name, service_id, appointment_date_str, appointment_time_str]):
        flash('Todos os campos são obrigatórios!', 'danger')
        return redirect(url_for('schedule_service', service_id=service_id))
    
    appointment_date = datetime.strptime(appointment_date_str, '%Y-%m-%d').date()
    appointment_time = datetime.strptime(appointment_time_str, '%H:%M').time()

    new_appointment = Appointment(
        client_name=client_name,
        service_id=service_id,
        appointment_date=appointment_date,
        appointment_time=appointment_time
    )
    db.session.add(new_appointment)
    db.session.commit()
    flash('Agendamento realizado com sucesso!', 'success')
    # Redireciona para a lista de serviços após agendar
    return redirect(url_for('agendar_lista_servicos'))

# --- ROTAS DE ADMINISTRAÇÃO ---

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Página de login para o administrador."""
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
            flash('Usuário ou senha inválidos.', 'danger')

    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    """Faz o logout do administrador."""
    logout_user()
    flash('Você foi desconectado.', 'success')
    return redirect(url_for('login'))

@app.route('/admin')
@login_required
def admin_dashboard():
    """Painel principal do administrador, exibe agendamentos e serviços."""
    today = datetime.now().date()
    appointments = Appointment.query.filter(Appointment.appointment_date >= today).order_by(Appointment.appointment_date, Appointment.appointment_time).all()
    services = Service.query.order_by(Service.name).all()
    return render_template('admin.html', appointments=appointments, services=services)

# CRUD de Serviços
@app.route('/admin/servicos/novo', methods=['POST'])
@login_required
def add_service():
    name = request.form.get('name')
    duration = request.form.get('duration')
    if name and duration:
        new_service = Service(name=name, duration=int(duration))
        db.session.add(new_service)
        db.session.commit()
        flash('Serviço adicionado com sucesso!', 'success')
    else:
        flash('Nome e duração são obrigatórios.', 'danger')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/servicos/editar/<int:service_id>', methods=['POST'])
@login_required
def edit_service(service_id):
    service = Service.query.get_or_404(service_id)
    name = request.form.get('name')
    duration = request.form.get('duration')
    if name and duration:
        service.name = name
        service.duration = int(duration)
        db.session.commit()
        flash('Serviço atualizado com sucesso!', 'success')
    else:
        flash('Nome e duração são obrigatórios.', 'danger')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/servicos/excluir/<int:service_id>', methods=['POST'])
@login_required
def delete_service(service_id):
    service = Service.query.get_or_404(service_id)
    db.session.delete(service)
    db.session.commit()
    flash('Serviço excluído com sucesso!', 'success')
    return redirect(url_for('admin_dashboard'))

# Exclusão de Agendamento
@app.route('/admin/agendamentos/excluir/<int:appointment_id>', methods=['POST'])
@login_required
def delete_appointment(appointment_id):
    appointment = Appointment.query.get_or_404(appointment_id)
    db.session.delete(appointment)
    db.session.commit()
    flash('Agendamento excluído com sucesso!', 'success')
    return redirect(url_for('admin_dashboard'))


# --- API INTERNA PARA HORÁRIOS ---

@app.route('/api/horarios_disponiveis')
def get_available_times():
    """
    API que calcula e retorna os horários disponíveis para um dado serviço e data.
    Esta é a lógica central do sistema de agendamento.
    """
    service_id = request.args.get('service_id', type=int)
    date_str = request.args.get('date')

    if not service_id or not date_str:
        return jsonify({'error': 'Serviço e data são obrigatórios'}), 400

    try:
        selected_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        return jsonify({'error': 'Formato de data inválido'}), 400

    service = Service.query.get(service_id)
    if not service:
        return jsonify({'error': 'Serviço não encontrado'}), 404

    # Horário de funcionamento (fixo nesta versão)
    work_start_time = time(9, 0)
    work_end_time = time(18, 0)

    # Buscar todos os agendamentos para a data selecionada
    appointments_on_date = Appointment.query.filter_by(appointment_date=selected_date).all()
    
    # Criar uma lista de blocos de tempo ocupados
    busy_slots = []
    for appt in appointments_on_date:
        # Verifica se o serviço do agendamento ainda existe
        if appt.service_info:
            appt_service_duration = appt.service_info.duration
        else:
            # Se o serviço foi deletado, usa a duração padrão
            appt_service_duration = 30 
            
        start = datetime.combine(selected_date, appt.appointment_time)
        end = start + timedelta(minutes=appt_service_duration)
        busy_slots.append((start, end)) # Alterado para usar datetime completo

    # Calcular os horários disponíveis
    available_slots = []
    service_duration = timedelta(minutes=service.duration)
    
    # Começa a verificar a partir do início do dia de trabalho
    potential_start_dt = datetime.combine(selected_date, work_start_time)
    work_end_dt = datetime.combine(selected_date, work_end_time)

    # Define o intervalo de verificação (ex: 15 min)
    check_interval = timedelta(minutes=15)

    while potential_start_dt + service_duration <= work_end_dt:
        potential_end_dt = potential_start_dt + service_duration
        is_slot_available = True
        
        # Verifica se o slot potencial se sobrepõe a algum bloco ocupado
        for busy_start_dt, busy_end_dt in busy_slots:
            # Condição de sobreposição: max(start1, start2) < min(end1, end2)
            if max(potential_start_dt, busy_start_dt) < min(potential_end_dt, busy_end_dt):
                is_slot_available = False
                break
        
        if is_slot_available:
            available_slots.append(potential_start_dt.strftime('%H:%M'))
        
        # Avança para o próximo slot de verificação
        potential_start_dt += check_interval

    return jsonify(available_slots)


# --- INICIALIZAÇÃO E CRIAÇÃO DO BANCO ---
if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        # Cria um usuário admin padrão se não existir
        if not Admin.query.filter_by(username='admin').first():
            admin_user = Admin(username='admin')
            admin_user.set_password('senha123')
            db.session.add(admin_user)
            db.session.commit()
            print("Usuário 'admin' com senha 'senha123' criado.")
    app.run(debug=True)