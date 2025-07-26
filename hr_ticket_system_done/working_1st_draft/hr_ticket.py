from flask import Flask, render_template, render_template_string, request, redirect, url_for, flash
from flask_mail import Mail, Message
from werkzeug.utils import secure_filename
import os
import psycopg2
from psycopg2 import pool
from datetime import datetime, timedelta
import uuid
import traceback2 as traceback
from dotenv import load_dotenv
from twilio.rest import Client
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
import time 

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY')

SERVER_HOST = os.environ.get('SERVER_HOST', 'http://127.0.0.1:5000')

app.config['SERVER_NAME'] = '127.0.0.1:5000'
app.config['PREFERRED_URL_SCHEME'] = 'http'
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
        
        print(f"\n📤 SENDING EMAIL...")
        mail.send(msg)
        print(f"✅ Email sent successfully using Flask-Mail!")
        print(f"\n🎉 EMAIL SENDING COMPLETED SUCCESSFULLY!")
        print("="*60)
        return True
        
    except Exception as e:
        print(f"\n❌ EMAIL SENDING ERROR:")
        print(f"   Error type: {type(e).__name__}")
        print(f"   Error message: {str(e)}")
        import traceback
        print(f"\n🔍 FULL TRACEBACK:")
        print(f"   {traceback.format_exc()}")
        print("="*60)
        return False

def send_whatsapp_message(to_phone, message):
    print("\n" + "="*60)
    print("📱 WHATSAPP MESSAGE SENDING STARTED")
    print("="*60)
    
    try:
        cleaned_phone = ''.join(filter(str.isdigit, to_phone))
        
        if not cleaned_phone.startswith('+'):
            if cleaned_phone.startswith('91'):
                cleaned_phone = '+' + cleaned_phone
            else:
                cleaned_phone = '+91' + cleaned_phone
                
        whatsapp_to = f"whatsapp:{cleaned_phone}"
        
        print(f"🔧 CONFIGURATION:")
        print(f"   To WhatsApp: {whatsapp_to}")
        print(f"   Message Length: {len(message)} characters")
        
        client = Client(
            os.environ.get('TWILIO_ACCOUNT_SID'), 
            os.environ.get('TWILIO_AUTH_TOKEN')
        )
        
        print(f"\n📤 SENDING WHATSAPP MESSAGE...")
        message = client.messages.create(
            body=message,
            from_=os.environ.get('TWILIO_WHATSAPP_FROM'),
            to=whatsapp_to
        )
        
        print(f"✅ WhatsApp message sent! SID: {message.sid}")
        print(f"\n🎉 WHATSAPP SENDING COMPLETED SUCCESSFULLY!")
        print("="*60)
        return True
        
    except Exception as e:
        print(f"\n❌ WHATSAPP SENDING ERROR:")
        print(f"   Error type: {type(e).__name__}")
        print(f"   Error message: {str(e)}")
        print(traceback.format_exc())
        print("="*60)
        return False

@app.route('/run-check')
def run_check():
    with app.app_context():
        check_pending_grievances()
    return "Check completed! See console for details."

