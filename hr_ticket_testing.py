from flask import Flask, render_template, render_template_string, request, redirect, url_for, flash, jsonify , send_from_directory
from flask_mail import Mail, Message
from werkzeug.utils import secure_filename
import os
import psycopg2
from psycopg2 import pool
from datetime import datetime, timedelta
import uuid
import traceback2 as traceback
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
import time
import random
import string
from flask import session, make_response
import smtplib
import requests
from requests.auth import HTTPBasicAuth
from markupsafe import Markup
import re
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders

load_dotenv()

app = Flask(__name__)

REMINDER_INITIAL_THRESHOLD_HOURS = 72       
REMINDER_REPEAT_EVERY_HOURS = 72             # repeat cadence (None to disable)
REMINDER_SCAN_INTERVAL_HOURS = 1             # scan frequency
DAILY_SUMMARY_HOURS = [9, 16]                # 09:00 & 16:00 HR summaries
MAX_ITEMS_IN_DAILY_EMAIL = 40                
EMPLOYEE_MAX_REPLIES = 2                     # employee reply cap (instead of edit)

def nl2br_filter(text):
    if text is None:
        return ''
    from markupsafe import escape
    text = escape(text)
    text = re.sub(r'(\r\n|\r|\n)', '<br>', str(text))
    return Markup(text)

app.jinja_env.filters['nl2br'] = nl2br_filter

app.secret_key = os.environ.get('SECRET_KEY')

SERVER_HOST = os.environ.get('SERVER_HOST')

app.config['SERVER_NAME'] = os.environ.get('SERVER_NAME')
app.config['PREFERRED_URL_SCHEME'] = os.environ.get('PREFERRED_URL_SCHEME', 'http')
app.config['APPLICATION_ROOT'] = '/'

basedir = os.path.abspath(os.path.dirname(__file__))
UPLOAD_FOLDER = os.path.join(basedir, 'uploads')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'pdf', 'doc', 'docx'}

app.config['DB_CONFIG'] = {
    'dbname': os.environ.get('DB_NAME'),
    'user': os.environ.get('DB_USER'),
    'password': os.environ.get('DB_PASSWORD'),
    'host': os.environ.get('DB_HOST'),
    'port': os.environ.get('DB_PORT')
}

db_pool = pool.SimpleConnectionPool(
    1, 20, **app.config['DB_CONFIG']
)

app.config['MAIL_SERVER'] = os.environ.get('MAIL_SERVER')
app.config['MAIL_PORT'] = os.environ.get('MAIL_PORT')
app.config['MAIL_USE_TLS'] = os.environ.get('MAIL_USE_TLS')
app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD')
app.config['MAIL_DEFAULT_SENDER'] = os.environ.get('MAIL_DEFAULT_SENDER')

app.config['SESSION_TYPE'] = 'filesystem'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=2)

mail = Mail(app)

GRIEVANCE_TYPES = {
    'leave_attendance': 'Leave & Attendance',
    'salary_queries': 'Salary Queries',
    'policies': 'Policies',
    'reimbursement': 'Reimbursement',
    'weconnect': 'Weconnect',
    'promotion_performance': 'Promotion & Performance Appraisals',
    'npower_issue': 'Npower related Issue',
    'ascent_issue': 'Ascent related Issue',
    'hr_forms_documents': 'HR Forms & Documents',
    'exit_settlement': 'Exit & Final Settlement',
    'letters': 'Letters',
    'confirmation': 'Confirmation',
    'others': 'Others',
    'recruitment': 'Recruitment',
    'provident_fund': 'Provident Fund',
    'mediclaim': 'Mediclaim',
    'canteen_food': 'Canteen Food',
    'transportation': 'Transportation',
    'reward': 'Reward',
    'address_proof': 'Address Proof',
    'id_card': 'ID Card',
    'income_tax': 'Income Tax related Queries',
    'testing1':'testing1',
    'testing2':'testing2'
}

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def get_upload_path(user_type, emp_code):
    """
    Returns the directory path for uploads based on user type and emp_code.
    Example: uploads/employee/EMP001/ or uploads/hr/HR001/
    """
    base_dir = os.path.join(UPLOAD_FOLDER, user_type, emp_code)
    os.makedirs(base_dir, exist_ok=True)
    return base_dir

def init_db():
    conn = db_pool.getconn()
    try:
        with conn.cursor() as c:
            c.execute('''CREATE TABLE IF NOT EXISTS grievances
                        (id TEXT PRIMARY KEY,
                         emp_code TEXT NOT NULL,
                         employee_name TEXT NOT NULL,
                         employee_email TEXT NOT NULL,
                         employee_phone TEXT,
                         business_unit TEXT,
                         department TEXT,
                         grievance_type TEXT NOT NULL,
                         subject TEXT NOT NULL,
                         description TEXT NOT NULL,
                         attachment_path TEXT,
                         submission_date TIMESTAMP NOT NULL,
                         status TEXT DEFAULT 'Submitted',
                         created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                         updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')

            c.execute('''CREATE TABLE IF NOT EXISTS responses
                        (id SERIAL PRIMARY KEY,
                         grievance_id TEXT,
                         responder_email TEXT NOT NULL,
                         responder_name TEXT,
                         response_text TEXT NOT NULL,
                         response_date TIMESTAMP NOT NULL,
                         attachment_path TEXT,
                         created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                         FOREIGN KEY (grievance_id) REFERENCES grievances(id))''')

            c.execute('''DO $$
                        BEGIN
                            IF NOT EXISTS (
                                SELECT 1
                                FROM information_schema.columns
                                WHERE table_name='responses'
                                AND column_name='attachment_path'
                            ) THEN
                                ALTER TABLE responses
                                ADD COLUMN attachment_path TEXT;
                            END IF;
                        END $$;''')

            c.execute('''CREATE TABLE IF NOT EXISTS feedback
                        (id SERIAL PRIMARY KEY,
                         grievance_id TEXT UNIQUE,
                         satisfaction TEXT,
                         rating INTEGER,
                         feedback_comments TEXT,
                         feedback_date TIMESTAMP,
                         created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                         FOREIGN KEY (grievance_id) REFERENCES grievances(id))''')

            c.execute('CREATE INDEX IF NOT EXISTS idx_grievance_id ON grievances(id)')
            c.execute('CREATE INDEX IF NOT EXISTS idx_response_grievance_id ON responses(grievance_id)')
            c.execute('CREATE INDEX IF NOT EXISTS idx_feedback_grievance_id ON feedback(grievance_id)')

            c.execute('''CREATE TABLE IF NOT EXISTS reminder_sent
                        (id SERIAL PRIMARY KEY,
                         grievance_id TEXT UNIQUE,
                         reminder_date TIMESTAMP NOT NULL,
                         created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                         FOREIGN KEY (grievance_id) REFERENCES grievances(id))''')

            c.execute('CREATE INDEX IF NOT EXISTS idx_reminder_grievance_id ON reminder_sent(grievance_id)')

            c.execute('''CREATE TABLE IF NOT EXISTS users
                        (id SERIAL PRIMARY KEY,
                         emp_code TEXT UNIQUE NOT NULL,
                         employee_name TEXT NOT NULL,
                         employee_phone TEXT NOT NULL,
                         employee_email TEXT,
                         role TEXT NOT NULL DEFAULT 'employee',
                         is_active BOOLEAN DEFAULT TRUE,
                         last_login TIMESTAMP,
                         created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')

            c.execute('CREATE INDEX IF NOT EXISTS idx_user_emp_code ON users(emp_code)')

            c.execute('''CREATE TABLE IF NOT EXISTS hr_grievance_mapping
                        (id SERIAL PRIMARY KEY,
                         grievance_type TEXT NOT NULL UNIQUE,
                         hr_emp_code TEXT NOT NULL,
                         created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                         FOREIGN KEY (hr_emp_code) REFERENCES users(emp_code))''')

            c.execute('''
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1
                        FROM information_schema.columns
                        WHERE table_name='grievances'
                        AND column_name='date_of_birth'
                    ) THEN
                        ALTER TABLE grievances
                        ADD COLUMN date_of_birth DATE;
                    END IF;
                    END $$;''')

            c.execute('''
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1
                        FROM pg_constraint
                        WHERE conname = 'hr_grievance_mapping_grievance_type_key'
                    ) THEN
                        ALTER TABLE hr_grievance_mapping ADD CONSTRAINT hr_grievance_mapping_grievance_type_key UNIQUE (grievance_type);
                    END IF;
                END
                $$;
            ''')
            c.execute('''
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1
                        FROM information_schema.columns
                        WHERE table_name='grievances'
                        AND column_name='edit_count'
                    ) THEN
                        ALTER TABLE grievances
                        ADD COLUMN edit_count INTEGER DEFAULT 0;
                    END IF;
                END $$;
            ''')
            c.execute('''
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name='grievances' AND column_name='reply_count'
                    ) THEN
                        ALTER TABLE grievances ADD COLUMN reply_count INTEGER DEFAULT 0;
                    END IF;
                END $$;
            ''')

            hr_users = [
                ('HR001', 'HR1', '8318436133', 'romil.agarwal@nvtpower.com', 'hr'),
                ('HR002', 'HR2', '8318436133', 'snehil.satyam@nvtpower.com', 'hr'),
                ('9022246', 'Mr. Sunil', '7419990925', 'sunil.kumar@nvtpower.com', 'hr'),
                ('9023574', 'Mr. Dipanshu', '9306709009', 'Dipanshu.Dhiman@nvtpower.com', 'hr'),
                ('9025263', 'Mrs. Priyanka', '9967040263', 'Priyanka.Mehta@nvtpower.com', 'hr'),
                ('9025432', 'Ms. Yashica', '9138699004', 'yashica.garg@nvtpower.com', 'hr'),
                ('9025398', 'Mr. Saveen', '8800505557', 'Saveen.Bhutani@nvtpower.com', 'hr'),
                ('9025649', 'Ms. Taru', '7217701675', 'taru.kaushik@nvtpower.com', 'hr'),
                ('9022826', 'Mr. Pawan', '9765497863', 'pawan.tyagi@nvtpower.com', 'hr'),
                ('9023649', 'Mr. Jayesh', '8383010034', 'jayesh.sinha@nvtpower.com', 'hr'),
                ('9025398', 'Mr. Saveen', '8800505557', 'Saveen.Bhutani@nvtpower.com', 'admin'),
                ('9023422', 'Mr. Mohit Agarwal', '7622011462', 'mohit.agarwal@nvtpower.com', 'admin'),
                ('ADMIN001', 'System Admin', '8318436133', 'romil.agarwal@nvtpower.com', 'admin')
            ]

            for user in hr_users:
                c.execute('''INSERT INTO users
                (emp_code, employee_name, employee_phone, employee_email, role)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (emp_code) DO UPDATE SET
                    employee_name = EXCLUDED.employee_name,
                    employee_email = EXCLUDED.employee_email,
                    employee_phone = EXCLUDED.employee_phone,
                    role = EXCLUDED.role
              ''', user)

            grievance_mappings = [
                ('leave_attendance', 'HR001'),
                ('salary_queries', '9023574'),
                ('policies', '9025263'),
                ('reimbursement', '9025432'),
                ('weconnect', '9025432'),
                ('promotion_performance', '9025398'),
                ('npower_issue', '9022246'),
                ('ascent_issue', '9022246'),
                ('hr_forms_documents', '9025649'),
                ('exit_settlement', '9023574'),
                ('letters', '9025649'),
                ('confirmation', '9022246'),
                ('others', '9022246'),
                ('recruitment', '9025263'),
                ('provident_fund', '9023574'),
                ('mediclaim', '9023574'),
                ('canteen_food', '9022826'),
                ('transportation', '9023649'),
                ('reward', '9022246'),
                ('address_proof', '9022246'),
                ('id_card', '9025649'),
                ('income_tax', '9023574'),
                ('testing1','HR001'),
                ('testing2','HR002')
            ]

            for mapping in grievance_mappings:
                c.execute('''
                    INSERT INTO hr_grievance_mapping (grievance_type, hr_emp_code)
                    VALUES (%s, %s)
                    ON CONFLICT (grievance_type) DO UPDATE
                    SET hr_emp_code = EXCLUDED.hr_emp_code
                ''', mapping)

            c.execute('CREATE INDEX IF NOT EXISTS idx_hr_mapping_grievance_type ON hr_grievance_mapping(grievance_type)')

        conn.commit()
    except psycopg2.Error as e:
        print(f"Database initialization error: {str(e)}")
        raise
    finally:
        db_pool.putconn(conn)
        
def send_email_flask_mail(to_email, subject, body, attachment_path=None):
    if not to_email:
        print("\n" + "="*60)
        print("📭 NO EMAIL PROVIDED – SKIPPING SMTP SEND (WhatsApp-only flow).")
        print(f" Subject: {subject}")
        print(" Returning success=True so process continues.\n" + "="*60)
        return True
    print("\n" + "="*60)
    print("📧 SMTP EMAIL SENDING STARTED")
    print("="*60)
    
    MAX_RETRIES = 3
    BASE_DELAY = 5
    retry_count = 0
    
    smtp_server = os.environ.get('MAIL_SERVER')
    smtp_port = int(os.environ.get('MAIL_PORT'))
    smtp_username = os.environ.get('MAIL_USERNAME')
    from_email = smtp_username
    use_tls = os.environ.get('USE_TLS').lower() == 'true'
    
    while retry_count < MAX_RETRIES:
        try:
            print(f"🔧 SMTP CONFIGURATION:")
            print(f" SMTP Server: {smtp_server}")
            print(f" SMTP Port: {smtp_port}")
            print(f" Use TLS: {use_tls}")
            print(f" From Email: {from_email}")
            print(f" To Email: {to_email}")
            print(f" Subject: {subject}")
            print(f" Has Attachment: {attachment_path is not None}")
            
            # Create message
            msg = MIMEMultipart()
            msg['From'] = from_email
            msg['To'] = to_email
            msg['Subject'] = subject
            
            # Attach HTML body
            msg.attach(MIMEText(body, 'html'))
            print(f"✅ HTML body attached (length: {len(body)} characters)")
            
            # Handle attachment
            if attachment_path and os.path.exists(attachment_path):
                print(f"\n📎 ATTACHING FILE...")
                try:
                    with open(attachment_path, "rb") as attachment:
                        part = MIMEBase('application', 'octet-stream')
                        part.set_payload(attachment.read())
                    
                    encoders.encode_base64(part)
                    part.add_header(
                        'Content-Disposition',
                        f'attachment; filename= {os.path.basename(attachment_path)}'
                    )
                    msg.attach(part)
                    print(f"✅ Attachment added: {os.path.basename(attachment_path)}")
                except Exception as attachment_error:
                    print(f"❌ ATTACHMENT ERROR: {str(attachment_error)}")
                    print(f" Continuing without attachment...")
            elif attachment_path:
                print(f"⚠️ Attachment path provided but file doesn't exist: {attachment_path}")
            
            # Create SMTP connection (no authentication for internal server)
            print(f"\n🔗 CONNECTING TO SMTP SERVER... (Attempt {retry_count + 1}/{MAX_RETRIES})")
            server = smtplib.SMTP(smtp_server, smtp_port)
            
            # No TLS and no authentication for internal server
            print(f"🔓 Using internal server - no TLS/authentication required")
            
            # Prepare recipient list (to + cc)
            recipients = [to_email]
            
            print(f"📤 Sending email to {len(recipients)} recipients...")
            text = msg.as_string()
            server.sendmail(from_email, recipients, text)
            server.quit()
            
            print(f"✅ Email sent successfully using internal SMTP server!")
            print(f"\n🎉 EMAIL SENDING COMPLETED SUCCESSFULLY!")
            print("="*60)
            return True
            
        except smtplib.SMTPConnectError as e:
            print(f"\n❌ SMTP CONNECTION ERROR:")
            print(f" Error: {str(e)}")
            print(f" Server: {smtp_server}:{smtp_port}")
            if retry_count < MAX_RETRIES - 1:
                retry_delay = BASE_DELAY * (2 ** retry_count)
                print(f"⏳ Retrying in {retry_delay} seconds... (Attempt {retry_count + 1}/{MAX_RETRIES})")
                time.sleep(retry_delay)
                retry_count += 1
            else:
                print("="*60)
                return False
                
        except smtplib.SMTPRecipientsRefused as e:
            print(f"\n❌ SMTP RECIPIENTS REFUSED:")
            print(f" Error: {str(e)}")
            print(f" Check recipient email addresses")
            print("="*60)
            return False
            
        except Exception as e:
            print(f"\n❌ SMTP SENDING ERROR:")
            print(f" Error type: {type(e).__name__}")
            print(f" Error message: {str(e)}")
            import traceback
            print(f"\n🔍 FULL TRACEBACK:")
            print(f" {traceback.format_exc()}")
            
            if retry_count < MAX_RETRIES - 1:
                retry_delay = BASE_DELAY * (2 ** retry_count)
                print(f"⏳ Retrying in {retry_delay} seconds... (Attempt {retry_count + 1}/{MAX_RETRIES})")
                time.sleep(retry_delay)
                retry_count += 1
            else:
                print("="*60)
                return False
    
    print("="*60)
    return False


def send_whatsapp_template(to_phone, template_name, lang_code, parameters):
    """
    Send a WhatsApp template message using Meta's Cloud API v22.0.
    :param to_phone: Recipient phone number in international format, e.g. '919999999999'
    :param template_name: Name of the approved template, e.g. 'otp_login_verification'
    :param lang_code: Language code, e.g. 'en'
    :param parameters: List of text values for the template placeholders (in order)
    :return: True if sent, False otherwise
    """
    phone_number_id = os.environ.get('WHATSAPP_PHONE_NUMBER_ID')
    access_token = os.environ.get('META_ACCESS_TOKEN')
    url = f"https://graph.facebook.com/v22.0/{phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    
    
    payload = {
        "messaging_product": "whatsapp",
        "to": to_phone,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": lang_code}
        }
    }
    
    
    components = []
    
    if parameters:
        if template_name == "otp_login_verification" and len(parameters) >= 1:
            components.append({
                "type": "body",
                "parameters": [
                    {"type": "text", "text": str(parameters[0])},  
                ]
            })
            components.append({
                "type": "button",
                "sub_type": "url",
                "index": 0,
                "parameters": [{"type": "text", "text": str(parameters[0])}]
            })
        elif template_name != "hello_world_private":
            components.append({"type": "body", "parameters": [{"type": "text", "text": str(val)} for val in parameters]})
    
    if components:
        payload["template"]["components"] = components
    
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=10)
        print(f"WhatsApp API response: {resp.status_code} {resp.text}")
        if resp.status_code == 200:
            response_data = resp.json()
            message_id = response_data.get('messages', [{}])[0].get('id', 'N/A')
            print(f"✅ {template_name} sent successfully! Message ID: {message_id}")
            return True
        else:
            print(f"❌ Failed to send {template_name}")
            return False
    except Exception as e:
        print(f"WhatsApp API error: {e}")
        return False

