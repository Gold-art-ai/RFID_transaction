from flask import Flask, render_template, request, jsonify, Response
from flask_socketio import SocketIO
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
import paho.mqtt.client as mqtt
import json
from datetime import datetime
import os

app = Flask(__name__, template_folder='templates')
app.config['SECRET_KEY'] = 'cyber_secret_key_99'
# SQLite Database Configuration
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///nexus.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# --- DATABASE MODELS ---
class UserCard(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    uid = db.Column(db.String(50), unique=True, nullable=False)
    balance = db.Column(db.Integer, default=0)
    last_seen = db.Column(db.DateTime, default=datetime.utcnow)

class Transaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    uid = db.Column(db.String(50), nullable=False)
    amount = db.Column(db.Integer, nullable=False)
    type = db.Column(db.String(20)) # 'TOPUP' or 'PAYMENT'
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

# Create the database and tables
with app.app_context():
    db.create_all()

# --- CONFIGURATION ---
TEAM_ID = "team_pixel"
MQTT_BROKER = "broker.benax.rw"
TOPIC_STATUS = f"rfid/{TEAM_ID}/card/status"
TOPIC_PAY = f"rfid/{TEAM_ID}/card/pay"
TOPIC_TOPUP = f"rfid/{TEAM_ID}/card/topup"

# --- MQTT LOGIC ---
def on_connect(client, userdata, flags, rc):
    print(f"[*] MQTT Connected to: {MQTT_BROKER}")
    client.subscribe(TOPIC_STATUS)

def on_message(client, userdata, msg):
    with app.app_context():
        try:
            payload = json.loads(msg.payload.decode())
            uid = str(payload.get('uid')).upper().strip()
            
            if uid:
                # implement "Safe Wallet Update" - Check if card exists, if not, create it
                card = UserCard.query.filter_by(uid=uid).first()
                if not card:
                    card = UserCard(uid=uid, balance=0)
                    db.session.add(card)
                
                card.last_seen = datetime.utcnow()
                db.session.commit()
                
                socketio.emit('update_ui', {
                    "uid": uid,
                    "balance": card.balance,
                    "type": "SCAN",
                    "time": datetime.now().strftime("%H:%M:%S")
                })
        except Exception as e:
            print(f"[!] MQTT Error: {e}")

mqtt_client = mqtt.Client()
mqtt_client.on_connect = on_connect
mqtt_client.on_message = on_message
mqtt_client.connect(MQTT_BROKER, 1883, 60)
mqtt_client.loop_start()

# --- ROUTES ---

@app.route('/')
def index():
    return render_template('login.html')

@app.route('/agent_dashboard')
def agent_dashboard():
    return render_template('agent_dashboard.html')

@app.route('/sales_dashboard')
def sales_dashboard():
    return render_template('sales_dashboard.html')

@app.route('/consolidated_dashboard')
def consolidated_dashboard():
    return render_template('consolidated_dashboard.html')

@app.route('/pay', methods=['POST'])
def pay():
    data = request.json
    uid = str(data.get('uid')).upper().strip()
    amount = int(data.get('amount', 0))

    card = UserCard.query.filter_by(uid=uid).first()
    if not card:
        return jsonify({"error": "Card not registered"}), 404

    # Safe Wallet Update Logic
    if card.balance >= amount:
        card.balance -= amount
        
        # Log Transaction
        txn = Transaction(uid=uid, amount=amount, type="PAYMENT")
        db.session.add(txn)
        db.session.commit()
        
        # Update ESP8266 & Web UI
        mqtt_client.publish(TOPIC_PAY, json.dumps({"uid": uid, "new_balance": card.balance}))
        
        res_data = {"uid": uid, "balance": card.balance, "amount": amount, "type": "PAYMENT", "time": datetime.now().strftime("%H:%M:%S")}
        socketio.emit('update_ui', res_data)
        
        return jsonify({"status": "success", "new_balance": card.balance, "transaction_id": txn.id}), 200
    
    return jsonify({"error": "Insufficient Funds"}), 400

@app.route('/topup', methods=['POST'])
def topup():
    data = request.json
    uid = str(data.get('uid')).upper().strip()
    amount = int(data.get('amount', 0))

    if not uid or uid == "--- --- ---":
        return jsonify({"error": "Scan card first"}), 400

    card = UserCard.query.filter_by(uid=uid).first()
    if not card:
        card = UserCard(uid=uid, balance=0)
        db.session.add(card)
    
    card.balance += amount
    
    # Log Transaction
    txn = Transaction(uid=uid, amount=amount, type="TOP-UP")
    db.session.add(txn)
    db.session.commit()
    
    mqtt_client.publish(TOPIC_TOPUP, json.dumps({"uid": uid, "new_balance": card.balance}))
    
    res_data = {"uid": uid, "balance": card.balance, "amount": amount, "type": "TOP-UP", "time": datetime.now().strftime("%H:%M:%S")}
    socketio.emit('update_ui', res_data)
    
    return jsonify({"status": "success", "new_balance": card.balance}), 200

@app.route('/receipt/<int:transaction_id>')
def receipt(transaction_id):
    txn = Transaction.query.get_or_404(transaction_id)
    card = UserCard.query.filter_by(uid=txn.uid).first()

    receipt_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Receipt</title>
        <style>
            body {{ font-family: 'Courier New', Courier, monospace; margin: 20px; }}
            .receipt-container {{ width: 320px; margin: auto; border: 2px dashed #333; padding: 15px; background-color: #f9f9f9; }}
            h2 {{ text-align: center; margin-top: 0; }}
            .item {{ display: flex; justify-content: space-between; margin-bottom: 8px; }}
            .item span:first-child {{ font-weight: bold; }}
            hr {{ border: 1px dashed #333; }}
            .footer {{ text-align: center; margin-top: 15px; }}
        </style>
    </head>
    <body>
        <div class="receipt-container">
            <h2>Payment Receipt</h2>
            <div class="item"><span>Transaction ID:</span> <span>{txn.id}</span></div>
            <div class="item"><span>Card UID:</span> <span>{txn.uid}</span></div>
            <div class="item"><span>Date:</span> <span>{txn.timestamp.strftime('%Y-%m-%d')}</span></div>
            <div class="item"><span>Time:</span> <span>{txn.timestamp.strftime('%H:%M:%S')}</span></div>
            <hr>
            <div class="item"><span>Type:</span> <span>{txn.type}</span></div>
            <div class="item"><span>Amount Paid:</span> <span>{txn.amount} RWF</span></div>
            <hr>
            <div class="item"><span>New Balance:</span> <span>{card.balance if card else 'N/A'} RWF</span></div>
            <div class="footer">Thank you!</div>
        </div>
    </body>
    </html>
    """
    return Response(receipt_html)

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5001, debug=True, allow_unsafe_werkzeug=True)