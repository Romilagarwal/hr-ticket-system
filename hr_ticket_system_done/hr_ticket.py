from flask import Flask, render_template, render_template_string, request, redirect, url_for, flash, jsonify
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
import time
import random
import string
from flask import session, make_response
import smtplib
import requests
from requests.auth import HTTPBasicAuth

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY')

SERVER_HOST = os.environ.get('SERVER_HOST')

app.config['SERVER_NAME'] = os.environ.get('SERVER_NAME')
app.config['PREFERRED_URL_SCHEME'] = os.environ.get('PREFERRED_URL_SCHEME', 'http')
app.config['APPLICATION_ROOT'] = '/'

UPLOAD_FOLDER = 'uploads'
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
    'income_tax': 'Income Tax related Queries'
}

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

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

            hr_users = [
                ('HR001', 'HR Leave Manager', '8318436133', 'Danshi.Gupta@nvtpower.com', 'hr'),
                ('HR002', 'HR Salary Manager', '8318436133', 'Deep.Dey@nvtpower.com', 'hr'),
                ('ADMIN001', 'System Admin', '8318436133', 'romil.agarwal@nvtpower.com', 'admin')
            ]

            for user in hr_users:
                c.execute('''INSERT INTO users
                            (emp_code, employee_name, employee_phone, employee_email, role)
                            VALUES (%s, %s, %s, %s, %s)
                            ON CONFLICT (emp_code) DO NOTHING''', user)

            grievance_mappings = [
                ('leave_attendance', 'HR001'),
                ('salary_queries', 'HR002'),
                ('policies', 'HR001'),
                ('reimbursement', 'HR002'),
                ('weconnect', 'HR001'),
                ('promotion_performance', 'HR001'),
                ('npower_issue', 'HR002'),
                ('ascent_issue', 'HR001'),
                ('hr_forms_documents', 'HR001'),
                ('exit_settlement', 'HR002'),
                ('letters', 'HR001'),
                ('confirmation', 'HR001'),
                ('others', 'HR001'),
                ('recruitment', 'HR002'),
                ('provident_fund', 'HR002'),
                ('mediclaim', 'HR002'),
                ('canteen_food', 'HR002'),
                ('transportation', 'HR002'),
                ('reward', 'HR001'),
                ('address_proof', 'HR001'),
                ('id_card', 'HR001'),
                ('income_tax', 'HR002')
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
    print("\n" + "="*60)
    print("📧 FLASK-MAIL EMAIL SENDING STARTED")
    print("="*60)

    MAX_RETRIES = 3
    BASE_DELAY = 5

    retry_count = 0

    while retry_count < MAX_RETRIES:
        try:
            print(f"🔧 CONFIGURATION:")
            print(f"   Mail Server: {app.config['MAIL_SERVER']}")
            print(f"   Mail Port: {app.config['MAIL_PORT']}")
            print(f"   Use TLS: {app.config['MAIL_USE_TLS']}")
            print(f"   From Email: {app.config['MAIL_USERNAME']}")
            print(f"   To Email: {to_email}")
            print(f"   Subject: {subject}")
            print(f"   Has Attachment: {attachment_path is not None}")

            if attachment_path:
                print(f"   Attachment Path: {attachment_path}")
                print(f"   Attachment Exists: {os.path.exists(attachment_path)}")
                if os.path.exists(attachment_path):
                    print(f"   File Size: {os.path.getsize(attachment_path)} bytes")

            print(f"\n🔨 CREATING MESSAGE...")
            msg = Message(
                subject=subject,
                recipients=[to_email],
                html=body
            )
            print(f"✅ Message object created successfully")
            print(f"   Body length: {len(body)} characters")

            if attachment_path and os.path.exists(attachment_path):
                print(f"\n📎 ATTACHING FILE...")
                try:
                    with app.open_resource(attachment_path) as fp:
                        msg.attach(
                            filename=os.path.basename(attachment_path),
                            content_type="application/octet-stream",
                            data=fp.read()
                        )
                    print(f"✅ Attachment added: {os.path.basename(attachment_path)}")
                except Exception as attachment_error:
                    print(f"❌ ATTACHMENT ERROR: {str(attachment_error)}")
                    print(f"   Continuing without attachment...")
            elif attachment_path:
                print(f"⚠️  Attachment path provided but file doesn't exist: {attachment_path}")

            print(f"\n📤 SENDING EMAIL... (Attempt {retry_count + 1}/{MAX_RETRIES})")

            with mail.connect() as conn:
                conn.send(msg)

            print(f"✅ Email sent successfully using Flask-Mail!")
            print(f"\n🎉 EMAIL SENDING COMPLETED SUCCESSFULLY!")
            print("="*60)
            return True

        except smtplib.SMTPSenderRefused as e:
            if "rate" in str(e).lower() and retry_count < MAX_RETRIES - 1:
                retry_delay = BASE_DELAY * (2 ** retry_count)
                print(f"\n⚠️ RATE LIMIT EXCEEDED. Waiting {retry_delay} seconds before retry...")
                time.sleep(retry_delay)
                retry_count += 1
            else:
                print(f"\n❌ EMAIL SENDING ERROR (RATE LIMIT):")
                print(f"   Error: {str(e)}")
                print("="*60)
                return False

        except Exception as e:
            print(f"\n❌ EMAIL SENDING ERROR:")
            print(f"   Error type: {type(e).__name__}")
            print(f"   Error message: {str(e)}")
            import traceback
            print(f"\n🔍 FULL TRACEBACK:")
            print(f"   {traceback.format_exc()}")
            print("="*60)

            if retry_count < MAX_RETRIES - 1:
                retry_delay = BASE_DELAY * (2 ** retry_count)
                print(f"⏳ Retrying in {retry_delay} seconds... (Attempt {retry_count + 1}/{MAX_RETRIES})")
                time.sleep(retry_delay)
                retry_count += 1
            else:
                return False

    return False

def send_whatsapp_template(to_phone, template_name, lang_code, parameters):
    """
    Send a WhatsApp template message using Meta's Cloud API v22.0.
    :param to_phone: Recipient phone number in international format, e.g. '919999999999'
    :param template_name: Name of the approved template, e.g. 'grievance_reassigned_hr'
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
    image_url = "https://i.ibb.co/xKYMdfg96/ask-hr-logo.png"
    components = [
        {
            "type": "header",
            "parameters": [
                {
                    "type": "image",
                    "image": {
                        "link": image_url
                    }
                }
            ]
        },
        {
            "type": "body",
            "parameters": [{"type": "text", "text": str(val)} for val in parameters]
        }
    ]
    payload = {
        "messaging_product": "whatsapp",
        "to": to_phone,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": lang_code},
            "components": components
        }
    }
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=10)
        print(f"WhatsApp API response: {resp.status_code} {resp.text}")
        return resp.status_code == 200
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
            return redirect(url_for('my_grievances'))

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

        if not all([emp_code, employee_name, employee_email, grievance_type, subject, description]):
            print(f"❌ VALIDATION FAILED: Missing required fields")
            flash('Please fill in all required fields.', 'error')
            return redirect(url_for('index'))

        print(f"✅ Form validation passed")

        attachment_path = None
        if 'attachment' in request.files:
            file = request.files['attachment']
            print(f"\n📁 FILE UPLOAD:")
            print(f"   File present: {file is not None}")
            print(f"   Filename: {file.filename if file else 'None'}")

            if file and file.filename != '' and allowed_file(file.filename):
                filename = secure_filename(f"{grievance_id}_{file.filename}")
                attachment_path = os.path.join(UPLOAD_FOLDER, filename)
                file.save(attachment_path)
                print(f"   ✅ File saved: {attachment_path}")
                print(f"   File size: {os.path.getsize(attachment_path)} bytes")
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
        email_subject = f"New Grievance Submitted - {subject} (ID: {grievance_id})"

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
                    hr_email = 'romil.agarwal@nvtpower.com'
                    hr_name = 'System Admin'
                    print(f"⚠️ No HR mapping found for type: {grievance_type}, using default")
        except Exception as e:
            hr_email = 'romil.agarwal@nvtpower.com'
            hr_name = 'System Admin'
            print(f"❌ Error finding HR email: {str(e)}")

        email_body = f"""
<html>
<body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
    <div style="max-width: 600px; margin: 0 auto; padding: 20px; background: #f9f9f9;">
        <h2 style="color: #2c5aa0;">Grievance Submission: Ask HR: {employee_name}</h2>
        <p>Dear {hr_name},</p>
        <p>Below grievance has been submitted by employees:</p>
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
        email_success = send_email_flask_mail(hr_email, email_subject, email_body, attachment_path)
        if hr_phone:
            print(f"📱 Sending WhatsApp notification to HR...")
            whatsapp_success = send_whatsapp_template(
                to_phone=hr_phone,
                template_name="new_grievance_notification_hr",
                lang_code="en",
                parameters=[
                    employee_name,
                    hr_name,
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
            <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
                <div style="max-width: 600px; margin: 0 auto; padding: 20px; background: #f9f9f9;">
                    <h2 style="color: #2c5aa0;">Grievance Submission Confirmation: Ask HR</h2>
                    <p>Dear {employee_name},</p>
                    <p>Your grievance has been successfully submitted with the following details:</p>
                    <div style="background: white; padding: 20px; border-radius: 8px;">
                        <p><strong>Reference ID:</strong> {grievance_id}</p>
                        <p><strong>Subject:</strong> {subject}</p>
                        <p><strong>Status:</strong> Submitted</p>
                        <p><strong>Submission Date:</strong> {datetime.now().strftime('%d-%m-%Y, %H:%M:%S')}</p>
                    </div>
                    <p>Please keep the Reference ID for tracking the Grievance Status.</p>
                    <p><strong>Human Resources</strong></p>
                </div>
            </body>
            </html>
            """
            send_email_flask_mail(employee_email, f"Grievance Submission Confirmation (ID: {grievance_id})", employee_email_body)

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

            flash(f'Your grievance has been submitted successfully! Reference ID: {grievance_id}', 'success')
        else:
            print(f"❌ EMAIL SENDING FAILED!")
            flash(f'Grievance submitted (ID: {grievance_id}) but email notification failed. Please contact IT support.', 'warning')

        print(f"\n🎉 GRIEVANCE SUBMISSION COMPLETED")
        print("="*60)
        return redirect(url_for('index'))

    except Exception as e:
        print(f"\n💥 SUBMISSION ERROR:")
        print(f"   Error: {str(e)}")
        import traceback
        print(f"   Traceback: {traceback.format_exc()}")
        print("="*60)
        flash(f'An error occurred while submitting your grievance: {str(e)}', 'error')
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
                c.execute('SELECT employee_name, employee_email, employee_phone FROM users WHERE emp_code = %s AND role = %s', (emp_code, user_type))
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
        flash('You do not have permission to respond to grievances', 'error')
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
                flash('Grievance not found.', 'error')
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
                        filename = secure_filename(f"response_{grievance_id}_{file.filename}")
                        response_attachment_path = os.path.join(UPLOAD_FOLDER, filename)
                        file.save(response_attachment_path)

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
                response_email_body = f"""
<html>
<body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
    <div style="max-width: 600px; margin: 0 auto; padding: 20px; background: #f9f9f9;">
        <h2 style="color: #2c5aa0;">Grievance Resolution Confirmation: Ask HR</h2>
        <p>Dear {employee_name},</p>
        <p>Your grievance has been successfully resolved with the following details:</p>
        <div style="background: white; padding: 20px; border-radius: 8px;">
            <p><strong>Reference ID:</strong> {grievance_id}</p>
            <p><strong>Subject:</strong> {grievance[8]}</p>
            <p><strong>Status:</strong> Resolved</p>
            <p><strong>Resolution Date:</strong> {response_date.strftime('%d-%m-%Y, %H:%M:%S')}</p>
        </div>
        <p>Please click on the below link to submit the feedback.</p>
        <p>
            <a href="{url_for('feedback', grievance_id=grievance_id, response='', _external=True)}"
               style="background: #3498db; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">
               Feedback
            </a>
        </p>
        <p><strong>Human Resources</strong></p>
    </div>
</body>
</html>
"""
                send_email_flask_mail(employee_email, f"Grievance Response (ID: {grievance_id})", response_email_body, response_attachment_path)

                employee_phone = grievance[4]
                if employee_phone:
                    send_whatsapp_template(
                        to_phone=employee_phone,
                        template_name="grievance_resolution_confirmation",
                        lang_code="en",
                        parameters=[
                            employee_name,         # Employee Name
                            grievance_id,      # Reference ID
                            grievance[3],      # Subject
                            datetime.now().strftime('%d-%m-%Y, %H:%M:%S')  # Submission Date
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
                flash('Grievance not found.', 'error')
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

            print(f"   Satisfaction: {satisfaction}")
            print(f"   Rating: {rating}")
            print(f"   Comments: {feedback_comments}")

            if not all([satisfaction, rating]):
                print(f"   ❌ Missing required fields!")
                flash('Please provide both satisfaction status and rating.', 'error')
                return redirect(url_for('feedback', grievance_id=grievance_id, response=satisfaction))

            if satisfaction == 'not_resolved':
                if not feedback_comments or len(feedback_comments.strip()) < 30:
                    print(f"   ❌ Comments too short for not_resolved!")
                    flash('Comments must be at least 30 characters for Not Resolved.', 'error')
                    return redirect(url_for('feedback', grievance_id=grievance_id, response=satisfaction))

            feedback_date = datetime.now()

            print(f"   💾 Saving feedback to database...")

            c.execute('''INSERT INTO feedback
                        (grievance_id, satisfaction, rating, feedback_comments, feedback_date)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (grievance_id) DO UPDATE
                        SET satisfaction = EXCLUDED.satisfaction,
                            rating = EXCLUDED.rating,
                            feedback_comments = EXCLUDED.feedback_comments,
                            feedback_date = EXCLUDED.feedback_date''',
                     (grievance_id, satisfaction, int(rating), feedback_comments, feedback_date))

            print(f"   ✅ Feedback saved successfully!")

            if satisfaction == 'not_resolved':
                c.execute('UPDATE grievances SET status = %s, updated_at = %s WHERE id = %s',
                         ('Reopened', datetime.now(), grievance_id))
                print(f"   ✅ Grievance status updated to: Reopened")
            else:
                c.execute('UPDATE grievances SET status = %s, updated_at = %s WHERE id = %s',
                         ('Resolved', datetime.now(), grievance_id))
                print(f"   ✅ Grievance status updated to: Resolved")

            conn.commit()

            if satisfaction == 'not_resolved':
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

                notify_subject = f"Grievance Reopened - {subject} (ID: {grievance_id})"
                notify_body = f"""
                <html>
                <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
                    <div style="max-width: 600px; margin: 0 auto; padding: 20px; background: #f9f9f9;">
                        <h2 style="color: #e74c3c; border-bottom: 2px solid #e74c3c; padding-bottom: 10px;">
                            Grievance Reopened (ID: {grievance_id})
                        </h2>
                        <p>Dear {hr_name or 'HR'},</p>
                        <p>The following grievance has been <b>reopened</b> by the employee:</p>
                        <div style="background: white; padding: 20px; border-radius: 8px;">
                            <p><strong>Grievance ID:</strong> {grievance_id}</p>
                            <p><strong>Employee Name:</strong> {employee_name}</p>
                            <p><strong>Subject:</strong> {subject}</p>
                            <p><strong>Status:</strong> Reopened</p>
                        </div>
                        <p>Please review and respond as soon as possible.</p>
                        <p><em>Human Resources </em></p>
                    </div>
                </body>
                </html>
                """
                whatsapp_message = f"""🔄 *Grievance Reopened*

Grievance ID: {grievance_id}
Employee: {employee_name}
Subject: {subject}
Status: Reopened

Please review and respond as soon as possible.

- Human Resources
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
            return redirect(url_for('my_grievances'))

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

def check_pending_grievances():
    print("\n" + "="*60)
    print("⏰ CHECKING FOR PENDING GRIEVANCES")
    print("="*60)

    cutoff_time = datetime.now() - timedelta(minutes=2)

    conn = db_pool.getconn()
    try:
        with conn.cursor() as c:
            c.execute("SELECT employee_email, employee_phone FROM users WHERE role = 'admin' LIMIT 1")
            admin_row = c.fetchone()
            admin_email = admin_row[0] if admin_row else None
            admin_phone = admin_row[1] if admin_row else None

            c.execute('''
                SELECT g.id, g.employee_name, g.employee_email, g.subject, g.submission_date, m.hr_emp_code, u.employee_email, u.employee_phone
                FROM grievances g
                LEFT JOIN hr_grievance_mapping m ON g.grievance_type = m.grievance_type
                LEFT JOIN users u ON m.hr_emp_code = u.emp_code
                LEFT JOIN reminder_sent r ON g.id = r.grievance_id
                WHERE g.status = 'Submitted'
                AND g.submission_date < %s
                AND r.grievance_id IS NULL
            ''', (cutoff_time,))

            pending_grievances = c.fetchall()

            if not pending_grievances:
                print(f"✅ No pending grievances requiring attention")
                return

            print(f"⚠️ Found {len(pending_grievances)} pending grievances requiring attention")

            for grievance in pending_grievances:
                grievance_id = grievance[0]
                employee_name = grievance[1]
                subject = grievance[3]
                submission_date = grievance[4]
                hr_email = grievance[6] or admin_email
                hr_phone = grievance[7] or admin_phone

                # Fetch HR name for WhatsApp notification (if available)
                hr_name = None
                if grievance[5]:  # grievance[5] is hr_emp_code
                    c.execute("SELECT employee_name FROM users WHERE emp_code = %s", (grievance[5],))
                    hr_name_row = c.fetchone()
                    hr_name = hr_name_row[0] if hr_name_row else None

                hours_pending = (datetime.now() - submission_date).total_seconds() / 3600

                print(f"📝 Processing grievance {grievance_id}: {subject}")
                print(f"   Submitted: {submission_date}, Hours pending: {hours_pending:.1f}")

                full_url = f"{SERVER_HOST}/respond/{grievance_id}"
                if not full_url.startswith(('http://', 'https://')):
                    full_url = f"http://{full_url}"

                email_subject = f"Urgent: Grievance Pending for over {int(hours_pending)} Hours: Ask HR"
                email_body = f"""
<html>
<body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
    <div style="max-width: 600px; margin: 0 auto; padding: 20px; background: #f9f9f9;">
        <h2 style="color: #e74c3c; border-bottom: 2px solid #e74c3c; padding-bottom: 10px;">
            Urgent: Grievance Pending for over {int(hours_pending)} Hours: Ask HR
        </h2>
        <p>This is an automated reminder that the following grievance has been pending without resolution:</p>
        <p>The grievance was successfully submitted with the following details:</p>
        <div style="background: white; padding: 20px; border-radius: 8px; margin-top: 20px;">
            <p><strong>Grievance ID:</strong> {grievance_id}</p>
            <p><strong>Employee Name:</strong> {employee_name}</p>
            <p><strong>Subject:</strong> {subject}</p>
            <p><strong>Submission Date:</strong> {submission_date.strftime('%d-%m-%Y, %H:%M:%S')}</p>
        </div>
        <p style="margin-top: 20px;">Please review and respond to this grievance as soon as possible.</p>
        <p><a href="{full_url}"
            style="background: #3498db; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">
            Respond Now</a>
        </p>
        <p><strong>Human Resources</strong></p>
    </div>
</body>
</html>
"""

                recipients = [hr_email]
                if admin_email and admin_email != hr_email:
                    recipients.append(admin_email)
                for recipient in recipients:
                    send_email_flask_mail(recipient, email_subject, email_body)

                if hr_phone:
                    send_whatsapp_template(
                        to_phone=hr_phone,
                        template_name="grievance_pending_reminder",
                        lang_code="en",
                        parameters=[
                            int(hours_pending),
                            grievance_id,
                            employee_name,
                            subject,
                            datetime.now().strftime('%d-%m-%Y, %H:%M:%S'),
                        ]
                    )
                if admin_phone and admin_phone != hr_phone:
                    send_whatsapp_template(
                        to_phone=admin_phone,
                        template_name="grievance_reopened_admin",
                        lang_code="en",
                        parameters=[
                            int(hours_pending),
                            grievance_id,
                            employee_name,
                            hr_name if hr_name else "",
                            subject,
                            datetime.now().strftime('%d-%m-%Y, %H:%M:%S'),
                        ]
                    )
                c.execute('INSERT INTO reminder_sent (grievance_id, reminder_date) VALUES (%s, %s)',
                          (grievance_id, datetime.now()))
                conn.commit()
                print(f"✅ Reminder sent for grievance {grievance_id}")
    except Exception as e:
        print(f"❌ Error checking pending grievances: {str(e)}")
        print(traceback.format_exc())
    finally:
        db_pool.putconn(conn)
        print("="*60)

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
    date_of_birth = request.form.get('date_of_birth', '')

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
            if user_type == 'employee' and auth_type == 'dob':
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
                return redirect(url_for('my_grievances'))

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
                else:
                    c.execute('''
                        SELECT id, role, employee_name FROM users
                        WHERE emp_code = %s
                        AND employee_phone = %s
                        AND employee_email = %s
                        AND role = %s
                    ''', (emp_code, employee_phone, employee_email, user_type))
                    user = c.fetchone()
                    if not user:
                        flash('Invalid credentials. Please check your details.', 'error')
                        return redirect(url_for('login'))
                    user_id = user[0]
                    user_role = user[1]
                    user_name = user[2]
                    user_email = employee_email
                    user_phone = employee_phone

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
    <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
        <div style="max-width: 600px; margin: 0 auto; padding: 20px; background: #f9f9f9;">
            <h2 style="color: #2c5aa0;">OTP Verification: Ask HR</h2>
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
        return redirect(url_for('my_grievances'))

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
                                      stats=stats)

            query = '''SELECT g.id, g.emp_code, g.employee_name, g.employee_email, g.grievance_type,
                        g.subject, g.status, g.submission_date,
                        f.rating , f.satisfaction , f.feedback_comments
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
                    'rating': g[8],
                    'satisfaction': g[9],
                    'feedback_comments': g[10]
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
                                  stats=stats)
    finally:
        db_pool.putconn(conn)

@app.route('/my-grievances')
def my_grievances():
    user = session.get('user')
    if not user or not user.get('authenticated'):
        flash('Please log in to view your grievances', 'error')
        return redirect(url_for('login'))

    server_host = os.environ.get('SERVER_HOST', 'http://localhost:5000')
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

            grievances = c.fetchall()

            print(f"Found {len(grievances)} grievances for emp_code {emp_code}")
            for g in grievances:
                print(f"Grievance {g[0]}: status={g[12]}, rating={g[15] if len(g) > 15 else 'N/A'}")

            c.execute('''
                SELECT status, COUNT(*)
                FROM grievances
                WHERE emp_code = %s
                GROUP BY status''', (emp_code,))

            status_counts = {status: count for status, count in c.fetchall()}

            total_grievances = sum(status_counts.values()) if status_counts else 0

            masked_phone = mask_phone(user['employee_phone'])

            return render_template('my_grievances.html',
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
                    f.rating, f.satisfaction, f.feedback_comments
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
                    'feedback_comments': g[11]
                })

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
                flash('Grievance not found.', 'error')
                return redirect(url_for('my_grievances'))
            if grievance[12] != 'Submitted':
                flash('You can only edit grievances that are still submitted.', 'error')
                return redirect(url_for('my_grievances'))
            edit_count = grievance[17] if len(grievance) > 17 and grievance[17] is not None else 0
            if edit_count >= 2:
                flash('You can only edit a grievance twice.', 'error')
                return redirect(url_for('my_grievances'))

            if request.method == 'POST':
                grievance_type = request.form.get('grievance_type')
                subject = request.form.get('subject')
                description = request.form.get('description')
                attachment_path = grievance[11]

                if 'attachment' in request.files:
                    file = request.files['attachment']
                    if file and file.filename != '' and allowed_file(file.filename):
                        filename = secure_filename(f"{grievance_id}_{file.filename}")
                        attachment_path = os.path.join(UPLOAD_FOLDER, filename)
                        file.save(attachment_path)

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

                # Fetch employee details
                employee_email = grievance[3]
                employee_name = grievance[2]
                employee_phone = grievance[4]

                # Notify employee
                email_subject = f"Your Grievance (ID: {grievance_id}) Has Been Updated"
                email_body = f"""
                <html>
                <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
                    <div style="max-width: 600px; margin: 0 auto; padding: 20px; background: #f9f9f9;">
                        <h2 style="color: #2c5aa0;">Grievance Updated: Ask HR</h2>
                        <p>Dear {employee_name},</p>
                        <p>Your grievance has been updated with the following details:</p>
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

                # Notify HR
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
                        hr_subject = f"Grievance Updated (ID: {grievance_id})"
                        hr_body = f"""
                        <html>
                        <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
                            <div style="max-width: 600px; margin: 0 auto; padding: 20px; background: #f9f9f9;">
                                <h2 style="color: #2c5aa0;">Grievance Updated: Ask HR</h2>
                                <p>Dear {hr_name},</p>
                                <p>The following grievance assigned to you has been updated by the employee:</p>
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
                    # Notify new HR only
                    c.execute('''
                        SELECT u.employee_email, u.employee_name, u.employee_phone
                        FROM hr_grievance_mapping m
                        JOIN users u ON m.hr_emp_code = u.emp_code
                        WHERE m.grievance_type = %s
                    ''', (grievance_type,))
                    new_hr_info = c.fetchone()
                    if new_hr_info:
                        new_hr_email, new_hr_name, new_hr_phone = new_hr_info
                        hr_subject = f"New Grievance Assigned (ID: {grievance_id})"
                        hr_body = f"""
                        <html>
                        <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
                            <div style="max-width: 600px; margin: 0 auto; padding: 20px; background: #f9f9f9;">
                                <h2 style="color: #2c5aa0;">New Grievance Assigned: Ask HR</h2>
                                <p>Dear {new_hr_name},</p>
                                <p>A grievance has been updated and is now assigned to you:</p>
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

                flash('Grievance updated successfully.', 'success')
                return redirect(url_for('my_grievances'))
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
            c.execute('SELECT status FROM grievances WHERE id = %s AND emp_code = %s', (grievance_id, user['emp_code']))
            row = c.fetchone()
            if not row:
                flash('Grievance not found.', 'error')
                return redirect(url_for('my_grievances'))
            if row[0] != 'Submitted':
                flash('You can only delete grievances that are still submitted.', 'error')
                return redirect(url_for('my_grievances'))
            c.execute('DELETE FROM grievances WHERE id = %s AND emp_code = %s', (grievance_id, user['emp_code']))
            conn.commit()
            flash('Grievance deleted successfully.', 'success')
    finally:
        db_pool.putconn(conn)
    return redirect(url_for('my_grievances'))

@app.route('/delete-grievance', methods=['POST'])
def delete_grievance():
    user = session.get('user')
    if not user or not user.get('authenticated') or user.get('role') != 'admin':
        flash('You do not have permission to delete grievances', 'error')
        return redirect(url_for('login'))

    grievance_id = request.form.get('grievance_id')
    reason = request.form.get('reason', '').strip()

    if not grievance_id or not reason:
        flash('Reason for deletion is required.', 'error')
        return redirect(url_for('master_dashboard'))

    conn = db_pool.getconn()
    try:
        with conn.cursor() as c:
            # Fetch grievance details before deletion
            c.execute('SELECT employee_name, employee_email, employee_phone, subject FROM grievances WHERE id = %s', (grievance_id,))
            gr = c.fetchone()
            if not gr:
                flash('Grievance not found.', 'error')
                return redirect(url_for('master_dashboard'))
            employee_name, employee_email, employee_phone, subject = gr

            # Delete related records
            c.execute('DELETE FROM feedback WHERE grievance_id = %s', (grievance_id,))
            c.execute('DELETE FROM responses WHERE grievance_id = %s', (grievance_id,))
            c.execute('DELETE FROM reminder_sent WHERE grievance_id = %s', (grievance_id,))
            c.execute('DELETE FROM grievances WHERE id = %s', (grievance_id,))
            conn.commit()

            # Send email notification
            email_subject = f"Your Grievance Request Deleted (ID: {grievance_id})"
            email_body = f"""
            <html>
            <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
                <div style="max-width: 600px; margin: 0 auto; padding: 20px; background: #f9f9f9;">
                    <h2 style="color: #e74c3c;">Grievance Request Deleted</h2>
                    <p>Dear {employee_name},</p>
                    <p>Your grievance request with the following details has been <b>deleted</b> by the admin:</p>
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

            # Send WhatsApp notification (template must be created in WhatsApp Manager)
            if employee_phone:
                send_whatsapp_template(
                    to_phone=employee_phone,
                    template_name="grievance_deleted_notification",  # <-- Create this template in WhatsApp Manager
                    lang_code="en",
                    parameters=[
                        employee_name,
                        grievance_id,
                        subject,
                        reason
                    ]
                )

            flash('Grievance deleted successfully and user notified.', 'success')
    except Exception as e:
        conn.rollback()
        flash(f'Error deleting grievance: {str(e)}', 'error')
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

                if not grievance_type or not hr_emp_code:
                    flash('Please select both grievance type and HR personnel.', 'error')
                    return redirect(url_for('manage_hr_mappings'))

                c.execute('''
                    INSERT INTO hr_grievance_mapping (grievance_type, hr_emp_code)
                    VALUES (%s, %s)
                    ON CONFLICT (grievance_type) DO UPDATE
                    SET hr_emp_code = EXCLUDED.hr_emp_code
                ''', (grievance_type, hr_emp_code))

                conn.commit()
                flash('HR mapping updated successfully', 'success')
                return redirect(url_for('manage_hr_mappings'))

            c.execute('''
                SELECT emp_code, employee_name FROM users
                WHERE role = 'hr'
                ORDER BY employee_name
            ''')
            hr_staff = c.fetchall()

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

            return render_template('manage_mappings.html',
                                 mappings=mappings,
                                 grievance_types=GRIEVANCE_TYPES,
                                 hr_staff=hr_staff)
    finally:
        db_pool.putconn(conn)

@app.route('/reassign-grievance', methods=['POST'])
def reassign_grievance():
    user = session.get('user')
    if not user or not user.get('authenticated') or user.get('role') != 'admin':
        flash('You do not have permission to reassign grievances', 'error')
        return redirect(url_for('login'))

    grievance_id = request.form.get('grievance_id')
    new_hr_emp_code = request.form.get('new_hr')
    reason = request.form.get('reason')
    current_type = request.form.get('current_type')

    if not all([grievance_id, new_hr_emp_code, reason]):
        flash('Missing required information for reassignment', 'error')
        return redirect(url_for('master_dashboard'))

    conn = db_pool.getconn()
    try:
        with conn.cursor() as c:
            # Get grievance details
            c.execute('''
                SELECT g.id, g.employee_name, g.grievance_type, g.subject, u.employee_name, u.employee_email
                FROM grievances g
                LEFT JOIN hr_grievance_mapping m ON g.grievance_type = m.grievance_type
                LEFT JOIN users u ON m.hr_emp_code = u.emp_code
                WHERE g.id = %s
            ''', (grievance_id,))

            grievance = c.fetchone()
            if not grievance:
                flash('Grievance not found', 'error')
                return redirect(url_for('master_dashboard'))

            # Get new HR staff details
            c.execute('SELECT employee_name, employee_email, employee_phone FROM users WHERE emp_code = %s', (new_hr_emp_code,))
            new_hr = c.fetchone()
            if not new_hr:
                flash('Selected HR staff not found', 'error')
                return redirect(url_for('master_dashboard'))

            # Create a temporary reassignment entry
            c.execute('''
                INSERT INTO hr_grievance_mapping (grievance_type, hr_emp_code)
                VALUES (%s, %s)
                ON CONFLICT (grievance_type) DO UPDATE
                SET hr_emp_code = EXCLUDED.hr_emp_code
            ''', (f"temp_{grievance_id}", new_hr_emp_code))

            # Update the grievance to use the temporary mapping
            c.execute('''
                UPDATE grievances
                SET grievance_type = %s
                WHERE id = %s
            ''', (f"temp_{grievance_id}", grievance_id))

            # Log the reassignment
            c.execute('''
                INSERT INTO responses
                (grievance_id, responder_email, responder_name, response_text, response_date)
                VALUES (%s, %s, %s, %s, %s)''',
                (grievance_id, user.get('employee_email'), user.get('employee_name'),
                f"Grievance reassigned to {new_hr[0]} by admin. Reason: {reason}", datetime.now()))

            conn.commit()

            # Send notifications
            notify_subject = f"Grievance Reassigned to You - {grievance[3]} (ID: {grievance_id})"
            notify_body = f"""
            <html>
            <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
                <div style="max-width: 600px; margin: 0 auto; padding: 20px; background: #f9f9f9;">
                    <h2 style="color: #9b59b6; border-bottom: 2px solid #9b59b6; padding-bottom: 10px;">
                        Grievance Reassigned to You (ID: {grievance_id})
                    </h2>
                    <p>Dear {new_hr[0]},</p>
                    <p>A grievance has been <b>reassigned</b> to you by {user.get('employee_name')}:</p>
                    <div style="background: white; padding: 20px; border-radius: 8px;">
                        <p><strong>Grievance ID:</strong> {grievance_id}</p>
                        <p><strong>Employee:</strong> {grievance[1]}</p>
                        <p><strong>Subject:</strong> {grievance[3]}</p>
                        <p><strong>Reason for reassignment:</strong> {reason}</p>
                    </div>
                    <p>Please review and respond as soon as possible.</p>
                    <p><a href="{url_for('respond_grievance', grievance_id=grievance_id, _external=True)}"
                        style="background: #3498db; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">
                        Respond Now</a>
                    </p>
                    <p><em>ASK HR Management System</em></p>
                </div>
            </body>
            </html>
            """

            # Send notification to new HR
            send_email_flask_mail(new_hr[1], notify_subject, notify_body)

            # Send WhatsApp notification if available
            if new_hr[2]:
                send_whatsapp_template(
                    to_phone=new_hr[2],  # HR phone
                    template_name="grievance_reassigned_hr",
                    lang_code="en",
                    parameters=[
                        new_hr[0],         # HR Name
                        grievance_id,      # Reference ID
                        grievance[3],      # Subject
                        datetime.now().strftime('%d-%m-%Y, %H:%M:%S')  # Submission Date
                    ]
                )
            # Notify previous HR if available
            if grievance[5]:  # If previous HR email exists
                prev_notify_subject = f"Grievance Reassigned - {grievance[3]} (ID: {grievance_id})"
                prev_notify_body = f"""
                <html>
                <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
                    <div style="max-width: 600px; margin: 0 auto; padding: 20px; background: #f9f9f9;">
                        <h2 style="color: #9b59b6; border-bottom: 2px solid #9b59b6; padding-bottom: 10px;">
                            Grievance Reassigned (ID: {grievance_id})
                        </h2>
                        <p>Dear {grievance[4]},</p>
                        <p>A grievance previously assigned to you has been <b>reassigned</b> to {new_hr[0]} by {user.get('employee_name')}:</p>
                        <div style="background: white; padding: 20px; border-radius: 8px;">
                            <p><strong>Grievance ID:</strong> {grievance_id}</p>
                            <p><strong>Employee:</strong> {grievance[1]}</p>
                            <p><strong>Subject:</strong> {grievance[3]}</p>
                            <p><strong>Reason for reassignment:</strong> {reason}</p>
                        </div>
                        <p><em>ASK HR Management System</em></p>
                    </div>
                </body>
                </html>
                """
                send_email_flask_mail(grievance[5], prev_notify_subject, prev_notify_body)

            flash(f'Grievance successfully reassigned to {new_hr[0]}', 'success')
            return redirect(url_for('master_dashboard'))

    except Exception as e:
        conn.rollback()
        print(f"Error in reassigning grievance: {str(e)}")
        print(traceback.format_exc())
        flash(f'Error reassigning grievance: {str(e)}', 'error')
        return redirect(url_for('master_dashboard'))
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
<body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
    <div style="max-width: 600px; margin: 0 auto; padding: 20px; background: #f9f9f9;">
        <h2 style="color: #2c5aa0;">OTP Verification: Ask HR</h2>
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

    # Extract the milliseconds from the string
    match = re.search(r'/Date\((\d+)\)/', date_string)
    if not match:
        return None

    # Convert milliseconds to a datetime object
    milliseconds = int(match.group(1))
    seconds = milliseconds / 1000  # Convert to seconds
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
        url = f"https://api44.sapsf.com/odata/v2/EmpJob?$select=division,divisionNav/name,location,locationNav/name,userId,employmentNav/personNav/personalInfoNav/firstName,employmentNav/personNav/personalInfoNav/middleName,employmentNav/personNav/personalInfoNav/lastName,department,departmentNav/name,employmentNav/personNav/emailNav/emailAddress,employmentNav/personNav/phoneNav/phoneNumber,employmentNav/personNav/dateOfBirth&$expand=employmentNav/personNav/personalInfoNav,divisionNav,locationNav,departmentNav,employmentNav/personNav/phoneNav,employmentNav/personNav/emailNav&$filter=userId eq '{emp_code}'&$format=json"

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

        print(f"🔍 PHONE EXTRACTION DEBUG:")

        phone_nav = safe_get(result, 'employmentNav', 'personNav', 'phoneNav')
        print(f"  Phone nav object type: {type(phone_nav)}")
        print(f"  Raw phone data: {phone_nav}")

        phone_number = ""

        if isinstance(phone_nav, dict):
            if 'phoneNumber' in phone_nav:
                phone_number = phone_nav['phoneNumber']
                print(f"  ✅ Found phone in phoneNav dictionary: {phone_number}")

            elif 'results' in phone_nav and phone_nav['results']:
                results = phone_nav['results']
                if results and isinstance(results[0], dict) and 'phoneNumber' in results[0]:
                    phone_number = results[0]['phoneNumber']
                    print(f"  ✅ Found phone in results[0]: {phone_number}")

        elif isinstance(phone_nav, list) and phone_nav:
            if isinstance(phone_nav[0], dict) and 'phoneNumber' in phone_nav[0]:
                phone_number = phone_nav[0]['phoneNumber']
                print(f"  ✅ Found phone in phoneNav list: {phone_number}")

        if not phone_number:
            direct_phone = safe_get(result, 'employmentNav', 'personNav', 'phoneNav', 'phoneNumber')
            if direct_phone:
                phone_number = direct_phone
                print(f"  ✅ Found phone via direct path: {phone_number}")

        if not phone_number:
            custom_fields = [
                ('employmentNav', 'personNav', 'personalInfoNav', 'results', 0, 'customString5'),
                ('customString6'),
                ('employmentNav', 'customString18')
            ]

            for field_path in custom_fields:
                if isinstance(field_path, tuple):
                    potential_phone = safe_get(result, *field_path)
                else:
                    potential_phone = safe_get(result, field_path)

                if potential_phone and isinstance(potential_phone, str) and len(potential_phone) >= 10:
                    digits_only = ''.join(filter(str.isdigit, potential_phone))
                    if len(digits_only) >= 10:
                        phone_number = digits_only
                        print(f"  ✅ Found potential phone in custom field: {phone_number}")
                        break

        if phone_number:
            phone_number = ''.join(filter(str.isdigit, phone_number))

            if phone_number and len(phone_number) >= 10:
                if not phone_number.startswith('+'):
                    if phone_number.startswith('91'):
                        phone_number = '+' + phone_number
                    else:
                        phone_number = '+91' + phone_number
            else:
                print("  ⚠️ Phone number format invalid, using empty value")
                phone_number = ""
        else:
            print("  ⚠️ No phone number found in API response")

        print(f"  📱 Final phone number: {phone_number}")

        full_name = f"{first_name} {middle_name} {last_name}".replace('  ', ' ').strip()

        email = safe_get(result, 'employmentNav', 'personNav', 'emailNav', 'emailAddress')
        if not email:
            email = f"{first_name.lower()}.{last_name.lower()}@nvtpower.com" if first_name and last_name else ''

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


if __name__ == '__main__':
    init_db()
    def run_check_with_app_context():
        with app.app_context():
            check_pending_grievances()

    scheduler = BackgroundScheduler()
    scheduler.add_job(
        func=run_check_with_app_context,
        trigger=IntervalTrigger(minutes=2),
        id='check_pending_grievances',
        name='Check for grievances pending for over 48 hours',
        replace_existing=True
    )
    scheduler.start()
    print("📅 Scheduler started - will check for pending grievances every 2 minutes")
    app.run(debug=True)