@app.route('/run-check')
def run_check():
    with app.app_context():
        check_pending_grievances()
    return "Check completed! See console for details."

@app.route('/')
def index():
    user = session.get('user')

    if user and user.get('authenticated'):
        if user.get('role') == 'admin':
            return redirect(url_for('master_dashboard'))
        elif user.get('role') == 'hr':
            return redirect(url_for('hr_dashboard'))
        else:
            return redirect(url_for('my_queries'))

    return render_template('index.html', grievance_types=GRIEVANCE_TYPES)

@app.route('/dashboard')
def dashboard():
    conn = db_pool.getconn()
    try:
        with conn.cursor() as c:
            c.execute('''SELECT id, emp_code, employee_name, employee_email, grievance_type,
                        subject, status, submission_date FROM grievances
                        ORDER BY submission_date DESC''')
            grievances = c.fetchall()
    finally:
        db_pool.putconn(conn)
    return render_template('dashboard.html', grievances=grievances, grievance_types=GRIEVANCE_TYPES)

@app.route('/submit', methods=['POST'])
def submit_grievance():
    try:
        print("\n" + "🚀" + "="*58)
        print("📝 GRIEVANCE SUBMISSION STARTED")
        print("="*60)

        grievance_id = str(uuid.uuid4())[:8]
        print(f"🆔 Generated Grievance ID: {grievance_id}")

        emp_code = request.form.get('emp_code')
        employee_name = request.form.get('employee_name')
        employee_email = request.form.get('employee_email')
        employee_phone = request.form.get('employee_phone')
        date_of_birth = request.form.get('date_of_birth')
        business_unit = request.form.get('business_unit')
        department = request.form.get('department')
        grievance_type = request.form.get('grievance_type')
        subject = request.form.get('subject')
        description = request.form.get('description')

        print(f"\n📋 FORM DATA RECEIVED:")
        print(f"   Employee Code: {emp_code}")
        print(f"   Employee Name: {employee_name}")
        print(f"   Employee Email: {employee_email}")
        print(f"   Employee Phone: {employee_phone}")
        print(f"   Business Unit: {business_unit}")
        print(f"   Department: {department}")
        print(f"   Grievance Type: {grievance_type}")
        print(f"   Subject: {subject}")
        print(f"   Description Length: {len(description) if description else 0} characters")

        required_fields = [emp_code, employee_name, grievance_type, subject, description]
        contact_provided = employee_email or employee_phone  # assuming phone_number is your variable

        if not all(required_fields) or not contact_provided:
            print(f"❌ VALIDATION FAILED: Missing required fields")
            flash('Please fill in all required fields. Either email or phone number must be provided.', 'error')
            return redirect(url_for('index'))

        print(f"✅ Form validation passed")
        print(f"\n🔍 CHECKING FOR PENDING FEEDBACK...")
        conn = db_pool.getconn()
        try:
            with conn.cursor() as c:
                # Check if user has any resolved queries without feedback
                c.execute('''
                    SELECT g.id, g.subject 
                    FROM grievances g
                    LEFT JOIN feedback f ON g.id = f.grievance_id
                    WHERE g.emp_code = %s AND g.status = %s AND f.grievance_id IS NULL
                ''', (emp_code, 'Resolved'))
                
                pending_feedback = c.fetchall()
                
                if pending_feedback:
                    print(f"❌ User {emp_code} has {len(pending_feedback)} resolved queries without feedback")
                    pending_query_ids = [query[0] for query in pending_feedback]
                    
                    flash(f'You have {len(pending_feedback)} resolved queries pending feedback. Please submit feedback for queries: {", ".join(pending_query_ids)} before submitting a new query.', 'error')
                    return redirect(url_for('index'))
                
                print(f"✅ No pending feedback found for user {emp_code}")
        finally:
            db_pool.putconn(conn)

        attachment_path = None
        if 'attachment' in request.files:
            file = request.files['attachment']
            print(f"\n📁 FILE UPLOAD:")
            print(f"   File present: {file is not None}")
            print(f"   Filename: {file.filename if file else 'None'}")

            if file and file.filename != '' and allowed_file(file.filename):
                upload_dir = get_upload_path('employee', emp_code)
                original_filename = secure_filename(file.filename)
                file_extension = original_filename.rsplit('.', 1)[1].lower()
                filename = f"{grievance_id}.{file_extension}"        
                full_path = os.path.join(upload_dir, filename)                
                file.save(full_path)                
                attachment_path = filename  
                print(f"   ✅ File saved: {attachment_path}")
                print(f"   File size: {os.path.getsize(full_path)} bytes")
            elif file and file.filename != '':
                print(f"   ❌ File type not allowed: {file.filename}")

        print(f"\n💾 SAVING TO DATABASE...")
        conn = db_pool.getconn()
        try:
            with conn.cursor() as c:
                c.execute('''INSERT INTO grievances
                            (id, emp_code, employee_name, employee_email, employee_phone,date_of_birth,
                            business_unit, department, grievance_type, subject, description,
                            attachment_path, submission_date)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)''',
                         (grievance_id, emp_code, employee_name, employee_email, employee_phone,
                          date_of_birth, business_unit, department, grievance_type, subject, description,
                          attachment_path, datetime.now()))
                conn.commit()
            print(f"✅ Data saved to database")
        finally:
            db_pool.putconn(conn)

        print(f"\n📧 PREPARING EMAIL NOTIFICATION...")
        email_subject = f"New Query Submitted - {subject} (ID: {grievance_id})"

        hr_email = None
        try:
            with conn.cursor() as c:
                c.execute('''
                    SELECT u.employee_email, u.employee_name , u.employee_phone
                    FROM hr_grievance_mapping m
                    JOIN users u ON m.hr_emp_code = u.emp_code
                    WHERE m.grievance_type = %s
                ''', (grievance_type,))

                hr_info = c.fetchone()
                if hr_info and hr_info[0]:
                    hr_email = hr_info[0]
                    hr_name = hr_info[1]
                    hr_phone = hr_info[2]
                    print(f"✅ Found HR email: {hr_email} and HR phone: {hr_phone} for grievance type: {grievance_type}")
                else:
                    hr_email = 'mohit.agarwal@nvtpower.com'
                    hr_name = 'Mohit Agarwal'
                    print(f"⚠️ No HR mapping found for type: {grievance_type}, using default")
        except Exception as e:
            hr_email = 'mohit.agarwal@nvtpower.com'
            hr_name = 'Mohit Agarwal'
            print(f"❌ Error finding HR email: {str(e)}")

        email_body = f"""
<html>
<body style="font-family: Arial, sans-serif; line-height: 1.6; color: #2c3e50;">
    <div style="max-width: 600px; margin: 0 auto; padding: 20px; background: #f7fafc; border-radius: 8px;">
        <h2 style="color: #1e3a8a;">Dear {hr_name},</h2>
        <p>Below query has been submitted by {employee_name}:</p>
        <div style="background: white; padding: 20px; border-radius: 8px;">
            <p><strong>Reference ID:</strong> {grievance_id}</p>
            <p><strong>Subject:</strong> {subject}</p>
            <p><strong>Status:</strong> Submitted</p>
            <p><strong>Submission Date:</strong> {datetime.now().strftime('%d-%m-%Y, %H:%M:%S')}</p>
        </div>
        <p>Please login into the Ask HR Portal to resolve the same.</p>
        <p><strong>Human Resources</strong></p>
    </div>
</body>
</html>
"""

        print(f"✅ Email content prepared")
        email_success = send_email_flask_mail(hr_email, email_subject, email_body, full_path if file and file.filename != '' and allowed_file(file.filename) else None)
        if hr_phone:
            print(f"📱 Sending WhatsApp notification to HR...")
            whatsapp_success = send_whatsapp_template(
                to_phone=hr_phone,
                template_name="new_grievance_notification_hr",
                lang_code="en",
                parameters=[
                    hr_name,
                    employee_name,
                    grievance_id,
                    subject,
                    datetime.now().strftime('%d-%m-%Y, %H:%M:%S')
                ]
            )
            if whatsapp_success:
                print(f"✅ WhatsApp notification sent successfully!")
            else:
                print(f"❌ Failed to send WhatsApp notification to HR")
        if email_success:
            print(f"✅ EMAIL SENT SUCCESSFULLY!")
            employee_email_body = f"""
            <html>
            <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #2c3e50;">
                <div style="max-width: 600px; margin: 0 auto; padding: 20px; background: #f7fafc; border-radius: 8px;">
                    <h2 style="color: #1e3a8a;"> Query Submission : Ask HR </h2>
                    <p> Dear {employee_name},<p>
                    <p>Your query has been successfully submitted with the following details:</p>
                    <div style="background: white; padding: 20px; border-radius: 8px;">
                        <p><strong>Reference ID:</strong> {grievance_id}</p>
                        <p><strong>Subject:</strong> {subject}</p>
                        <p><strong>Status:</strong> Submitted</p>
                        <p><strong>Submission Date:</strong> {datetime.now().strftime('%d-%m-%Y, %H:%M:%S')}</p>
                    </div>
                    <p>Please keep the Reference ID for tracking the Query Status.</p>
                    <p><strong>Human Resources</strong></p>
                </div>
            </body>
            </html>
            """
            send_email_flask_mail(employee_email, f"Query Submission Confirmation (ID: {grievance_id})", employee_email_body)

            if employee_phone:
                send_whatsapp_template(
                    to_phone=employee_phone,
                    template_name="grievance_submission_confirmation",
                    lang_code="en",
                    parameters=[
                        employee_name,
                        grievance_id,
                        subject,
                        datetime.now().strftime('%d-%m-%Y, %H:%M:%S')
                    ]
                )

            flash(f'Your query has been submitted successfully! Reference ID: {grievance_id}', 'success')
        else:
            print(f"❌ EMAIL SENDING FAILED!")
            flash(f'Query submitted (ID: {grievance_id}) but email notification failed. Please contact HR support.', 'warning')

        print(f"\n🎉 GRIEVANCE SUBMISSION COMPLETED")
        print("="*60)
        return redirect(url_for('index'))

    except Exception as e:
        print(f"\n💥 SUBMISSION ERROR:")
        print(f"   Error: {str(e)}")
        import traceback
        print(f"   Traceback: {traceback.format_exc()}")
        print("="*60)
        flash(f'An error occurred while submitting your query: {str(e)}', 'error')
        return redirect(url_for('index'))

