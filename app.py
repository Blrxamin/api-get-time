from flask import Flask, jsonify, request
from datetime import datetime, timedelta
import json
import os
import threading
import time
import requests
import httpx
from threading import Thread
from functools import wraps

app = Flask(__name__)

# ============ Configuration ============
CONFIG = {
    "JWT_GENERATOR_URL": "https://jwt-gen-api-v2.onrender.com/token",
    "REMOVE_API": "https://amin-api-remove-add-jwt-token.onrender.com/remove_friend",
    "ADD_API": "https://amin-api-remove-add-jwt-token.onrender.com/adding_friend",
    "ADMIN_KEY": "amin_belara",  # Change this in production!
    "STORAGE_FILE": 'uid_storage.json',
    "TOKEN_REFRESH_INTERVAL": 8 * 3600,  # 8 hours
    "CLEANUP_INTERVAL": 60  # 1 minute
}

# ============ JWT Token Management ============
class TokenManager:
    def __init__(self):
        self.token = None
        self.uid = "3935704624"
        self.password = "4DD9580BC3E3E64BBAA1455E624E02DF230BCD68D36E16CB451CC4EA734B3DF0"
    
    def get_jwt_token(self):
        try:
            url = f"{CONFIG['JWT_GENERATOR_URL']}?uid={self.uid}&password={self.password}"
            response = httpx.get(url, timeout=10)
            if response.status_code == 200:
                data = response.json()
                if data.get('status') == 'live':
                    self.token = data['token']
                    app.logger.info(f"[JWT] Token updated: {self.token[:15]}...")
                    return True
            app.logger.error(f"[JWT] Failed to get token: {response.text}")
        except Exception as e:
            app.logger.error(f"[JWT] Error: {str(e)}")
        return False
    
    def start_token_refresh(self):
        def refresh_loop():
            while True:
                self.get_jwt_token()
                time.sleep(CONFIG['TOKEN_REFRESH_INTERVAL'])
        
        Thread(target=refresh_loop, daemon=True).start()

token_manager = TokenManager()

# ============ UID Storage Management ============
class UIDStorage:
    def __init__(self):
        self.lock = threading.Lock()
        self.ensure_storage_file()
    
    def ensure_storage_file(self):
        if not os.path.exists(CONFIG['STORAGE_FILE']):
            with open(CONFIG['STORAGE_FILE'], 'w') as file:
                json.dump({}, file)
    
    def load_uids(self):
        self.ensure_storage_file()
        with self.lock:
            with open(CONFIG['STORAGE_FILE'], 'r') as file:
                return json.load(file)
    
    def save_uids(self, uids):
        self.ensure_storage_file()
        with self.lock:
            with open(CONFIG['STORAGE_FILE'], 'w') as file:
                json.dump(uids, file, default=str)
    
    def cleanup_expired(self):
        while True:
            uids = self.load_uids()
            current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            expired = [
                uid for uid, exp_time in uids.items() 
                if exp_time != 'permanent' and exp_time <= current_time
            ]
            
            for uid in expired:
                self.remove_uid(uid, external=True)
                app.logger.info(f"[CLEANUP] Removed expired UID: {uid}")
            
            time.sleep(CONFIG['CLEANUP_INTERVAL'])

    def start_cleanup(self):
        Thread(target=self.cleanup_expired, daemon=True).start()
    
    def remove_uid(self, uid, external=False):
        uids = self.load_uids()
        if uid in uids:
            del uids[uid]
            self.save_uids(uids)
            
            if external and token_manager.token:
                try:
                    url = f"{CONFIG['REMOVE_API']}?token={token_manager.token}&id={uid}&key={CONFIG['ADMIN_KEY']}"
                    requests.get(url, timeout=5)
                except Exception as e:
                    app.logger.error(f"[EXTERNAL] Remove error: {str(e)}")
            return True
        return False

storage = UIDStorage()