@app.route('/')
def index():
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
                            (id, emp_code, employee_name, employee_email, employee_phone, 
                            business_unit, department, grievance_type, subject, description, 
                            attachment_path, submission_date) 
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)''',
                         (grievance_id, emp_code, employee_name, employee_email, employee_phone,
                          business_unit, department, grievance_type, subject, description,
                          attachment_path, datetime.now()))
                conn.commit()
            print(f"✅ Data saved to database")
        finally:
            db_pool.putconn(conn)
        
        print(f"\n📧 PREPARING EMAIL NOTIFICATION...")
        email_subject = f"New Grievance Submitted - {subject} (ID: {grievance_id})"
        email_body = f"""
        <html>
        <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
            <div style="max-width: 600px; margin: 0 auto; padding: 20px; background: #f9f9f9;">
                <h2 style="color: #2c5aa0; border-bottom: 2px solid #2c5aa0; padding-bottom: 10px;">
                    New Grievance Submission
                </h2>
                <div style="background: white; padding: 20px; border-radius: 8px; margin-top: 20px;">
                    <p><strong>Grievance ID:</strong> {grievance_id}</p>
                    <p><strong>Employee Code:</strong> {emp_code}</p>
                    <p><strong>Employee Name:</strong> {employee_name}</p>
                    <p><strong>Employee Email:</strong> {employee_email}</p>
                    <p><strong>Employee Phone:</strong> {employee_phone or 'Not provided'}</p>
                    <p><strong>Business Unit:</strong> {business_unit or 'Not provided'}</p>
                    <p><strong>Department:</strong> {department or 'Not provided'}</p>
                    <p><strong>Grievance Type:</strong> {GRIEVANCE_TYPES[grievance_type]}</p>
                    <p><strong>Subject:</strong> {subject}</p>
                    <div style="margin-top: 15px;">
                        <strong>Description:</strong>
                        <div style="background: #f5f5f5; padding: 10px; border-left: 4px solid #2c5aa0; margin-top: 5px;">
                            {description.replace(chr(10), '<br>')}
                        </div>
                    </div>
                    <p><strong>Submission Date:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
                    {f'<p><strong>Attachment:</strong> {os.path.basename(attachment_path)}</p>' if attachment_path else ''}
                </div>
                <p style="margin-top: 20px; font-size: 12px; color: #666;">
                    <em>This is an automated message from the Grievance Management System.</em>
                </p>
            </div>
        </body>
        </html>
        """
        
        print(f"✅ Email content prepared")
        email_success = send_email_flask_mail('romil.agarwal@nvtpower.com', email_subject, email_body, attachment_path)
        
        if email_success:
            print(f"✅ EMAIL SENT SUCCESSFULLY!")
            employee_email_body = f"""
            <html>
            <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
                <div style="max-width: 600px; margin: 0 auto; padding: 20px; background: #f9f9f9;">
                    <h2 style="color: #2c5aa0;">Grievance Submission Confirmation</h2>
                    <p>Dear {employee_name},</p>
                    <p>Your grievance has been successfully submitted with the following details:</p>
                    <div style="background: white; padding: 20px; border-radius: 8px;">
                        <p><strong>Reference ID:</strong> {grievance_id}</p>
                        <p><strong>Subject:</strong> {subject}</p>
                        <p><strong>Status:</strong> Submitted</p>
                        <p><strong>Submission Date:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
                    </div>
                    <p>Please keep the Reference ID for tracking your grievance status.</p>
                    <p><em>Grievance Management System</em></p>
                </div>
            </body>
            </html>
            """
            send_email_flask_mail(employee_email, f"Grievance Submission Confirmation (ID: {grievance_id})", employee_email_body)
            
            if employee_phone:
                whatsapp_message = f"""🎫 *Grievance Submission Confirmation*
                
Dear {employee_name},

Your grievance has been submitted successfully!

📝 *Reference ID:* {grievance_id}
📋 *Subject:* {subject}
📊 *Status:* Submitted
⏱️ *Date:* {datetime.now().strftime('%Y-%m-%d %H:%M')}

Please keep this ID for tracking your grievance.

Thank you,
Grievance Management System"""
                
                send_whatsapp_message(employee_phone, whatsapp_message)
            
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

@app.route('/respond/<grievance_id>', methods=['GET', 'POST'])
def respond_grievance(grievance_id):
    conn = db_pool.getconn()
    try:
        with conn.cursor() as c:
            c.execute('SELECT * FROM grievances WHERE id = %s', (grievance_id,))
            grievance = c.fetchone()
            if not grievance:
                flash('Grievance not found.', 'error')
                return redirect(url_for('dashboard'))
            
            if request.method == 'POST':
                responder_email = request.form.get('responder_email')
                responder_name = request.form.get('responder_name')
                response_text = request.form.get('response_text')
                new_status = request.form.get('status')
                
                if not all([responder_email, response_text, new_status]):
                    flash('Please fill in all required fields.', 'error')
                    return render_template('response.html', grievance=grievance, grievance_types=GRIEVANCE_TYPES)

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
                        <h2 style="color: #2c5aa0;">Grievance Response (ID: {grievance_id})</h2>
                        <p>Dear {employee_name},</p>
                        <p>We have a response to your grievance:</p>
                        <div style="background: white; padding: 20px; border-radius: 8px;">
                            <p><strong>Grievance ID:</strong> {grievance_id}</p>
                            <p><strong>Subject:</strong> {grievance[8]}</p>
                            <p><strong>Status:</strong> {new_status}</p>
                            <p><strong>Responder:</strong> {responder_name or responder_email}</p>
                            <p><strong>Response:</strong></p>
                            <div style="background: #f5f5f5; padding: 10px; border-left: 4px solid #2c5aa0;">
                                {response_text.replace(chr(10), '<br>')}
                            </div>
                            <p><strong>Response Date:</strong> {response_date.strftime('%Y-%m-%d %H:%M:%S')}</p>
                            {f'<p><strong>Attachment:</strong> {os.path.basename(response_attachment_path)}</p>' if response_attachment_path else ''}
                        </div>
                        <p>Please provide your feedback on this resolution:</p>
                        <p>
                            <a href="{url_for('feedback', grievance_id=grievance_id, response='satisfied', _external=True)}" style="background: #27ae60; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; margin-right: 10px;">Satisfied</a>
                            <a href="{url_for('feedback', grievance_id=grievance_id, response='not_satisfied', _external=True)}" style="background: #e74c3c; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">Not Satisfied</a>
                        </p>
                        <p><em>Grievance Management System</em></p>
                    </div>
                </body>
                </html>
                """
                send_email_flask_mail(employee_email, f"Grievance Response (ID: {grievance_id})", response_email_body, response_attachment_path)
                
                employee_phone = grievance[4]  
                if employee_phone:
                    whatsapp_message = f"""📣 *Grievance Response Update*
        
Dear {employee_name},

We have a response for your grievance!

📝 *Grievance ID:* {grievance_id}
📋 *Subject:* {grievance[8]}
📊 *Status:* {new_status}
👤 *Responder:* {responder_name or responder_email}

*Response:*
{response_text}

Please provide your feedback by clicking here:
{url_for('feedback', grievance_id=grievance_id, response='', _external=True)}

Thank you,
Grievance Management System"""
        
                    send_whatsapp_message(employee_phone, whatsapp_message)
            
                flash('Response submitted successfully.', 'success')
                return redirect(url_for('dashboard'))

        return render_template('response.html', grievance=grievance, grievance_types=GRIEVANCE_TYPES)
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
    conn = db_pool.getconn()
    try:
        with conn.cursor() as c:
            satisfaction = request.form.get('satisfaction')
            rating = request.form.get('rating')
            feedback_comments = request.form.get('feedback_comments')
            
            if not all([satisfaction, rating]):
                flash('Please provide both satisfaction status and rating.', 'error')
                return redirect(url_for('feedback', grievance_id=grievance_id, response=satisfaction))
            
            feedback_date = datetime.now()
            c.execute('''INSERT INTO feedback 
                        (grievance_id, satisfaction, rating, feedback_comments, feedback_date) 
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (grievance_id) DO UPDATE 
                        SET satisfaction = EXCLUDED.satisfaction,
                            rating = EXCLUDED.rating,
                            feedback_comments = EXCLUDED.feedback_comments,
                            feedback_date = EXCLUDED.feedback_date''',
                     (grievance_id, satisfaction, int(rating), feedback_comments, feedback_date))
            
            if satisfaction == 'not_satisfied':
                c.execute('UPDATE grievances SET status = %s, updated_at = %s WHERE id = %s',
                         ('Reopened', datetime.now(), grievance_id))
            
            conn.commit()
            flash('Feedback submitted successfully.', 'success')
            return redirect(url_for('dashboard'))
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
            c.execute('''
                SELECT g.id, g.employee_name, g.employee_email, g.subject, g.submission_date 
                FROM grievances g
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
                hr_email = 'Romil.Agarwal@nvtpower.com'  
                subject = grievance[3]
                submission_date = grievance[4]
                
                hours_pending = (datetime.now() - submission_date).total_seconds() / 3600
                
                print(f"📝 Processing grievance {grievance_id}: {subject}")
                print(f"   Submitted: {submission_date}, Hours pending: {hours_pending:.1f}")
                
                full_url = f"{SERVER_HOST}/respond/{grievance_id}"
                if not full_url.startswith(('http://', 'https://')):
                    full_url = f"http://{full_url}"
                
                email_subject = f"⚠️ URGENT: Unresolved Grievance (ID: {grievance_id})"
                email_body = f"""
                <html>
                <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
                    <div style="max-width: 600px; margin: 0 auto; padding: 20px; background: #f9f9f9;">
                        <h2 style="color: #e74c3c; border-bottom: 2px solid #e74c3c; padding-bottom: 10px;">
                            ⚠️ Urgent: Grievance Pending for over {int(hours_pending)} hours
                        </h2>
                        <p>This is an automated reminder that the following grievance has been pending without resolution:</p>
                        <div style="background: white; padding: 20px; border-radius: 8px; margin-top: 20px;">
                            <p><strong>Grievance ID:</strong> {grievance_id}</p>
                            <p><strong>Employee Name:</strong> {employee_name}</p>
                            <p><strong>Subject:</strong> {subject}</p>
                            <p><strong>Submission Date:</strong> {submission_date.strftime('%Y-%m-%d %H:%M:%S')}</p>
                            <p><strong>Status:</strong> Submitted</p>
                        </div>
                        <p style="margin-top: 20px;">Please review and respond to this grievance as soon as possible.</p>
                        <p><a href="{full_url}" 
                            style="background: #3498db; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">
                            Respond Now</a>
                        </p>
                        <p style="margin-top: 20px; font-size: 12px; color: #666;">
                            <em>This is an automated message from the Grievance Management System.</em>
                        </p>
                    </div>
                </body>
                </html>
                """
                
                email_sent = send_email_flask_mail(hr_email, email_subject, email_body)
                
                if email_sent:
                    hr_phone = "8318436133" 
                    whatsapp_message = f"""⚠️ *URGENT: Unresolved Grievance*

Grievance ID: {grievance_id} has been pending for *{int(hours_pending)} hours* without resolution.

*Details:*
- Employee: {employee_name}
- Subject: {subject}
- Submitted: {submission_date.strftime('%Y-%m-%d')}

Please review and respond to this grievance as soon as possible.

{full_url}

Grievance Management System
"""
                    send_whatsapp_message(hr_phone, whatsapp_message)
                    
                    c.execute('INSERT INTO reminder_sent (grievance_id, reminder_date) VALUES (%s, %s)',
                              (grievance_id, datetime.now()))
                    conn.commit()
                    print(f"✅ Reminder sent for grievance {grievance_id}")
                else:
                    print(f"❌ Failed to send reminder for grievance {grievance_id}")
                    
    except Exception as e:
        print(f"❌ Error checking pending grievances: {str(e)}")
        print(traceback.format_exc())
    finally:
        db_pool.putconn(conn)
        print("="*60)

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