@app.route('/api/get_user_details', methods=['GET'])
def get_user_details():
    emp_code = request.args.get('emp_code')
    user_type = request.args.get('user_type', 'employee')
    if not emp_code:
        return jsonify({'success': False, 'error': 'No employee code provided'}), 400

    conn = db_pool.getconn()
    try:
        with conn.cursor() as c:
            if user_type == 'employee':
                c.execute('''
                    SELECT employee_name, employee_phone
                    FROM grievances
                    WHERE emp_code = %s
                    ORDER BY submission_date DESC
                    LIMIT 1
                ''', (emp_code,))
                row = c.fetchone()
                if row:
                    return jsonify({'success': True, 'employee_name': row[0], 'employee_phone': row[1]})
            else:
                roles_to_check = ('hr', 'admin') if user_type == 'hr' else ('admin',)                
                c.execute(f'''
                    SELECT employee_name, employee_email, employee_phone FROM users
                    WHERE emp_code = %s AND role IN %s
                ''', (emp_code, roles_to_check))                
                row = c.fetchone()
                if row:
                    return jsonify({'success': True, 'employee_name': row[0], 'employee_email': row[1], 'employee_phone': row[2]})
        return jsonify({'success': False, 'error': 'User not found'})
    finally:
        db_pool.putconn(conn)

@app.route('/respond/<grievance_id>', methods=['GET', 'POST'])
def respond_grievance(grievance_id):
    user = session.get('user')
    if not user or not user.get('authenticated') or user.get('role') not in ['hr', 'admin']:
        flash('You do not have permission to respond to the query', 'error')
        return redirect(url_for('login'))

    conn = db_pool.getconn()
    try:
        with conn.cursor() as c:
            c.execute('SELECT employee_name, employee_email FROM users WHERE emp_code = %s', (user['emp_code'],))
            hr_row = c.fetchone()
            responder_name = hr_row[0] if hr_row else user.get('employee_name', '')
            responder_email = hr_row[1] if hr_row else user.get('employee_email', '')

            c.execute('SELECT * FROM grievances WHERE id = %s', (grievance_id,))
            grievance = c.fetchone()
            if not grievance:
                flash('Query not found.', 'error')
                return redirect(url_for('hr_dashboard'))

            if request.method == 'POST':
                responder_email = request.form.get('responder_email')
                responder_name = request.form.get('responder_name')
                response_text = request.form.get('response_text')
                new_status = request.form.get('status')

                if not all([responder_email, response_text, new_status]):
                    flash('Please fill in all required fields.', 'error')
                    return render_template('response.html', grievance=grievance, grievance_types=GRIEVANCE_TYPES,
                                           responder_name=responder_name, responder_email=responder_email)

                response_attachment_path = None
                if 'attachment' in request.files:
                    file = request.files['attachment']
                    if file and file.filename != '' and allowed_file(file.filename):
                        upload_dir = get_upload_path('hr', user['emp_code'])
                        filename = secure_filename(f"response_{grievance_id}_{file.filename}")
                        full_path = os.path.join(upload_dir, filename)
                        file.save(full_path)
                        response_attachment_path = filename  

                response_date = datetime.now()
                c.execute('''INSERT INTO responses
                            (grievance_id, responder_email, responder_name, response_text, response_date, attachment_path)
                            VALUES (%s, %s, %s, %s, %s, %s)''',
                         (grievance_id, responder_email, responder_name, response_text, response_date, response_attachment_path))

                c.execute('UPDATE grievances SET status = %s, updated_at = %s WHERE id = %s',
                         (new_status, datetime.now(), grievance_id))
                conn.commit()

                employee_email = grievance[3]
                employee_name = grievance[2]
                feedback_url = 'http://172.19.66.141:8112/login'
                print("Final feedback URL:", feedback_url)             
                response_email_body = f"""
<html>
<body style="font-family: Arial, sans-serif; line-height: 1.6; color: #2c3e50;">
    <div style="max-width: 600px; margin: 0 auto; padding: 20px; background: #f7fafc; border-radius: 8px;">
        <p>Dear {employee_name},</p>
        <p>Your query has been successfully updated with the following details:</p>
        <div style="background: white; padding: 20px; border-radius: 8px;">
            <p><strong>Reference ID:</strong> {grievance_id}</p>
            <p><strong>Subject:</strong> {grievance[8]}</p>
            <p><strong>Status:</strong> {new_status}</p>
            <p><strong>Resolution Date:</strong> {response_date.strftime('%d-%m-%Y, %H:%M:%S')}</p>
        </div>
        <p>Please click on the below link to submit the feedback.</p>
        <p>
            <a href="{feedback_url}"            
            style="display:inline-block; background:#1e3a8a; color:#fff; padding:10px 18px; border-radius:5px; text-decoration:none; font-weight:bold;">
               Submit Feedback
            </a>
        </p>
        <p><strong>Human Resources</strong></p>
    </div>
</body>
</html>
"""
                send_email_flask_mail(employee_email, f"Query Response (ID: {grievance_id})", response_email_body, full_path if file and file.filename != '' and allowed_file(file.filename) else None)
                employee_phone = grievance[4]
                if employee_phone:
                    if new_status == 'Resolved':
                        send_whatsapp_template(
                            to_phone=employee_phone,
                            template_name="grievance_resolution_confirmation",
                            lang_code="en",
                            parameters=[
                                employee_name,         
                                grievance_id,      
                                grievance[8],
                                new_status,      
                                datetime.now().strftime('%d-%m-%Y, %H:%M:%S')  
                            ]
                        )
                    else:
                        send_whatsapp_template(
                            to_phone= employee_phone,
                            template_name = "grievance_in_progress",
                            lang_code = "en",
                            parameters=[
                                employee_name,         
                                grievance_id,      
                                grievance[8],
                                new_status,      
                                datetime.now().strftime('%d-%m-%Y, %H:%M:%S')  
                            ]
                        )
                flash('Response submitted successfully.', 'success')
                return redirect(url_for('hr_dashboard'))

            return render_template('response.html', grievance=grievance, grievance_types=GRIEVANCE_TYPES,
                                   responder_name=responder_name, responder_email=responder_email)
    finally:
        db_pool.putconn(conn)

@app.route('/feedback/<grievance_id>/<response>')
def feedback(grievance_id, response):
    conn = db_pool.getconn()
    try:
        with conn.cursor() as c:
            c.execute('SELECT * FROM grievances WHERE id = %s', (grievance_id,))
            grievance = c.fetchone()
            if not grievance:
                flash('Query not found.', 'error')
                return redirect(url_for('dashboard'))
        return render_template('feedback.html', grievance_id=grievance_id, response=response)
    finally:
        db_pool.putconn(conn)

@app.route('/submit_feedback/<grievance_id>', methods=['POST'])
def submit_feedback(grievance_id):
    print(f"\n🔍 FEEDBACK SUBMISSION DEBUG:")
    print(f"   Grievance ID: {grievance_id}")
    print(f"   Form data: {dict(request.form)}")

    conn = db_pool.getconn()
    try:
        with conn.cursor() as c:
            satisfaction = request.form.get('satisfaction')
            rating = request.form.get('rating')
            feedback_comments = request.form.get('feedback_comments')
            reopen_ticket = request.form.get('reopen_ticket')

            if not satisfaction:
                flash('Please select a satisfaction status.', 'error')
                return redirect(url_for('feedback', grievance_id=grievance_id, response=''))

            if satisfaction == 'resolved' and not rating:
                flash('Please provide a rating for the resolved ticket.', 'error')
                return redirect(url_for('feedback', grievance_id=grievance_id, response=satisfaction))

            if satisfaction == 'not_resolved':
                if not feedback_comments or len(feedback_comments.strip()) < 30:
                    flash('Comments must be at least 30 characters for Not Resolved.', 'error')
                    return redirect(url_for('feedback', grievance_id=grievance_id, response=satisfaction))

            feedback_date = datetime.now()

            print(f"   💾 Saving feedback to database...")
            rating_value = int(rating) if rating else None

            c.execute('''INSERT INTO feedback
                        (grievance_id, satisfaction, rating, feedback_comments, feedback_date)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (grievance_id) DO UPDATE
                        SET satisfaction = EXCLUDED.satisfaction,
                            rating = EXCLUDED.rating,
                            feedback_comments = EXCLUDED.feedback_comments,
                            feedback_date = EXCLUDED.feedback_date''',
                     (grievance_id, satisfaction, rating_value, feedback_comments, feedback_date))

            print(f"   ✅ Feedback saved successfully!")

            if satisfaction == 'not_resolved' and reopen_ticket == 'yes':
                c.execute('UPDATE grievances SET status = %s, updated_at = %s WHERE id = %s',
                         ('Reopened', datetime.now(), grievance_id))
                print(f"   ✅ Grievance status updated to: Reopened")
            else:
                c.execute('UPDATE grievances SET status = %s, updated_at = %s WHERE id = %s',
                         ('Resolved', datetime.now(), grievance_id))
                print(f"   ✅ Grievance status updated to: Resolved")

            conn.commit()

            if satisfaction == 'not_resolved' and reopen_ticket == 'yes':
                c.execute('SELECT emp_code, employee_name, employee_email, employee_phone, grievance_type, subject FROM grievances WHERE id = %s', (grievance_id,))
                gr = c.fetchone()
                emp_code, employee_name, employee_email, employee_phone, grievance_type, subject = gr

                c.execute('''
                    SELECT u.employee_email, u.employee_name, u.employee_phone
                    FROM hr_grievance_mapping m
                    JOIN users u ON m.hr_emp_code = u.emp_code
                    WHERE m.grievance_type = %s
                ''', (grievance_type,))
                hr_info = c.fetchone()
                hr_email, hr_name, hr_phone = hr_info if hr_info else (None, None, None)

                notify_subject = f"Query Reopened - {subject} (ID: {grievance_id})"
                notify_body = f"""
                <html>
                <body style="font-family: Arial, sans-serif; line-height: 1.6;">
                    <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
                        <h2 style="color: #1e3a8a;"> 
                            Query Reopened (ID: {grievance_id}): Ask HR
                        </h2>
                        <p>Dear {hr_name or 'HR'},</p>
                        <p>The following query has been <b>reopened</b> by the employee:</p>
                        <div style="background: white; padding: 20px; border-radius: 8px;">
                            <p><strong>Query ID:</strong> {grievance_id}</p>
                            <p><strong>Employee Name:</strong> {employee_name}</p>
                            <p><strong>Subject:</strong> {subject}</p>
                            <p><strong>Status:</strong> Reopened</p>
                        </div>
                        <p>Please review and respond as soon as possible.</p>
                        <p><em>Human Resources</em></p>
                    </div>
                </body>
                </html>
                """
                if hr_email:
                    send_email_flask_mail(hr_email, notify_subject, notify_body)
                if hr_phone:
                    send_whatsapp_template(
                        to_phone=hr_phone,
                        template_name="grievance_reopened_hr",
                        lang_code="en",
                        parameters=[
                            hr_name,
                            grievance_id,
                            employee_name,
                            subject,
                        ]
                    )

                if employee_email:
                    send_email_flask_mail(employee_email, notify_subject, notify_body)
                if employee_phone:
                    send_whatsapp_template(
                        to_phone=employee_phone,
                        template_name="grievance_reopened_employee",
                        lang_code="en",
                        parameters=[
                            employee_name,
                            grievance_id,
                            subject,
                        ]
                    )

            flash('Thank you for your feedback!', 'success')
            return redirect(url_for('my_queries'))

    except Exception as e:
        print(f"   ❌ Error in submit_feedback: {str(e)}")
        print(f"   Traceback: {traceback.format_exc()}")
        flash('An error occurred while submitting feedback.', 'error')
        return redirect(url_for('feedback', grievance_id=grievance_id, response=''))
    finally:
        db_pool.putconn(conn)

@app.route('/test-email')
def test_email():
    try:
        with app.app_context():
            msg = Message(
                subject="Flask-Mail Connection Test",
                recipients=['romil.agarwal@nvtpower.com'],
                body="This is a test message to verify Flask-Mail connection."
            )
            mail.send(msg)
        return "<h1>✅ Flask-Mail Test Successful!</h1><p>Check your inbox for the test email.</p><a href='/'>Back to Form</a>"
    except Exception as e:
        return f"<h1>❌ Flask-Mail Test Failed!</h1><p>Error: {str(e)}</p><a href='/'>Back to Form</a>"