# ============ API Endpoints ============
def require_key(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        provided_key = request.args.get('key')
        if provided_key != CONFIG['ADMIN_KEY']:
            return jsonify({"error": "Invalid or missing API key"}), 403
        return func(*args, **kwargs)
    return wrapper

@app.route('/')
def home():
    return jsonify({
        "status": "running",
        "endpoints": {
            "add_uid": "/add_uid?uid=ID&time=VALUE&type=TYPE&key=API_KEY",
            "remove_uid": "/remove?uid=ID&key=API_KEY",
            "check_time": "/get_time/ID?key=API_KEY"
        },
        "documentation": "See README for full API docs"
    })

@app.route('/add_uid', methods=['GET'])
@require_key
def add_uid():
    uid = request.args.get('uid')
    time_value = request.args.get('time')
    time_unit = request.args.get('type')
    permanent = request.args.get('permanent', 'false').lower() == 'true'

    if not uid:
        return jsonify({'error': 'UID parameter is required'}), 400

    if permanent:
        expiration = 'permanent'
    else:
        if not time_value or not time_unit:
            return jsonify({'error': 'Time parameters required for temporary UIDs'}), 400
        
        try:
            time_value = int(time_value)
        except ValueError:
            return jsonify({'error': 'Time value must be integer'}), 400

        delta_map = {
            'seconds': timedelta(seconds=time_value),
            'minutes': timedelta(minutes=time_value),
            'hours': timedelta(hours=time_value),
            'days': timedelta(days=time_value),
            'months': timedelta(days=time_value*30),
            'years': timedelta(days=time_value*365)
        }
        
        if time_unit not in delta_map:
            return jsonify({'error': 'Invalid time unit'}), 400
        
        expiration = (datetime.now() + delta_map[time_unit]).strftime('%Y-%m-%d %H:%M:%S')

    # Add to external service
    if token_manager.token:
        try:
            url = f"{CONFIG['ADD_API']}?token={token_manager.token}&id={uid}&key={CONFIG['ADMIN_KEY']}"
            requests.get(url, timeout=5)
        except Exception as e:
            app.logger.error(f"[EXTERNAL] Add error: {str(e)}")

    # Save locally
    uids = storage.load_uids()
    uids[uid] = expiration
    storage.save_uids(uids)

    return jsonify({
        'uid': uid,
        'status': 'added',
        'expires_at': expiration if not permanent else 'never'
    })

@app.route('/remove', methods=['GET'])
@require_key
def remove_uid():
    uid = request.args.get('uid')
    if not uid:
        return jsonify({'error': 'UID parameter is required'}), 400

    success = storage.remove_uid(uid, external=True)
    
    if success:
        return jsonify({'status': 'removed', 'uid': uid})
    return jsonify({'error': 'UID not found'}), 404

@app.route('/get_time/<string:uid>', methods=['GET'])
@require_key
def check_time(uid):
    uids = storage.load_uids()
    
    if uid not in uids:
        return jsonify({'error': 'UID not found'}), 404
    
    expiration = uids[uid]
    
    if expiration == 'permanent':
        return jsonify({
            'uid': uid,
            'status': 'permanent',
            'message': 'This UID will never expire'
        })
    
    expiration_time = datetime.strptime(expiration, '%Y-%m-%d %H:%M:%S')
    remaining = expiration_time - datetime.now()
    
    if remaining.total_seconds() <= 0:
        return jsonify({'error': 'UID has expired'}), 400
    
    return jsonify({
        'uid': uid,
        'expires_at': expiration,
        'remaining': {
            'days': remaining.days,
            'hours': remaining.seconds // 3600,
            'minutes': (remaining.seconds % 3600) // 60,
            'seconds': remaining.seconds % 60
        }
    })

# ============ Initialization ============
if __name__ == '__main__':
    token_manager.start_token_refresh()
    storage.start_cleanup()
    
    app.run(host='0.0.0.0', port=5000, debug=False)
else:
    # For WSGI deployment
    token_manager.start_token_refresh()
    storage.start_cleanup()
