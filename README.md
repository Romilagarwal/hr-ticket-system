# HR Ticket Management System

![HR Ticket System](https://img.shields.io/badge/HR-Ticket%20System-blue)
![Version](https://img.shields.io/badge/version-1.0.0-green)
![License](https://img.shields.io/badge/license-MIT-orange)

A comprehensive HR Ticket Management System designed to streamline employee requests, grievances, and HR administrative tasks. This web-based application provides a centralized platform for employees to submit and track requests while enabling HR teams to efficiently manage, respond to, and report on organizational HR activities.

## 📋 Table of Contents

- [Features](#features)
- [Technology Stack](#technology-stack)
- [Installation](#installation)
- [Environment Configuration](#environment-configuration)
- [Usage](#usage)
- [Core Functionality](#core-functionality)
- [API Documentation](#api-documentation)
- [Contributing](#contributing)
- [License](#license)

## ✨ Features

### Employee Portal
- **Ticket Submission**: Create and submit HR requests across multiple categories
- **Grievance Filing**: Confidential channel for workplace grievances
- **Request Tracking**: Real-time status updates on submitted requests
- **Document Attachments**: Attach relevant files to tickets

### HR Admin Dashboard
- **Ticket Management**: Centralized view of all employee requests
- **Priority Assignment**: Categorize tickets by urgency and importance
- **Response System**: Direct communication with employees
- **Workflow Automation**: Automated ticket routing, notifications, and reminders
- **Performance Analytics**: Comprehensive reporting on ticket metrics

### Integration & Notifications
- **SAP Integration**: Employee data synchronization with SAP SuccessFactors
- **Multi-channel Notifications**: Email and WhatsApp notifications
- **Status Updates**: Automatic notifications on ticket status changes
- **Reminder System**: Escalation workflows for pending tickets

## 🛠️ Technology Stack

- **Backend**: Flask (Python)
- **Database**: PostgreSQL
- **Frontend**: HTML, CSS, JavaScript, Bootstrap
- **API Integrations**: SAP SuccessFactors, WhatsApp Business API
- **Authentication**: Session-based with role management
- **Notifications**: Email (SMTP), WhatsApp
- **Scheduling**: APScheduler for automated tasks
- **File Handling**: Werkzeug for secure file uploads

## 📥 Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/romilagarwal/hr-ticket-system.git
   cd hr_ticket_system
   ```

2. **Create a virtual environment**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Set up your environment variables** (see [Environment Configuration](#environment-configuration))

5. **Initialize the database**
   ```bash
   python init_db.py
   ```

6. **Run the application**
   ```bash
   flask run --host=0.0.0.0 --port=8112
   ```

## 🔧 Environment Configuration

Create a `.env` file in the root directory with the following variables (use `.env.example` as a template):

```env
# Server Configuration
SECRET_KEY=your_secret_key_here
SERVER_HOST=http://127.0.0.1:8112
SERVER_NAME=127.0.0.1:8112
PREFERRED_URL_SCHEME=http

# Database Configuration
DB_NAME=your_db_name
DB_USER=your_db_user
DB_PASSWORD=your_db_password
DB_HOST=localhost
DB_PORT=5432

# Email Configuration
MAIL_SERVER=your_mail_server
MAIL_PORT=25
MAIL_USERNAME=your_email_username
FROM_MAIL=MAIL_USERNAME
USE_TLS=False

# WhatsApp API Configuration
META_ACCESS_TOKEN=your_meta_token_here
WHATSAPP_PHONE_NUMBER_ID=your_phone_number_id

# SAP API Configuration
SAP_API_BASE_URL=https://api.example.com
SAP_API_USERNAME=your_api_username
SAP_API_PASSWORD=your_api_password
```

## 📋 Usage

### User Roles

- **Employee**: Submit and track HR requests and grievances
- **HR Admin**: Process tickets, respond to employees, run reports
- **HR Manager**: Oversee ticket workflows, handle escalations, generate analytics
- **System Admin**: Manage system settings, user roles, and configurations

### Core Workflows

1. **HR Ticket Lifecycle**
   - Submission → Assignment → Processing → Resolution → Feedback
   - Automatic notifications at each stage
   - SLA tracking and escalation for overdue tickets

2. **Grievance Handling**
   - Anonymous submission option
   - Confidential review process
   - Documentation of resolution steps

3. **Reporting and Analytics**
   - Ticket volume by department, category, and time period
   - Resolution time metrics
   - Employee satisfaction indicators

## 🔍 Core Functionality

### Ticket Management

The system handles various HR request types, including:
- Leave requests
- Document requests
- Policy questions
- Benefits inquiries
- IT access requests
- Training requests
- Travel requests
- Reimbursement claims

Each ticket follows a predefined workflow with automated status transitions, notifications, and SLA tracking.

### SAP Integration

Employee data synchronization with SAP SuccessFactors ensures:
- Up-to-date employee information
- Accurate department and division data
- Valid employee verification
- Organizational hierarchy for approvals

### Notification System

The application includes a robust notification engine:
- Email notifications for ticket updates
- WhatsApp messages for urgent communications
- Reminder system for pending tickets
- Escalation alerts for SLA violations

### Reporting Engine

Comprehensive analytics dashboard provides:
- Real-time ticket status overview
- Historical trend analysis
- Performance metrics by HR staff, department, and ticket type
- Custom report generation with export functionality

## 📚 API Documentation

### External Integrations

- SAP SuccessFactors API for employee data
- WhatsApp Business API for mobile notifications
- SMTP services for email communications

## 🤝 Contributing

Contributions are welcome! Please follow these steps:

1. Fork the repository
2. Create a new branch (`git checkout -b feature/your-feature`)
3. Make your changes
4. Run tests if available
5. Commit your changes (`git commit -am 'Add new feature'`)
6. Push to the branch (`git push origin feature/your-feature`)
7. Create a new Pull Request

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.

---

© 2025 HR Ticket Management System. All rights reserved.