def check_pending_grievances(debug=True):
    """
    Send / re-send reminders for grievances stuck in 'Submitted' beyond thresholds.
    First reminder: submission_date older than REMINDER_INITIAL_THRESHOLD_HOURS
    Repeat: last reminder older than REMINDER_REPEAT_EVERY_HOURS (if enabled)
    """
    print("\n" + "="*68)
    print("⏰ OVERDUE QUERY REMINDER SCAN")
    now = datetime.now()
    first_cutoff = now - timedelta(hours=REMINDER_INITIAL_THRESHOLD_HOURS)
    repeat_cutoff = (now - timedelta(hours=REMINDER_REPEAT_EVERY_HOURS)) if REMINDER_REPEAT_EVERY_HOURS else None
    print(f" Now: {now}")
    print(f" First reminder if submission_date < {first_cutoff}")
    if repeat_cutoff:
        print(f" Repeat reminder if last reminder < {repeat_cutoff}")
    print("="*68)

    conn = db_pool.getconn()
    try:
        with conn.cursor() as c:
            # Diagnostics
            c.execute("SELECT COUNT(*) FROM grievances WHERE status='Submitted'")
            total_submitted = c.fetchone()[0]
            c.execute("SELECT COUNT(*) FROM grievances WHERE status='Submitted' AND submission_date < %s", (first_cutoff,))
            total_over_threshold = c.fetchone()[0]

            # Candidates
            c.execute("""
                SELECT g.id, g.employee_name, g.employee_email, g.subject, g.submission_date,
                       m.hr_emp_code, u.employee_email, u.employee_phone, r.reminder_date
                FROM grievances g
                LEFT JOIN hr_grievance_mapping m ON g.grievance_type = m.grievance_type
                LEFT JOIN users u ON m.hr_emp_code = u.emp_code
                LEFT JOIN reminder_sent r ON g.id = r.grievance_id
                WHERE g.status='Submitted'
                  AND g.submission_date < %s
                  AND (
                        r.grievance_id IS NULL
                        OR (%s IS NOT NULL AND r.reminder_date < %s)
                      )
                ORDER BY g.submission_date
            """, (first_cutoff, repeat_cutoff, repeat_cutoff))
            rows = c.fetchall()

            c.execute("SELECT employee_email, employee_phone FROM users WHERE role='admin' LIMIT 1")
            admin_row = c.fetchone()
            admin_email = admin_row[0] if admin_row else None
            admin_phone = admin_row[1] if admin_row else None

            if debug:
                print(f"🧪 Total Submitted: {total_submitted}")
                print(f"🧪 Submitted over threshold: {total_over_threshold}")
                print(f"🧪 Due (send / re-send) this scan: {len(rows)}")

            if not rows:
                print("✅ No reminders required now.")
                print("="*68)
                return

            sent = 0
            for (gid, emp_name, emp_email, subject, sub_dt,
                 hr_emp_code, hr_email, hr_phone, last_rem) in rows:
                age_h = int((now - sub_dt).total_seconds() / 3600)
                hr_name = None
                if hr_emp_code:
                    c.execute("SELECT employee_name FROM users WHERE emp_code=%s", (hr_emp_code,))
                    rnm = c.fetchone()
                    hr_name = rnm[0] if rnm else None

                target_email = hr_email or admin_email
                target_phone = hr_phone or admin_phone

                full_url = f"{SERVER_HOST}/respond/{gid}"
                if full_url and not full_url.startswith(("http://", "https://")):
                    full_url = f"http://{full_url}"
                email_subject = f"Pending {age_h}h: Query {gid}"
                email_body = f"""
<html>
<body style="font-family: Arial, sans-serif; line-height: 1.6; color: #2c3e50;">
    <div style="max-width: 600px; margin: 0 auto; padding: 20px; background: #f7fafc; border-radius: 8px;">
        <h2 style="color: #1e2a8a;"> 
            Urgent: Query Pending for over {age_h} Hours: Ask HR
        </h2>
        <p>This is an automated reminder that the following query has been pending without resolution:</p>
        <p>The query was successfully submitted with the following details:</p>
        <div style="background: white; padding: 20px; border-radius: 8px; margin-top: 20px;">
            <p><strong>Query ID:</strong> {gid}</p>
            <p><strong>Employee Name:</strong> {emp_name}</p>
            <p><strong>Subject:</strong> {subject}</p>
            <p><strong>Submission Date:</strong> {sub_dt.strftime('%d-%m-%Y %H:%M')}</p>
        </div>
        <p style="margin-top: 20px;">Please review and respond to this query as soon as possible.</p>
        <p><a href="{full_url}"
            style="display:inline-block; background:#1e3a8a; color:#fff; padding:10px 18px; border-radius:5px; text-decoration:none; font-weight:bold;">
            Respond Now</a>
        </p>
        <p><strong>Human Resources</strong></p>
    </div>
</body>
</html>
"""
                if target_email:
                    send_email_flask_mail(target_email, email_subject, email_body)
                if admin_email and admin_email != target_email:
                    send_email_flask_mail(admin_email, email_subject, email_body)
                if target_phone:
                    send_whatsapp_template(
                        to_phone=target_phone,
                        template_name="grievance_pending_reminder",
                        lang_code="en",
                        parameters=[str(age_h), gid, emp_name, subject]
                    )

                # Upsert reminder
                if last_rem:
                    c.execute("UPDATE reminder_sent SET reminder_date=%s WHERE grievance_id=%s", (now, gid))
                else:
                    c.execute("INSERT INTO reminder_sent (grievance_id, reminder_date) VALUES (%s,%s)", (gid, now))
                sent += 1

            conn.commit()
            print(f"✅ Sent {sent} reminder(s).")
    except Exception as e:
        print(f"❌ Error in reminder scan: {e}")
        print(traceback.format_exc())
    finally:
        db_pool.putconn(conn)
        print("="*68)

def send_daily_hr_pending_summary(debug=True):
    """
    Email & WhatsApp daily summary to each active HR at configured hours.
    Buckets: >=72h, 48-71h, 24-47h, <24h
    """
    print("\n" + "="*68)
    print("📨 DAILY HR PENDING SUMMARY")
    now = datetime.now()
    print(f" Timestamp: {now}")
    print("="*68)
    conn = db_pool.getconn()
    try:
        with conn.cursor() as c:
            c.execute("""
                SELECT emp_code, employee_name, employee_email, employee_phone
                FROM users
                WHERE role='hr' AND is_active=TRUE
            """)
            hr_rows = c.fetchall()
            if debug:
                print(f"HR accounts: {len(hr_rows)}")

            for emp_code, hr_name, hr_email, hr_phone in hr_rows:
                c.execute("""
                    SELECT g.id, g.subject, g.employee_name, g.submission_date
                    FROM grievances g
                    JOIN hr_grievance_mapping m ON g.grievance_type = m.grievance_type
                    WHERE m.hr_emp_code=%s AND g.status='Submitted'
                    ORDER BY g.submission_date
                """, (emp_code,))
                pending = c.fetchall()
                total = len(pending)
                if total == 0:
                    if debug:
                        print(f" - {hr_name}: 0 pending")
                    continue

                b72 = b48 = b24 = 0
                rows_html = []
                for gid, subj, emp_name, sub_dt in pending:
                    age_h = int((now - sub_dt).total_seconds() / 3600)
                    if age_h >= 72: b72 += 1
                    elif age_h >= 48: b48 += 1
                    elif age_h >= 24: b24 += 1
                    if len(rows_html) < MAX_ITEMS_IN_DAILY_EMAIL:
                        rows_html.append(
                            f"<tr><td>{gid}</td><td>{emp_name}</td><td>{subj}</td>"
                            f"<td>{sub_dt.strftime('%d-%m-%Y %H:%M')}</td><td>{age_h}h</td></tr>"
                        )
                under24 = total - b72 - b48 - b24

                if hr_email:
                    body = f"""
<html><body style="font-family:Arial,sans-serif">
<h3>Daily Pending Queries Summary</h3>
<p>Dear {hr_name},</p>
<ul>
<li>Total Pending: <b>{total}</b></li>
<li>&gt;=72h: <b>{b72}</b></li>
<li>48-71h: <b>{b48}</b></li>
<li>24-47h: <b>{b24}</b></li>
<li>&lt;24h: <b>{under24}</b></li>
</ul>
<table border="1" cellspacing="0" cellpadding="4" style="border-collapse:collapse;font-size:12px">
<thead style="background:#f0f0f0">
<tr><th>ID</th><th>Employee</th><th>Subject</th><th>Submitted</th><th>Age</th></tr>
</thead><tbody>
{''.join(rows_html)}
</tbody></table>
{"<p>...and more not listed.</p>" if total > len(rows_html) else ""}
<p>Human Resources</p>
</body></html>
"""
                    send_email_flask_mail(hr_email, f"Daily Pending Summary ({total}) - Ask HR", body)

                if hr_phone:
                    send_whatsapp_template(
                        to_phone=hr_phone,
                        template_name="daily_hr_pending_summary",
                        lang_code="en",
                        parameters=[hr_name, str(total), str(b72), str(b48), str(b24), str(under24)]
                    )
                if debug:
                    print(f" - {hr_name}: emailed/WA total={total} >=72h={b72}")
    except Exception as e:
        print(f"❌ Daily summary error: {e}")
        print(traceback.format_exc())
    finally:
        db_pool.putconn(conn)
        print("="*68)

def send_pending_feedback_reminders():
    """
    Send daily reminders to users whose queries are resolved but feedback is pending
    """
    print("\n" + "="*60)
    print("📋 CHECKING FOR PENDING FEEDBACK REMINDERS")
    print("="*60)
    
    conn = db_pool.getconn()
    try:
        with conn.cursor() as c:
            # Get resolved grievances with no feedback
            c.execute('''
                SELECT g.id, g.emp_code, g.employee_name, g.employee_email, 
                       g.employee_phone, g.subject, g.updated_at
                FROM grievances g
                LEFT JOIN feedback f ON g.id = f.grievance_id
                WHERE g.status = %s AND f.grievance_id IS NULL
            ''', ('Resolved',))
            
            pending_feedbacks = c.fetchall()
            
            if not pending_feedbacks:
                print("✅ No pending feedback reminders found")
                return
            
            print(f"📝 Found {len(pending_feedbacks)} users with pending feedback")
            
            for grievance in pending_feedbacks:
                grievance_id, emp_code, name, email, phone, subject, resolved_date = grievance
                
                print(f"Sending reminder for grievance {grievance_id} to {name}")
                
                # Create feedback URL
                feedback_url = f"{SERVER_HOST}/feedback/{grievance_id}"
                if not feedback_url.startswith(('http://', 'https://')):
                    feedback_url = f"http://{feedback_url}"
                
                # Email reminder
                email_subject = f"Reminder: Please Submit Feedback for Resolved Query (ID: {grievance_id})"
                email_body = f"""
                <h3>Feedback Reminder</h3>
                
                <p>Dear {name},</p>
                
                <p>Your query has been resolved and we would appreciate your feedback:</p>
                
                <p><strong>Reference ID:</strong> {grievance_id}</p>
                <p><strong>Subject:</strong> {subject}</p>
                <p><strong>Status:</strong> Resolved</p>
                
                <p>Please click the link below to submit your feedback:</p>
                <p><a href="{feedback_url}">Submit Feedback</a></p>
                
                <p>Your feedback helps us improve our services.</p>
                
                <p><strong>Human Resources</strong></p>
                """
                
                # Send email reminder
                if email:
                    send_email_flask_mail(email, email_subject, email_body)
                
                if phone:
                    send_whatsapp_template(
                        to_phone=phone,
                        template_name="feedback_reminder",
                        lang_code="en",
                        parameters=[
                            name, 
                            grievance_id, 
                            subject
                            ]
                    )
                
                print(f"✅ Reminder sent to {name} ({email}, {phone})")
    
    except Exception as e:
        print(f"❌ Error sending feedback reminders: {str(e)}")
    finally:
        db_pool.putconn(conn)

def generate_otp():
    """Generate a 6-digit OTP"""
    return ''.join(random.choices(string.digits, k=6))

def mask_phone(phone):
    """Mask the middle digits of a phone number for privacy"""
    if not phone or len(phone) < 8:
        return phone

    visible_start = phone[:2]
    visible_end = phone[-3:]
    masked_part = '*' * (len(phone) - 5)

    return f"{visible_start}{masked_part}{visible_end}"

@app.route('/login')
def login():
    return render_template('login.html')

@app.route('/verify_login', methods=['POST'])
def verify_login():
    user_type = request.form.get('user_type', 'employee')
    auth_type = request.form.get('auth_type', 'otp')
    emp_code = request.form.get('emp_code')
    employee_name = request.form.get('employee_name', '')
    employee_phone = request.form.get('employee_phone', '')
    employee_email = request.form.get('employee_email', '')
    date_of_birth = request.form.get('date_of_birth_formatted', '')

    if user_type in ['hr', 'admin']:
        auth_type = 'otp'

    if user_type == 'employee':
        if auth_type == 'otp' and not all([emp_code, employee_name, employee_phone]):
            flash('Please fill in all required fields for OTP login.', 'error')
            return redirect(url_for('login'))
        elif auth_type == 'dob' and not all([emp_code, employee_name, date_of_birth]):
            flash('Please fill in employee code, name and date of birth.', 'error')
            return redirect(url_for('login'))
    else:
        if not all([emp_code, employee_phone, employee_email]):
            flash('Please fill in all required fields.', 'error')
            return redirect(url_for('login'))

    conn = db_pool.getconn()
    try:
        with conn.cursor() as c:
            if user_type == 'employee':
                if auth_type == 'dob':
                    c.execute('''
                    SELECT id, employee_name, employee_email, employee_phone
                    FROM grievances
                    WHERE emp_code = %s
                    AND employee_name = %s
                    AND date_of_birth::date = %s::date
                    ORDER BY submission_date DESC
                    LIMIT 1
                ''', (emp_code, employee_name, date_of_birth))

                    employee = c.fetchone()
                    if not employee:
                        flash('Invalid credentials or date of birth. Please check your details.', 'error')
                        return redirect(url_for('login'))

                    session['user'] = {
                        'emp_code': emp_code,
                        'employee_name': employee_name,
                        'employee_phone': employee[3] if employee[3] else '',
                        'employee_email': employee[2] if employee[2] else '',
                        'role': 'employee',
                        'authenticated': True,
                        'login_time': datetime.now().isoformat()
                    }

                    flash('Login successful!', 'success')
                    return redirect(url_for('my_queries'))

                else:
                    if user_type == 'employee':
                        c.execute('''
                            SELECT id, employee_name, employee_email, employee_phone
                            FROM grievances
                            WHERE emp_code = %s AND employee_name = %s AND employee_phone = %s
                            ORDER BY submission_date DESC
                            LIMIT 1
                        ''', (emp_code, employee_name, employee_phone))
                        gr = c.fetchone()
                        if not gr:
                            flash('Invalid credentials. Please check your details.', 'error')
                            return redirect(url_for('login'))
                        user_id = gr[0]
                        user_name = gr[1]
                        user_email = gr[2]
                        user_phone = gr[3]
                        user_role = 'employee'

                        otp = generate_otp()
                        session['login_otp'] = {
                            'otp': otp,
                            'emp_code': emp_code,
                            'employee_name': user_name,
                            'employee_phone': user_phone,
                            'employee_email': user_email,
                            'user_type': user_type,
                            'role': user_role,
                            'expires': (datetime.now() + timedelta(minutes=5)).isoformat()
                        }
                        if user_phone:
                            send_whatsapp_template(
                            to_phone=user_phone,
                            template_name="otp_login_verification",
                            lang_code="en",
                            parameters=[otp]
                        )
                        if user_email:
                            email_subject = "OTP Verification: Ask HR"
                            email_body = f"""
    <html>
    <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #2c3e50;">
        <div style="max-width: 600px; margin: 0 auto; padding: 20px; background: #f7fafc; border-radius: 8px;">
            <h2 style="color: #1e3a8a:">
                OTP Verification for Ask HR Portal
            </h2>
            <p>Dear {user_name},</p>
            <p>Your OTP for Ask HR Portal Login is: {otp}</p>
            <p>This code will expire in 05 minutes.</p>
            <p>Don't share this OTP with anyone.</p>
            <p><strong>Human Resources</strong></p>
        </div>
    </body>
    </html>
    """
                            send_email_flask_mail(user_email, email_subject, email_body)

                        masked_phone = mask_phone(user_phone)
                        return render_template('verify_otp.html',
                                  emp_code=emp_code,
                                  employee_name=user_name,
                                  employee_phone=user_phone,
                                  masked_phone=masked_phone,
                                  user_type=user_type)
            else:
                roles_to_check = ('hr', 'admin') if user_type == 'hr' else ('admin',)

                c.execute(f'''
                    SELECT id, role, employee_name, employee_email, employee_phone 
                    FROM users
                    WHERE emp_code = %s AND employee_phone = %s AND employee_email = %s AND role IN %s
                ''', (emp_code, employee_phone, employee_email, roles_to_check))
                user = c.fetchone()

                if not user:
                    flash('Access Denied. You are not an authorized user for this section.', 'error')
                    return redirect(url_for('login'))

                user_id, user_role, user_name, user_email, user_phone = user
                
                otp = generate_otp()
                session['login_otp'] = {
                    'otp': otp, 
                    'emp_code': emp_code,                     
                    'employee_name': user_name, 
                    'employee_phone': user_phone,
                    'employee_email': user_email, 
                    'user_type': user_type, 
                    'role': user_role, 
                    'expires': (datetime.now() + timedelta(minutes=5)).isoformat()}
                
                if user_phone: 
                    send_whatsapp_template(
                        to_phone=user_phone, 
                        template_name="otp_login_verification", 
                        lang_code="en", 
                        parameters=[otp])
                
                if user_email:
                    email_subject = "OTP Verification: Ask HR"
                    email_body = f"""
    <html>
    <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #2c3e50;">
        <div style="max-width: 600px; margin: 0 auto; padding: 20px; background: #f7fafc; border-radius: 8px;">
            <h2 style="color: #1e3a8a:">
                OTP Verification for Ask HR Portal
            </h2>
            <p>Dear {user_name},</p>
            <p>Your OTP for Ask HR Portal Login is: {otp}</p>
            <p>This code will expire in 05 minutes.</p>
            <p>Don't share this OTP with anyone.</p>
            <p><strong>Human Resources</strong></p>
        </div>
    </body>
    </html>
    """
                    send_email_flask_mail(user_email, email_subject, email_body)


                masked_phone = mask_phone(user_phone)
                return render_template('verify_otp.html', emp_code=emp_code, employee_name=user_name, employee_phone=user_phone, masked_phone=masked_phone, user_type=user_type)                      
    except Exception as e:
        flash(f'Error: {str(e)}', 'error')
        return redirect(url_for('login'))
    finally:
        db_pool.putconn(conn)

