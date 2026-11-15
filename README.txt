PARKIFY - COLLEGE DEMO
======================

1. Install Python 3.10+.
2. Open a terminal in this folder.
3. Create a virtual environment (recommended):
   python -m venv venv
4. Activate it on Windows:
   venv\Scripts\activate
5. Install packages:
   pip install -r requirements.txt
6. Run:
   python app.py
7. Open:
   http://127.0.0.1:5000

DEMO ACCOUNTS
Customer: customer@parkify.com / 1234
Owner:    owner@parkify.com / 1234
Admin:    admin@parkify.com / 1234

IMPORTANT DEMO RULE
Online booking reserves parking capacity only. No slot is assigned online.
The owner verifies the customer's QR at arrival. Parkify then finds a free
physical slot and assigns it dynamically (for example B17).

Payment is recorded as a DEMO payment; no real money is charged.
Cancellation records a 70% partial refund.
No-show after the arrival deadline gives no refund.
A confirmed booking can use one free 15-minute arrival extension before its deadline.
When capacity is full, users enter a FIFO waiting list; an available capacity
is offered to the next waiting user for 15 minutes.

If you want a fresh demo database, stop the app and delete database.db.
The app will recreate it with demo data on the next run.
