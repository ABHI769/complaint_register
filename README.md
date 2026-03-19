# Complaint Portal (Python + HTML)

A simple website where:

- Users submit a complaint (name, phone, area, subject, description)
- The receiver logs in and views all complaints, and can mark them resolved

## Run (Windows PowerShell)

From this folder:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

Then open:

- Submit complaint: `http://127.0.0.1:5000/`
- Receiver login: `http://127.0.0.1:5000/receiver/login`

## Receiver password

Default receiver password is: `admin123`

To change it:

```powershell
$env:RECEIVER_PASSWORD="myStrongPassword"
python app.py
```

## Data storage

Complaints are stored in a local SQLite database file: `complaints.db`