@app.route('/verify_otp', methods=['POST'])
def verify_otp():
    otp = request.form.get('otp')
    emp_code = request.form.get('emp_code')
    employee_name = request.form.get('employee_name')
    employee_phone = request.form.get('employee_phone')
    user_type = request.form.get('user_type', 'employee')

    login_data = session.get('login_otp')

    if not login_data:
        flash('Session expired. Please try again.', 'error')
        return redirect(url_for('login'))

    if datetime.now() > datetime.fromisoformat(login_data['expires']):
        session.pop('login_otp', None)
        flash('OTP has expired. Please request a new one.', 'error')
        return redirect(url_for('login'))

    if otp != login_data['otp']:
        flash('Invalid OTP. Please try again.', 'error')
        masked_phone = mask_phone(employee_phone)
        return render_template('verify_otp.html',
                              emp_code=emp_code,
                              employee_name=employee_name,
                              employee_phone=employee_phone,
                              masked_phone=masked_phone,
                              user_type=user_type)

    session.pop('login_otp', None)
    user_role = login_data['role']

    session['user'] = {
        'emp_code': emp_code,
        'employee_name': employee_name,
        'employee_phone': employee_phone,
        'employee_email': login_data.get('employee_email'),
        'role': user_role,
        'authenticated': True,
        'login_time': datetime.now().isoformat()
    }

    flash('Login successful!', 'success')

    if user_role == 'admin':
        return redirect(url_for('master_dashboard'))
    elif user_role == 'hr':
        return redirect(url_for('hr_dashboard'))
    else:
        return redirect(url_for('my_queries'))

@app.route('/hr-dashboard')
def hr_dashboard():
    user = session.get('user')
    if not user or not user.get('authenticated') or user.get('role') not in ['hr', 'admin']:
        flash('You do not have permission to access the HR dashboard', 'error')
        return redirect(url_for('login'))

    page = request.args.get('page', 1, type=int)
    per_page = 10
    offset = (page - 1) * per_page

    status = request.args.get('status', '')
    grievance_type = request.args.get('grievance_type', '')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    search = request.args.get('search', '')

    filter_args = {}
    if status:
        filter_args['status'] = status
    if grievance_type:
        filter_args['grievance_type'] = grievance_type
    if date_from:
        filter_args['date_from'] = date_from
    if date_to:
        filter_args['date_to'] = date_to
    if search:
        filter_args['search'] = search

    emp_code = user['emp_code']
    conn = db_pool.getconn()
    try:
        with conn.cursor() as c:
            c.execute('''
                SELECT grievance_type FROM hr_grievance_mapping
                WHERE hr_emp_code = %s
            ''', (emp_code,))

            assigned_types = [row[0] for row in c.fetchall()]
            
            c.execute("SELECT emp_code, employee_name FROM users WHERE role = 'hr' ORDER BY employee_name")
            hr_staff = c.fetchall()
            
            c.execute('''
                SELECT
                    SUM(CASE WHEN status = 'Submitted' THEN 1 ELSE 0 END) as submitted,
                    SUM(CASE WHEN status = 'In Progress' THEN 1 ELSE 0 END) as in_progress,
                    SUM(CASE WHEN status = 'Resolved' THEN 1 ELSE 0 END) as resolved,
                    SUM(CASE WHEN status = 'Reopened' THEN 1 ELSE 0 END) as reopened,
                    COUNT(*) as total
                FROM grievances
                WHERE grievance_type IN %s
            ''', (
                tuple(assigned_types),))
            stats_row = c.fetchone()
            stats = {
                'submitted': stats_row[0] or 0,
                'in_progress': stats_row[1] or 0,
                'resolved': stats_row[2] or 0,
                'reopened': stats_row[3] or 0,
                'total': stats_row[4] or 0
            }

            if user.get('role') == 'admin' and not assigned_types:
                assigned_types = list(GRIEVANCE_TYPES.keys())

            if not assigned_types:
                return render_template('hr_dashboard.html',
                                      grievances=[],
                                      grievance_types=GRIEVANCE_TYPES,
                                      assigned_types=assigned_types,
                                      page=page,
                                      total_pages=0,
                                      filter_args=filter_args,
                                      max=max,
                                      min=min,
                                      stats=stats,
                                      hr_staff=hr_staff)

            query = '''SELECT g.id, g.emp_code, g.employee_name, g.employee_email, g.grievance_type,
                        g.subject, g.status, g.submission_date, g.attachment_path,
                        f.rating, f.satisfaction, f.feedback_comments
                        FROM grievances g
                        LEFT JOIN feedback f ON g.id = f.grievance_id
                        WHERE '''

            query_conditions = ["g.grievance_type IN %s"]
            query_params = [tuple(assigned_types)]

            if status:
                query_conditions.append("status = %s")
                query_params.append(status)

            if grievance_type and grievance_type in assigned_types:
                query_conditions.append("grievance_type = %s")
                query_params.append(grievance_type)

            if date_from:
                query_conditions.append("submission_date::date >= %s")
                query_params.append(date_from)

            if date_to:
                query_conditions.append("submission_date::date <= %s")
                query_params.append(date_to)

            if search:
                query_conditions.append('''(
                    g.id ILIKE %s OR
                    g.emp_code ILIKE %s OR
                    g.employee_name ILIKE %s OR
                    g.subject ILIKE %s
                )''')
                search_param = f"%{search}%"
                query_params.extend([search_param, search_param, search_param, search_param])

            query += " AND ".join(query_conditions)
            query += " ORDER BY submission_date DESC LIMIT %s OFFSET %s"
            query_params.extend([per_page, offset])

            print(f"🔍 Executing query: {query % tuple(['%s'] * len(query_params))}")
            c.execute(query, query_params)
            grievances = c.fetchall()

            grievances_list = []
            for g in grievances:
                grievances_list.append({
                    'id': g[0],
                    'emp_code': g[1],
                    'employee_name': g[2],
                    'employee_email': g[3],
                    'grievance_type': g[4],
                    'subject': g[5],
                    'status': g[6],
                    'submission_date': g[7],
                    'attachment_path': g[8],
                    'rating': g[9],
                    'satisfaction': g[10],
                    'feedback_comments': g[11],
                })
            c.execute('''SELECT COUNT(*) FROM grievances WHERE grievance_type IN %s''', (tuple(assigned_types),))
            total_count = c.fetchone()[0]

            total_pages = (total_count + per_page - 1) // per_page

            return render_template('hr_dashboard.html',
                                  grievances=grievances_list,
                                  grievance_types=GRIEVANCE_TYPES,
                                  assigned_types=assigned_types,
                                  page=page,
                                  total_pages=total_pages,
                                  filter_args=filter_args,
                                  max=max,
                                  min=min,
                                  stats=stats,
                                  hr_staff=hr_staff)
    finally:
        db_pool.putconn(conn)

@app.route('/my-queries')
def my_queries():
    user = session.get('user')
    if not user or not user.get('authenticated'):
        flash('Please log in to view your query', 'error')
        return redirect(url_for('login'))

    server_host = os.environ.get('SERVER_HOST')
    grievance_form_url = f"{server_host}"
    emp_code = user['emp_code']
    conn = db_pool.getconn()
    try:
        with conn.cursor() as c:
            c.execute('''
                SELECT g.*, f.rating, f.satisfaction
                FROM grievances g
                LEFT JOIN feedback f ON g.id = f.grievance_id
                WHERE g.emp_code = %s
                ORDER BY g.submission_date DESC
            ''', (emp_code,))
            grievances_raw = c.fetchall()
            columns = [desc[0] for desc in c.description]
            grievances = []
            for gr in grievances_raw:
                grievance_dict = dict(zip(columns, gr))
                c.execute('''
                    SELECT responder_name, response_text, response_date, attachment_path, responder_email
                    FROM responses
                    WHERE grievance_id = %s
                    ORDER BY response_date ASC
                    ''', (grievance_dict['id'],))
                responses = []
                for resp in c.fetchall():
                    hr_emp_code = None
                    if resp[4]:  
                        c.execute('SELECT emp_code FROM users WHERE employee_email = %s', (resp[4],))
                        hr_row = c.fetchone()
                        hr_emp_code = hr_row[0] if hr_row else ''
                    responses.append({
                        'responder_name': resp[0],
                        'response_text': resp[1],
                        'response_date': resp[2],
                        'attachment_path': resp[3],  
                        'hr_emp_code': hr_emp_code
                        })
                grievance_dict['responses'] = responses
                grievances.append(grievance_dict)

            print(f"Found {len(grievances)} grievances for emp_code {emp_code}")
            for g in grievances:
                print(f"Grievance {g['id']}: status={g['status']}, rating={g['rating'] if 'rating' in g else 'N/A'}")

            c.execute('''
                SELECT status, COUNT(*)
                FROM grievances
                WHERE emp_code = %s
                GROUP BY status''', (emp_code,))

            status_counts = {status: count for status, count in c.fetchall()}

            total_grievances = sum(status_counts.values()) if status_counts else 0

            masked_phone = mask_phone(user['employee_phone'])

            return render_template('my_queries.html',
                                 grievances=grievances,
                                 emp_code=emp_code,
                                 grievance_form_url=grievance_form_url,
                                 employee_name=user['employee_name'],
                                 masked_phone=masked_phone,
                                 grievance_types=GRIEVANCE_TYPES,
                                 status_counts=status_counts,
                                 total_grievances=total_grievances)
    finally:
        db_pool.putconn(conn)

@app.route('/master-dashboard')
def master_dashboard():
    user = session.get('user')
    if not user or not user.get('authenticated') or user.get('role') != 'admin':
        flash('You do not have permission to access the master dashboard', 'error')
        return redirect(url_for('login'))

    page = request.args.get('page', 1, type=int)
    per_page = 10
    offset = (page - 1) * per_page

    status = request.args.get('status', '')
    grievance_type = request.args.get('grievance_type', '')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    search = request.args.get('search', '')
    hr_emp_code = request.args.get('hr_emp_code', '')

    filter_args = {}
    if status:
        filter_args['status'] = status
    if grievance_type:
        filter_args['grievance_type'] = grievance_type
    if date_from:
        filter_args['date_from'] = date_from
    if date_to:
        filter_args['date_to'] = date_to
    if search:
        filter_args['search'] = search
    if hr_emp_code:
        filter_args['hr_emp_code'] = hr_emp_code

    conn = db_pool.getconn()
    try:
        with conn.cursor() as c:
            c.execute('''
                SELECT
                    SUM(CASE WHEN status = 'Submitted' THEN 1 ELSE 0 END) as submitted,
                    SUM(CASE WHEN status = 'In Progress' THEN 1 ELSE 0 END) as in_progress,
                    SUM(CASE WHEN status = 'Resolved' THEN 1 ELSE 0 END) as resolved,
                    SUM(CASE WHEN status = 'Reopened' THEN 1 ELSE 0 END) as reopened,
                    COUNT(*) as total
                FROM grievances
            ''')
            stats_row = c.fetchone()
            stats = {
                'submitted': stats_row[0] or 0,
                'in_progress': stats_row[1] or 0,
                'resolved': stats_row[2] or 0,
                'reopened': stats_row[3] or 0,
                'total': stats_row[4] or 0
            }

            c.execute('''
                SELECT emp_code, employee_name FROM users
                WHERE role = 'hr'
                ORDER BY employee_name
            ''')
            hr_staff = c.fetchall()

            query = '''
                SELECT
                    g.id, g.emp_code, g.employee_name, g.employee_email,
                    g.grievance_type, g.subject, g.status, g.submission_date,
                    u.employee_name as hr_name,
                    f.rating, f.satisfaction, f.feedback_comments,
                    g.description, g.updated_at
                FROM grievances g
                LEFT JOIN hr_grievance_mapping m ON g.grievance_type = m.grievance_type
                LEFT JOIN users u ON m.hr_emp_code = u.emp_code
                LEFT JOIN feedback f ON g.id = f.grievance_id
                WHERE 1=1
            '''
            params = []

            grievances = []
            if status:
                query += " AND g.status = %s"
                params.append(status)

            if grievance_type:
                query += " AND g.grievance_type = %s"
                params.append(grievance_type)

            if date_from:
                query += " AND g.submission_date::date >= %s"
                params.append(date_from)

            if date_to:
                query += " AND g.submission_date::date <= %s"
                params.append(date_to)

            if hr_emp_code:
                query += " AND m.hr_emp_code = %s"
                params.append(hr_emp_code)

            if search:
                query += " AND (g.id ILIKE %s OR g.employee_name ILIKE %s OR g.subject ILIKE %s OR g.emp_code ILIKE %s)"
                search_term = f"%{search}%"
                params.extend([search_term, search_term, search_term, search_term])

            count_query = f"SELECT COUNT(*) FROM ({query}) AS count_query"
            c.execute(count_query, params)
            total_count = c.fetchone()[0]
            total_pages = (total_count + per_page - 1) // per_page

            query += " ORDER BY g.submission_date DESC LIMIT %s OFFSET %s"
            params.extend([per_page, offset])

            c.execute(query, params)
            grievances_data = c.fetchall()

            grievances = []
            for g in grievances_data:
                grievances.append({
                    'id': g[0],
                    'emp_code': g[1],
                    'employee_name': g[2],
                    'employee_email': g[3],
                    'grievance_type': g[4],
                    'subject': g[5],
                    'status': g[6],
                    'submission_date': g[7],
                    'hr_name': g[8] or 'Unassigned',
                    'rating': g[9],
                    'satisfaction': g[10],
                    'feedback_comments': g[11],
                    'description': g[12],
                    'updated_at': g[13],
                    'responses': []
                })
            ids = [gr['id'] for gr in grievances]
            if ids:
                c.execute("""
                    SELECT grievance_id, responder_name, responder_email, response_text, response_date, attachment_path
                    FROM responses
                    WHERE grievance_id = ANY(%s)
                    ORDER BY response_date ASC
                    """, (ids,))
                resp_map = {}
                for gid, rname, remail, rtext, rdate, rattach in c.fetchall():
                    resp_map.setdefault(gid, []).append({
                        'responder_name': rname,
                        'responder_email': remail,
                        'message': rtext,
                        'created_at': rdate.strftime('%Y-%m-%d %H:%M') if rdate else '',
                        'attachment_path': rattach
                    })
                for gr in grievances:
                    gr['responses'] = resp_map.get(gr['id'], [])
            return render_template('master_dashboard.html',
                                 grievances=grievances,
                                 grievance_types=GRIEVANCE_TYPES,
                                 stats=stats,
                                 hr_staff=hr_staff,
                                 page=page,
                                 total_pages=total_pages,
                                 filter_args=filter_args,
                                 selected_status=status,
                                 selected_type=grievance_type,
                                 selected_hr=hr_emp_code,
                                 search_query=search,
                                 date_from=date_from,
                                 date_to=date_to,
                                 max=max,
                                 min=min)
    finally:
        db_pool.putconn(conn)

@app.route('/reply-grievance/<grievance_id>', methods=['GET', 'POST'])
def reply_grievance(grievance_id):
    user = session.get('user')
    if not user or not user.get('authenticated') or user.get('role') != 'employee':
        flash('Unauthorized.', 'error')
        return redirect(url_for('login'))

    conn = db_pool.getconn()
    try:
        with conn.cursor() as c:
            c.execute("""SELECT id, emp_code, employee_name, employee_email,
                                employee_phone, grievance_type, subject, status,
                                reply_count
                         FROM grievances WHERE id=%s AND emp_code=%s""",
                      (grievance_id, user['emp_code']))
            gr = c.fetchone()
            if not gr:
                flash('Query not found.', 'error')
                return redirect(url_for('my_queries'))

            status = gr[7]
            reply_count = gr[8] if gr[8] is not None else 0
            if status == 'Resolved':
                flash('Cannot reply to a resolved query.', 'error')
                return redirect(url_for('my_queries'))
            if reply_count >= 2:
                flash('You have reached the maximum of 2 replies.', 'error')
                return redirect(url_for('my_queries'))

            if request.method == 'POST':
                reply_text = request.form.get('reply_text', '').strip()
                if not reply_text or len(reply_text) < 10:
                    flash('Reply must be at least 10 characters.', 'error')
                    return redirect(url_for('reply_grievance', grievance_id=grievance_id))

                attachment_path = None
                if 'attachment' in request.files:
                    file = request.files['attachment']
                    if file and file.filename != '' and allowed_file(file.filename):
                        upload_dir = get_upload_path('employee', user['emp_code'])
                        fname = secure_filename(f"reply_{grievance_id}_{reply_count+1}_{file.filename}")
                        full_path = os.path.join(upload_dir, fname)
                        file.save(full_path)
                        attachment_path = fname

                # Insert reply as a response row (responder = employee)
                c.execute("""INSERT INTO responses
                            (grievance_id, responder_email, responder_name, response_text, response_date, attachment_path)
                            VALUES (%s,%s,%s,%s,%s,%s)""",
                          (grievance_id, gr[3], gr[2], reply_text, datetime.now(), attachment_path))
                c.execute("UPDATE grievances SET reply_count = reply_count + 1, updated_at=%s WHERE id=%s",
                          (datetime.now(), grievance_id))

                # Notify assigned HR
                c.execute("""SELECT u.employee_email, u.employee_name, u.employee_phone
                             FROM hr_grievance_mapping m
                             JOIN users u ON m.hr_emp_code = u.emp_code
                             WHERE m.grievance_type = %s""", (gr[5],))
                hr_info = c.fetchone()
                if hr_info:
                    hr_email, hr_name, hr_phone = hr_info
                    subj = f"Employee Reply Received - {gr[6]} (ID: {grievance_id})"
                    body = f"""
<html><body style="font-family:Arial,sans-serif">
<p>Dear {hr_name},</p>
<p>The employee has added a reply to the query requiring your attention:</p>
<ul>
<li><b>ID:</b> {grievance_id}</li>
<li><b>Subject:</b> {gr[6]}</li>
<li><b>Reply Count:</b> {reply_count+1}/2</li>
<li><b>Status:</b> {status}</li>
</ul>
<p><b>Reply Text:</b><br>{reply_text}</p>
<p>Please respond in the portal.</p>
<p>Human Resources</p>
</body></html>
"""
                    if hr_email:
                        send_email_flask_mail(hr_email, subj, body)
                    if hr_phone:
                        send_whatsapp_template(
                            to_phone=hr_phone,
                            template_name="employee_reply_hr_notify",
                            lang_code="en",
                            parameters=[hr_name, grievance_id, gr[2], gr[6], str(reply_count+1)]
                        )
                conn.commit()
                flash('Reply submitted.', 'success')
                return redirect(url_for('my_queries'))

            # GET
            return render_template('reply_grievance.html',
                                   grievance_id=grievance_id,
                                   subject=gr[6],
                                   status=status,
                                   reply_count=reply_count,
                                   max_replies=2)
    finally:
        db_pool.putconn(conn)

@app.route('/edit-grievance/<grievance_id>', methods=['GET', 'POST'])
def edit_grievance(grievance_id):
    user = session.get('user')
    if not user or not user.get('authenticated') or user.get('role') != 'employee':
        flash('Unauthorized access.', 'error')
        return redirect(url_for('login'))

    conn = db_pool.getconn()
    try:
        with conn.cursor() as c:
            c.execute('SELECT * FROM grievances WHERE id = %s AND emp_code = %s', (grievance_id, user['emp_code']))
            grievance = c.fetchone()
            if not grievance:
                flash('Query not found.', 'error')
                return redirect(url_for('my_queries'))
            if grievance[12] != 'Submitted':
                flash('You can only edit query that are still submitted.', 'error')
                return redirect(url_for('my_queries'))
            edit_count = grievance[17] if len(grievance) > 17 and grievance[17] is not None else 0
            if edit_count >= 1:
                flash('You can only edit a query once.', 'error')
                return redirect(url_for('my_queries'))

            if request.method == 'POST':
                grievance_type = request.form.get('grievance_type')
                subject = request.form.get('subject')
                description = request.form.get('description')
                attachment_path = grievance[11]
                emp_code = grievance[1]
                if 'attachment' in request.files:
                    file = request.files['attachment']
                    if file and file.filename != '' and allowed_file(file.filename):
                        upload_dir = get_upload_path('employee', emp_code)
                        filename = secure_filename(f"{grievance_id}_{file.filename}")
                        file.save(os.path.join(upload_dir, filename))
                        attachment_path = filename  

                if not subject or not description:
                    flash('Subject and description are required.', 'error')
                    return render_template('edit_grievance.html', grievance=grievance, grievance_types=GRIEVANCE_TYPES)

                old_grievance_type = grievance[10]
                old_subject = grievance[8]
                old_description = grievance[9]

                c.execute('''
                    UPDATE grievances
                    SET subject = %s, grievance_type = %s, attachment_path = %s, description = %s, edit_count = edit_count + 1, updated_at = %s
                    WHERE id = %s
                ''', (subject, grievance_type, attachment_path, description, datetime.now(), grievance_id))
                conn.commit()

                
                employee_email = grievance[3]
                employee_name = grievance[2]
                employee_phone = grievance[4]

                
                email_subject = f"Your Query (ID: {grievance_id}) Has Been Updated"
                email_body = f"""
                <html>
                <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #2c3e50;">
                    <div style="max-width: 600px; margin: 0 auto; padding: 20px; background: #f7fafc; border-radius: 8px;">
                        <h2 style="color: #1e3a8a;">Query Updated: Ask HR</h2>
                        <p>Dear {employee_name},</p>
                        <p>Your query has been updated with the following details:</p>
                        <div style="background: white; padding: 20px; border-radius: 8px;">
                            <p><strong>Reference ID:</strong> {grievance_id}</p>
                            <p><strong>Subject:</strong> {subject}</p>
                            <p><strong>Type of Concern:</strong> {GRIEVANCE_TYPES.get(grievance_type, grievance_type)}</p>
                            <p><strong>Description:</strong> {description}</p>
                        </div>
                        <p>If you did not make this change, please contact HR immediately.</p>
                        <p><strong>Human Resources</strong></p>
                    </div>
                </body>
                </html>
                """
                send_email_flask_mail(employee_email, email_subject, email_body)
                if employee_phone:
                    send_whatsapp_template(
                        to_phone=employee_phone,
                        template_name="grievance_updated_employee",
                        lang_code="en",
                        parameters=[
                            employee_name,
                            grievance_id,
                            subject,
                            GRIEVANCE_TYPES.get(grievance_type, grievance_type)
                        ]
                    )

                
                if grievance_type == old_grievance_type:
                    c.execute('''
                        SELECT u.employee_email, u.employee_name, u.employee_phone
                        FROM hr_grievance_mapping m
                        JOIN users u ON m.hr_emp_code = u.emp_code
                        WHERE m.grievance_type = %s
                    ''', (grievance_type,))
                    hr_info = c.fetchone()
                    if hr_info:
                        hr_email, hr_name, hr_phone = hr_info
                        hr_subject = f"Query Updated (ID: {grievance_id})"
                        hr_body = f"""
                        <html>
                        <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #2c3e50;">
    <div style="max-width: 600px; margin: 0 auto; padding: 20px; background: #f7fafc; border-radius: 8px;"> 
                                <h2 style="color: #1e3a8a;">Query Updated: Ask HR</h2>
                                <p>Dear {hr_name},</p>
                                <p>The following query assigned to you has been updated by the employee:</p>
                                <div style="background: white; padding: 20px; border-radius: 8px;">
                                    <p><strong>Reference ID:</strong> {grievance_id}</p>
                                    <p><strong>Subject:</strong> {subject}</p>
                                    <p><strong>Type of Concern:</strong> {GRIEVANCE_TYPES.get(grievance_type, grievance_type)}</p>
                                    <p><strong>Description:</strong> {description}</p>
                                </div>
                                <p>Please review the updated details in the Ask HR portal.</p>
                                <p><strong>Human Resources</strong></p>
                            </div>
                        </body>
                        </html>
                        """
                        send_email_flask_mail(hr_email, hr_subject, hr_body)
                        if hr_phone:
                            send_whatsapp_template(
                                to_phone=hr_phone,
                                template_name="grievance_updated_hr",
                                lang_code="en",
                                parameters=[
                                    hr_name,
                                    grievance_id,
                                    subject,
                                    employee_name
                                ]
                            )
                else:
                    
                    c.execute('''
                        SELECT u.employee_email, u.employee_name, u.employee_phone
                        FROM hr_grievance_mapping m
                        JOIN users u ON m.hr_emp_code = u.emp_code
                        WHERE m.grievance_type = %s
                    ''', (grievance_type,))
                    new_hr_info = c.fetchone()
                    if new_hr_info:
                        new_hr_email, new_hr_name, new_hr_phone = new_hr_info
                        hr_subject = f"New Query Assigned (ID: {grievance_id})"
                        hr_body = f"""
                        <html>
                        <body style="font-family: Arial, sans-serif; line-height: 1.6; color: 
                            <div style="max-width: 600px; margin: 0 auto; padding: 20px; background: 
                                <h2 style="color: 
                                <p>Dear {new_hr_name},</p>
                                <p>A query has been updated and is now assigned to you:</p>
                                <div style="background: white; padding: 20px; border-radius: 8px;">
                                    <p><strong>Reference ID:</strong> {grievance_id}</p>
                                    <p><strong>Subject:</strong> {subject}</p>
                                    <p><strong>Type of Concern:</strong> {GRIEVANCE_TYPES.get(grievance_type, grievance_type)}</p>
                                    <p><strong>Description:</strong> {description}</p>
                                </div>
                                <p>Please review the details in the Ask HR portal.</p>
                                <p><strong>Human Resources</strong></p>
                            </div>
                        </body>
                        </html>
                        """
                        send_email_flask_mail(new_hr_email, hr_subject, hr_body)
                        if new_hr_phone:
                                send_whatsapp_template(
                                to_phone=new_hr_phone,
                                template_name="new_grievance_notification_hr",
                                lang_code="en",
                                parameters=[
                                    employee_name,
                                    new_hr_name,
                                    grievance_id,
                                    subject,
                                    datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                                ]
                            )

                flash('Query updated successfully.', 'success')
                return redirect(url_for('my_queries'))
            return render_template('edit_grievance.html', grievance=grievance, grievance_types=GRIEVANCE_TYPES)
    finally:
        db_pool.putconn(conn)

@app.route('/new-grievance')
def new_grievance():
    session.pop('user', None)
    return redirect(url_for('index'))

@app.route('/delete-grievance-employee', methods=['POST'])
def delete_grievance_employee():
    user = session.get('user')
    if not user or not user.get('authenticated') or user.get('role') != 'employee':
        flash('Unauthorized access.', 'error')
        return redirect(url_for('login'))

    grievance_id = request.form.get('grievance_id')
    conn = db_pool.getconn()
    try:
        with conn.cursor() as c:
            
            c.execute('SELECT id, emp_code, employee_name, employee_email, employee_phone, grievance_type, subject, status FROM grievances WHERE id = %s AND emp_code = %s', (grievance_id, user['emp_code']))
            gr = c.fetchone()
            if not gr:
                flash('Query not found.', 'error')
                return redirect(url_for('my_queries'))
            if gr[7] != 'Submitted':
                flash('You can only delete queries that are still submitted.', 'error')
                return redirect(url_for('my_queries'))

            
            c.execute('''
                SELECT u.employee_email, u.employee_name, u.employee_phone
                FROM hr_grievance_mapping m
                JOIN users u ON m.hr_emp_code = u.emp_code
                WHERE m.grievance_type = %s
            ''', (gr[5],))
            hr_info = c.fetchone()
            hr_email, hr_name, hr_phone = hr_info if hr_info else (None, None, None)

            
            c.execute('DELETE FROM grievances WHERE id = %s AND emp_code = %s', (grievance_id, user['emp_code']))
            conn.commit()

            
            employee_name = gr[2]
            employee_email = gr[3]
            employee_phone = gr[4]
            subject = gr[6]

            
            email_subject_emp = f"Your Query Request Deleted (ID: {grievance_id})"
            email_body_emp = f"""
            <html>
            <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #2c3e50;">
                <div style="max-width: 600px; margin: 0 auto; padding: 20px; background: #f7fafc; border-radius: 8px;"> 
                    <h2 style="color:#1e3a8a;">Query Deleted: Ask HR</h2> 
                    <p>Dear {employee_name},</p>
                    <p>Your query request with the following details has been <b>deleted</b>:</p>
                    <div style="background: white; padding: 20px; border-radius: 8px;">
                        <p><strong>Reference ID:</strong> {grievance_id}</p>
                        <p><strong>Subject:</strong> {subject}</p>
                        <p><strong>Status:</strong> Deleted</p>
                    </div>
                    <p>If you have any questions, please contact HR.</p>
                    <p><strong>Human Resources</strong></p>
                </div>
            </body>
            </html>
            """
            send_email_flask_mail(employee_email, email_subject_emp, email_body_emp)
            if employee_phone:
                send_whatsapp_template(
                    to_phone=employee_phone,
                    template_name="grievance_deleted_notification",
                    lang_code="en",
                    parameters=[
                        employee_name,
                        grievance_id,
                        subject,
                    ]
                )

            
            if hr_email:
                email_subject_hr = f"Query Request Deleted by Employee (ID: {grievance_id})"
                email_body_hr = f"""
                <html>
                <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #2c3e50;">
                    <div style="max-width: 600px; margin: 0 auto; padding: 20px; background: #f7fafc; border-radius: 8px;">
                        <h2 style="color:#1e3a8a;">Query Deleted: Ask HR</h2> 
                        <p>Dear {hr_name or 'HR'},</p>
                        <p>The following query has been <b>deleted by the employee</b>:</p>
                        <div style="background: white; padding: 20px; border-radius: 8px;">
                            <p><strong>Reference ID:</strong> {grievance_id}</p>
                            <p><strong>Employee Name:</strong> {employee_name}</p>
                            <p><strong>Subject:</strong> {subject}</p>
                            <p><strong>Status:</strong> Deleted</p>
                        </div>
                        <p><strong>Human Resources</strong></p>
                    </div>
                </body>
                </html>
                """
                send_email_flask_mail(hr_email, email_subject_hr, email_body_hr)
            if hr_phone:
                send_whatsapp_template(
                    to_phone=hr_phone,
                    template_name="grievance_deleted_notification_hr",
                    lang_code="en",
                    parameters=[
                        hr_name or "HR",
                        grievance_id,
                        subject,
                        employee_name     
                    ]
                )

            flash('Query deleted successfully and notifications sent.', 'success')
    except Exception as e:
        conn.rollback()
        flash(f'Error deleting query: {str(e)}', 'error')
    finally:
        db_pool.putconn(conn)
    return redirect(url_for('my_queries'))

@app.route('/delete-grievance', methods=['POST'])
def delete_grievance():
    user = session.get('user')
    if not user or not user.get('authenticated') or user.get('role') != 'admin':
        flash('You do not have permission to delete query', 'error')
        return redirect(url_for('login'))

    grievance_id = request.form.get('grievance_id')
    reason = request.form.get('reason', '').strip()

    if not grievance_id or not reason:
        flash('Reason for deletion is required.', 'error')
        return redirect(url_for('master_dashboard'))

    conn = db_pool.getconn()
    try:
        with conn.cursor() as c:
            
            c.execute('SELECT employee_name, employee_email, employee_phone, subject FROM grievances WHERE id = %s', (grievance_id,))
            gr = c.fetchone()
            if not gr:
                flash('Query not found.', 'error')
                return redirect(url_for('master_dashboard'))
            employee_name, employee_email, employee_phone, subject = gr

            
            c.execute('DELETE FROM feedback WHERE grievance_id = %s', (grievance_id,))
            c.execute('DELETE FROM responses WHERE grievance_id = %s', (grievance_id,))
            c.execute('DELETE FROM reminder_sent WHERE grievance_id = %s', (grievance_id,))
            c.execute('DELETE FROM grievances WHERE id = %s', (grievance_id,))
            conn.commit()

            
            email_subject = f"Your Query Request Deleted (ID: {grievance_id})"
            email_body = f"""
            <html>
            <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #2c3e50;">
                <div style="max-width: 600px; margin: 0 auto; padding: 20px; background: #f7fafc; border-radius: 8px;">
                    <h2 style="color: #1e3a8a;">Query Deleted: Ask HR</h2> 
                    <p>Dear {employee_name},</p>
                    <p>Your query request with the following details has been <b>deleted</b> by the admin:</p>
                    <div style="background: white; padding: 20px; border-radius: 8px;">
                        <p><strong>Reference ID:</strong> {grievance_id}</p>
                        <p><strong>Subject:</strong> {subject}</p>
                        <p><strong>Status:</strong> Deleted</p>
                        <p><strong>Reason for Deletion:</strong> {reason}</p>
                    </div>
                    <p>If you have any questions, please contact HR.</p>
                    <p><strong>Human Resources</strong></p>
                </div>
            </body>
            </html>
            """
            send_email_flask_mail(employee_email, email_subject, email_body)

            if employee_phone:
                send_whatsapp_template(
                    to_phone=employee_phone,
                    template_name="grievance_deleted_notification",
                    lang_code="en",
                    parameters=[
                        employee_name,
                        grievance_id,
                        subject,
                        reason
                    ]
                )

            flash('Query deleted successfully and user notified.', 'success')
    except Exception as e:
        conn.rollback()
        flash(f'Error deleting query: {str(e)}', 'error')
    finally:
        db_pool.putconn(conn)

    return redirect(url_for('master_dashboard'))

@app.route('/manage-hr-mappings', methods=['GET', 'POST'])
def manage_hr_mappings():
    user = session.get('user')
    if not user or not user.get('authenticated') or user.get('role') != 'admin':
        flash('You do not have permission to manage HR mappings', 'error')
        return redirect(url_for('login'))

    conn = db_pool.getconn()
    try:
        with conn.cursor() as c:
            if request.method == 'POST':
                grievance_type = request.form.get('grievance_type')
                hr_emp_code = request.form.get('hr_emp_code')

                # Get the details of the HR person fetched from the hidden form fields
                hr_name = request.form.get('hr_employee_name')
                hr_email = request.form.get('hr_employee_email')
                hr_phone = request.form.get('hr_employee_phone')

                if not all([grievance_type, hr_emp_code, hr_name]):
                    flash('Missing HR details. Please fetch the HR person again before submitting.', 'error')
                    return redirect(url_for('manage_hr_mappings'))

                # --- New Logic: Add or update the user in the 'users' table ---
                # This ensures the user exists with an 'hr' role before mapping.
                c.execute('''
                    INSERT INTO users (emp_code, employee_name, employee_email, employee_phone, role, is_active)
                    VALUES (%s, %s, %s, %s, 'hr', TRUE)
                    ON CONFLICT (emp_code) DO UPDATE SET
                        employee_name = EXCLUDED.employee_name,
                        employee_email = EXCLUDED.employee_email,
                        employee_phone = EXCLUDED.employee_phone,
                        role = 'hr',
                        is_active = TRUE
                ''', (hr_emp_code, hr_name, hr_email, hr_phone))
                print(f"Upserted {hr_name} ({hr_emp_code}) into users table with role 'hr'.")


                # --- Existing Logic: Update the mapping table ---
                c.execute('''
                    INSERT INTO hr_grievance_mapping (grievance_type, hr_emp_code)
                    VALUES (%s, %s)
                    ON CONFLICT (grievance_type) DO UPDATE
                    SET hr_emp_code = EXCLUDED.hr_emp_code
                ''', (grievance_type, hr_emp_code))

                conn.commit()
                flash(f'HR mapping updated successfully. {hr_name} is now assigned.', 'success')
                return redirect(url_for('manage_hr_mappings'))

            # For the GET request, the logic remains mostly the same
            c.execute('''
                SELECT m.grievance_type, u.employee_name, u.emp_code
                FROM hr_grievance_mapping m
                LEFT JOIN users u ON m.hr_emp_code = u.emp_code
            ''')
            mapping_rows = c.fetchall()

            mappings = {}
            for row in mapping_rows:
                mappings[row[0]] = {
                    'name': row[1],
                    'emp_code': row[2]
                }
            
            # The hr_staff variable is no longer needed for the form,
            # but we keep it for now if other parts of the system use it.
            c.execute("SELECT emp_code, employee_name FROM users WHERE role = 'hr' ORDER BY employee_name")
            hr_staff = c.fetchall()

            return render_template('manage_mappings.html',
                                 mappings=mappings,
                                 grievance_types=GRIEVANCE_TYPES,
                                 hr_staff=hr_staff)
    finally:
        db_pool.putconn(conn)

@app.route('/get_current_hr/<grievance_id>')
def get_current_hr(grievance_id):
    user = session.get('user')
    if not user or not user.get('authenticated') or user.get('role') not in ['admin', 'hr']:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    
    conn = db_pool.getconn()
    try:
        with conn.cursor() as c:
            c.execute('''
                SELECT m.hr_emp_code
                FROM grievances g
                JOIN hr_grievance_mapping m ON g.grievance_type = m.grievance_type
                WHERE g.id = %s
            ''', (grievance_id,))
            result = c.fetchone()
            
            if result:
                return jsonify({'success': True, 'current_hr': result[0]})
            return jsonify({'success': False, 'error': 'No HR mapping found'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        db_pool.putconn(conn)

@app.route('/reassign-grievance', methods=['POST'])
def reassign_grievance():
    user = session.get('user')
    if not user or not user.get('authenticated') or user.get('role') not in ['admin','hr']:
        flash('You do not have permission to forward the query', 'error')
        return redirect(url_for('login'))

    grievance_id = request.form.get('grievance_id')
    new_hr_emp_code = request.form.get('new_hr')
    reason = request.form.get('reason')
    current_type = request.form.get('current_type')

    if not all([grievance_id, new_hr_emp_code, reason]):
        flash('Missing required information for the change', 'error')
        if user.get('role') == 'admin':
            return redirect(url_for('master_dashboard'))
        else:
            return redirect(url_for('hr_dashboard'))

    conn = db_pool.getconn()
    try:
        with conn.cursor() as c:
            
            c.execute('''
                SELECT g.id, g.employee_name, g.grievance_type, g.subject, u.employee_name, u.employee_email
                FROM grievances g
                LEFT JOIN hr_grievance_mapping m ON g.grievance_type = m.grievance_type
                LEFT JOIN users u ON m.hr_emp_code = u.emp_code
                WHERE g.id = %s
            ''', (grievance_id,))

            grievance = c.fetchone()
            if not grievance:
                flash('Query not found', 'error')
                if user.get('role') == 'admin':
                    return redirect(url_for('master_dashboard'))
                else:
                    return redirect(url_for('hr_dashboard'))

            
            c.execute('SELECT employee_name, employee_email, employee_phone FROM users WHERE emp_code = %s', (new_hr_emp_code,))
            new_hr = c.fetchone()
            if not new_hr:
                flash('Selected HR staff not found', 'error')
                if user.get('role') == 'admin':
                    return redirect(url_for('master_dashboard'))
                else:
                    return redirect(url_for('hr_dashboard'))

            
            c.execute('''
                INSERT INTO hr_grievance_mapping (grievance_type, hr_emp_code)
                VALUES (%s, %s)
                ON CONFLICT (grievance_type) DO UPDATE
                SET hr_emp_code = EXCLUDED.hr_emp_code
            ''', (f"temp_{grievance_id}", new_hr_emp_code))

            
            c.execute('''
                UPDATE grievances
                SET grievance_type = %s
                WHERE id = %s
            ''', (f"temp_{grievance_id}", grievance_id))

            
            c.execute('''
                INSERT INTO responses
                (grievance_id, responder_email, responder_name, response_text, response_date)
                VALUES (%s, %s, %s, %s, %s)''',
                (grievance_id, user.get('employee_email'), user.get('employee_name'),
                f"Grievance forwarded to {new_hr[0]} by admin. Reason: {reason}", datetime.now()))

            conn.commit()

            
            notify_subject = f"Query forwarded to You - {grievance[3]} (ID: {grievance_id})"
            notify_body = f"""
            <html>
            <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #2c3e50;">
                <div style="max-width: 600px; margin: 0 auto; padding: 20px; background: #f7fafc; border-radius: 8px;">
                    <h2 style="color:#1e3a8a;">Query forwarded: Ask HR</h2> 
                        Query forwarded to You (ID: {grievance_id})
                    </h2>
                    <p>Dear {new_hr[0]},</p>
                    <p>A query has been <b>forwarded</b> to you by {user.get('employee_name')}:</p>
                    <div style="background: white; padding: 20px; border-radius: 8px;">
                        <p><strong>Query ID:</strong> {grievance_id}</p>
                        <p><strong>Query Type:</strong> {grievance[2]}</p>
                        <p><strong>Employee:</strong> {grievance[1]}</p>
                        <p><strong>Subject:</strong> {grievance[3]}</p>
                        <p><strong>Reason for change:</strong> {reason}</p>
                    </div>
                    <p>Please review and respond as soon as possible.</p>
                    <p><a href="{url_for('respond_grievance', grievance_id=grievance_id, _external=True)}"
                        style="display:inline-block; background:#1e3a8a; color:#fff; padding:10px 18px; border-radius:5px; text-decoration:none; font-weight:bold;">
                        Respond Now</a>
                    </p>
                    <p><em>Human Resources</em></p>
                </div>
            </body>
            </html>
            """

            
            send_email_flask_mail(new_hr[1], notify_subject, notify_body)

            
            if new_hr[2]:
                send_whatsapp_template(
                    to_phone=new_hr[2],  
                    template_name="grievance_reassigned_hr",
                    lang_code="en",
                    parameters=[
                        new_hr[0],         
                        grievance_id,      
                        grievance[3],      
                        datetime.now().strftime('%d-%m-%Y, %H:%M:%S')  
                    ]
                )
            
            if grievance[5]:  
                prev_notify_subject = f"Query forwarded - {grievance[3]} (ID: {grievance_id})"
                prev_notify_body = f"""
                <html>
                <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #2c3e50;">
                    <div style="max-width: 600px; margin: 0 auto; padding: 20px; background: #f7fafc; border-radius: 8px;">
                        <h2 style="color: #1e3a8a;">Query forwarded:Ask HR</h2>
                                Query Forwarded (ID: {grievance_id})
                        </h2>
                        <p>Dear {grievance[4]},</p>
                        <p>A query previously assigned to you has been <b>fowarded</b> to {new_hr[0]} by {user.get('employee_name')}:</p>
                        <div style="background: white; padding: 20px; border-radius: 8px;">
                            <p><strong>Query ID:</strong> {grievance_id}</p>
                            <p><strong>Employee:</strong> {grievance[1]}</p>
                            <p><strong>Subject:</strong> {grievance[3]}</p>
                            <p><strong>Reason for change:</strong> {reason}</p>
                        </div>
                        <p><em> Human Resources </em></p>
                    </div>
                </body>
                </html>
                """
                send_email_flask_mail(grievance[5], prev_notify_subject, prev_notify_body)

            flash(f'Query successfully forwarded to {new_hr[0]}', 'success')
            if user.get('role') == 'admin':
                return redirect(url_for('master_dashboard'))
            else:
                return redirect(url_for('hr_dashboard'))

    except Exception as e:
        conn.rollback()
        print(f"Error in reassigning grievance: {str(e)}")
        print(traceback.format_exc())
        flash(f'Error reassigning grievance: {str(e)}', 'error')
        if user.get('role') == 'admin':
            return redirect(url_for('master_dashboard'))
        else:
            return redirect(url_for('hr_dashboard'))
    finally:
        db_pool.putconn(conn)

@app.route('/update-mapping', methods=['POST'])
def update_mapping():
    return redirect(url_for('manage_hr_mappings'))

@app.route('/logout')
def logout():
    session.pop('user', None)
    flash('You have been logged out successfully', 'success')
    return redirect(url_for('index'))

@app.route('/resend_otp', methods=['POST'])
def resend_otp():
    user_type = request.form.get('user_type')
    emp_code = request.form.get('emp_code')
    phone = request.form.get('employee_phone')
    email = request.form.get('employee_email')
    name = request.form.get('employee_name', '')

    otp = generate_otp()
    session['otp'] = otp
    session['otp_time'] = datetime.now().timestamp()
    email_subject = "OTP Verification: Ask HR"
    email_body = f"""
<html>
<body style="font-family: Arial, sans-serif; line-height: 1.6; color: #2c3e50;">
    <div style="max-width: 600px; margin: 0 auto; padding: 20px; background: #f7fafc; border-radius: 8px;">
        <h2 style="color: #1e3a8a;">OTP Verification: Ask HR</h2> 
        <p>Dear {name or emp_code},</p>
        <p>Your OTP for Ask HR Portal Login is: {otp}</p>
        <p>This code will expire in 05 minutes.</p>
        <p>Don't share this OTP with anyone.</p>
        <p><strong>Human Resources</strong></p>
    </div>
</body>
</html>
"""

    if phone:
        send_whatsapp_template(
            to_phone=phone,
            template_name="otp_login_verification",
            lang_code="en",
            parameters=[otp]
        )
    if email:
        send_email_flask_mail(email, email_subject, email_body)

    return jsonify({'success': True, 'message': 'OTP resent successfully!'})

def parse_sap_date(date_string):
    """Parse SAP date format like /Date(1749168000000)/"""
    if not date_string or not isinstance(date_string, str):
        return None

    import re
    from datetime import datetime

    
    match = re.search(r'/Date\((\d+)\)/', date_string)
    if not match:
        return None

    
    milliseconds = int(match.group(1))
    seconds = milliseconds / 1000  
    dt = datetime.fromtimestamp(seconds)

    return dt

@app.route('/api/get_employee_sap', methods=['GET'])
def get_employee_sap():
    start_time = time.time()
    emp_code = request.args.get('emp_code')

    print(f"\n" + "="*50)
    print(f"📡 API REQUEST: Fetching employee data for: {emp_code}")
    print("="*50)

    if not emp_code:
        print("❌ ERROR: No employee code provided")
        return jsonify({'success': False, 'error': 'No employee code provided'}), 400

    try:
        url = f"https://api44.sapsf.com/odata/v2/EmpJob?$select=division,divisionNav/name,location,locationNav/name,userId,employmentNav/personNav/personalInfoNav/firstName,employmentNav/personNav/personalInfoNav/middleName,employmentNav/personNav/personalInfoNav/lastName,department,departmentNav/name,employmentNav/personNav/emailNav/emailAddress,employmentNav/personNav/phoneNav/phoneNumber,employmentNav/personNav/dateOfBirth,emplStatusNav/picklistLabels/label&$expand=employmentNav/personNav/personalInfoNav,divisionNav,locationNav,departmentNav,employmentNav/personNav/phoneNav,employmentNav/personNav/emailNav,emplStatusNav/picklistLabels&$filter=userId eq '{emp_code}'&$format=json"

        print(f"🔗 Using API URL: {url}")

        username = os.environ.get('SAP_API_USERNAME')
        password = os.environ.get('SAP_API_PASSWORD')

        print(f"👤 Using username: {username}")
        print(f"🔑 Password provided: {'Yes' if password else 'No'}")

        print(f"🚀 Sending API request with 5-second timeout...")
        response = requests.get(
            url,
            auth=HTTPBasicAuth(username, password),
            timeout=5,
            headers={'Cache-Control': 'no-cache'}
        )

        api_time = time.time() - start_time
        print(f"⏱️ API responded in {api_time:.2f} seconds with status: {response.status_code}")

        if response.status_code != 200:
            print(f"❌ API ERROR: Status code {response.status_code}")
            print(f"Response text: {response.text[:200]}...")
            return jsonify({
                'success': False,
                'error': f'API returned status code {response.status_code}'
            }), 500

        data = response.json()
        results = data.get('d', {}).get('results', [])

        if not results:
            print(f"❌ No results found for employee ID: {emp_code}")
            return jsonify({
                'success': False,
                'error': f'No employee found with ID: {emp_code}'
            }), 404

        result = results[0]
        print(f"✅ Found employee data, processing...")

        def safe_get(data, *keys):
            for key in keys:
                if isinstance(data, dict):
                    data = data.get(key)
                elif isinstance(data, list) and isinstance(key, int) and len(data) > key:
                    data = data[key]
                else:
                    return None
                if data is None:
                    return None
            return data
                # Check Employee Status
        print(f"🔍 CHECKING EMPLOYEE STATUS:")
        employee_status = safe_get(result, 'emplStatusNav', 'picklistLabels', 'results', 0, 'label')
        print(f"  Employee Status: {employee_status}")

        if not employee_status or employee_status.lower() != 'active':
            print(f"❌ Employee {emp_code} is not active. Status: {employee_status}")
            return jsonify({
                'success': False,
                'error': f'Employee {emp_code} is not active. Current status: {employee_status or "Unknown"}. Only active employees can submit queries.'
            }), 403

        print(f"✅ Employee {emp_code} is active, proceeding with data extraction...")
        personal_info = safe_get(result, 'employmentNav', 'personNav', 'personalInfoNav', 'results', 0)
        first_name = safe_get(personal_info, 'firstName') or ''
        middle_name = safe_get(personal_info, 'middleName') or ''
        last_name = safe_get(personal_info, 'lastName') or ''

        date_of_birth_raw = safe_get(result, 'employmentNav', 'personNav', 'dateOfBirth')
        date_of_birth = None

        if date_of_birth_raw:
            print(f"📅 Raw DOB: {date_of_birth_raw}")
            dob_date = parse_sap_date(date_of_birth_raw)
            if dob_date:
                date_of_birth = dob_date.strftime('%Y-%m-%d')
                print(f"📅 Parsed DOB: {date_of_birth}")
            else:
                print(f"⚠️ Could not parse DOB: {date_of_birth_raw}")

        division = safe_get(result, 'divisionNav', 'name') or result.get('division', '')
        department = safe_get(result, 'departmentNav', 'name') or result.get('department', '')

        print(f"🔍 PHONE EXTRACTION (STRICT MODE):")
        phone_nav = safe_get(result, 'employmentNav', 'personNav', 'phoneNav')
        print(f"  Raw phone data: {phone_nav}")

        phone_number = ""
        phone_results = None

        if isinstance(phone_nav, dict) and 'results' in phone_nav:
            phone_results = phone_nav.get('results')
        elif isinstance(phone_nav, list):
            phone_results = phone_nav

        if phone_results and len(phone_results) > 1:
            phone_item_at_index_1 = phone_results[1]
            if isinstance(phone_item_at_index_1, dict) and phone_item_at_index_1.get('phoneNumber'):
                phone_number = phone_item_at_index_1['phoneNumber']
                print(f"  ✅ Found required phone number at index 1: {phone_number}")
            else:
                print("  ⚠️ Item at index 1 is not a valid phone object or has no number.")
        else:
            print("  ⚠️ Phone results list does not have an item at index 1. No other fallbacks will be used.")

        if phone_number:
            phone_number = ''.join(filter(str.isdigit, phone_number))

            if phone_number and len(phone_number) >= 10:
                if not phone_number.startswith('+'):
                    if phone_number.startswith('91'):
                        phone_number = '+91' + phone_number
                    else:
                        phone_number = '+91' + phone_number
            else:
                print("  ⚠️ Phone number format invalid, using empty value")
                phone_number = ""
        else:
            print("  ⚠️ No phone number found using the strict index 1 rule.")

        print(f"  📱 Final phone number: {phone_number}")

        full_name = f"{first_name} {middle_name} {last_name}".replace('  ', ' ').strip()

        work_email = ''  
        email_list = safe_get(result, 'employmentNav', 'personNav', 'emailNav', 'results')

        print(f"📧 EMAIL EXTRACTION DEBUG: Found email list: {email_list}")

        if isinstance(email_list, list):
            for email_item in email_list:
                email_address = safe_get(email_item, 'emailAddress')
                if email_address and '@nvtpower.com' in email_address.lower():
                    work_email = email_address
                    print(f"  ✅ Found work email: {work_email}")
                    break 
        email = work_email

        employee_data = {
            'emp_code': emp_code,
            'employee_name': full_name,
            'employee_email': email,
            'employee_phone': phone_number,
            'date_of_birth': date_of_birth,
            'business_unit': division,
            'department': department
        }

        print(f"📤 Returning employee data: {employee_data}")
        total_time = time.time() - start_time
        print(f"⏱️ Total processing time: {total_time:.2f} seconds")
        print("="*50)

        return jsonify({'success': True, 'employee': employee_data})

    except requests.exceptions.Timeout:
        print(f"⏰ API request timed out after 5 seconds")
        return jsonify({
            'success': False,
            'error': 'API request timed out. Please try again.'
        }), 504
    except requests.exceptions.RequestException as e:
        print(f"🌐 Network error: {str(e)}")
        return jsonify({
            'success': False,
            'error': f'Network error: {str(e)}'
        }), 503
    except Exception as e:
        print(f"❌ Unexpected error: {str(e)}")
        print(traceback.format_exc())
        return jsonify({
            'success': False,
            'error': f'Error: {str(e)}'
        }), 500

@app.route('/privacy-policy')
def privacy_policy():
    return render_template('privacy.html')

@app.route('/terms-of-service')
def terms_of_service():
    return render_template('terms.html')

@app.route('/download/<user_type>/<emp_code>/<filename>')
def download_file(user_type, emp_code, filename):
    try:
        if user_type not in ['employee', 'hr','admin']:
            flash('Invalid file type requested.', 'error')
            return redirect(url_for('index'))
        
        file_directory = os.path.join(UPLOAD_FOLDER, user_type, emp_code)
        
        if not os.path.exists(file_directory):
            flash('File not found.', 'error') 
            return redirect(url_for('index'))
        
        full_file_path = os.path.join(file_directory, filename)
        if not os.path.exists(full_file_path):
            flash('File not found.', 'error')
            return redirect(url_for('index'))
            
        print(f"📁 Serving file: {full_file_path}")
        
        return send_from_directory(
            directory=file_directory,
            path=filename,
            as_attachment=False  
        )        
    except Exception as e:
        print(f"❌ Download error: {str(e)}")
        flash('Error accessing file.', 'error')
        return redirect(url_for('index'))

if __name__ == '__main__':
    init_db()
    # Immediate runs wrapper
    def run_overdue_scan():
        with app.app_context():
            check_pending_grievances(debug=True)

    def run_daily_summary():
        with app.app_context():
            send_daily_hr_pending_summary(debug=True)

    # Overdue scan scheduler (hourly)
    scheduler_overdue = BackgroundScheduler()
    scheduler_overdue.add_job(
        func=run_overdue_scan,
        trigger=IntervalTrigger(hours=REMINDER_SCAN_INTERVAL_HOURS),
        id='overdue_scan',
        replace_existing=True,
        next_run_time=datetime.now()  # immediate
    )
    scheduler_overdue.start()
    print("📅 Overdue reminder scheduler (hourly + immediate) started")

    # Daily HR summary (09:00 & 16:00)
    scheduler_daily = BackgroundScheduler()
    scheduler_daily.add_job(
        func=run_daily_summary,
        trigger=CronTrigger(hour="9,16", minute=0),
        id='daily_hr_summary',
        replace_existing=True,
        next_run_time=datetime.now()  # first immediate for verification
    )
    scheduler_daily.start()
    print("📅 Daily HR summary scheduler (09:00 & 16:00 + immediate) started")

    # Existing feedback reminders (daily at startup time)
    scheduler_feedback = BackgroundScheduler()
    scheduler_feedback.add_job(
        func=lambda: send_pending_feedback_reminders(),
        trigger=IntervalTrigger(days=1),
        id='feedback_reminders',
        replace_existing=True,
        next_run_time=datetime.now()
    )
    scheduler_feedback.start()
    print("📅 Feedback reminder scheduler (daily + immediate) started")

    # Defensive immediate executions
    run_overdue_scan()
    run_daily_summary()

    app.run(debug=True, use_reloader=False)