from flask import Flask, jsonify, request
from datetime import datetime, timedelta
import json
import os
import threading
import time
import requests
import httpx
from threading import Thread  

app = Flask(__name__)

####################################
key2 = "amin_belara"
jwt_token = None  

REMOVE_API = "https://amin-api-remove-add-jwt-token.onrender.com/remove_friend"
ADD_API = "https://amin-api-remove-add-jwt-token.onrender.com/adding_friend"

def get_jwt_token():
    global jwt_token
    url = "https://jwt-gen-api-v2.onrender.com/token?uid=3935704624&password=4DD9580BC3E3E64BBAA1455E624E02DF230BCD68D36E16CB451CC4EA734B3DF0"
    try:
        response = httpx.get(url)
        if response.status_code == 200:
            data = response.json()
            if data.get('status') == 'live':
                jwt_token = data['token']
                print("[JWT] Token updated:", jwt_token)
            else:
                print("[JWT] Failed to get token:", data)
        else:
            print("[JWT] Status code error:", response.status_code)
    except httpx.RequestError as e:
        print(f"[JWT] Request error: {e}")

def token_updater():
    while True:
        get_jwt_token()
        time.sleep(8 * 3600)

token_thread = Thread(target=token_updater, daemon=True)
token_thread.start()

####################################
STORAGE_FILE = 'uid_storage.json'
storage_lock = threading.Lock()

def ensure_storage_file():
    if not os.path.exists(STORAGE_FILE):
        with open(STORAGE_FILE, 'w') as file:
            json.dump({}, file)

def load_uids():
    ensure_storage_file()
    with open(STORAGE_FILE, 'r') as file:
        return json.load(file)

def save_uids(uids):
    ensure_storage_file()
    with open(STORAGE_FILE, 'w') as file:
        json.dump(uids, file, default=str)

def cleanup_expired_uids():
    while True:
        with storage_lock:
            uids = load_uids()
            current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            expired_uids = [uid for uid, exp_time in uids.items() if exp_time != 'permanent' and exp_time <= current_time]
            for uid in expired_uids:
                try:
                    if jwt_token:  
                        requests.get(f"{REMOVE_API}?token={jwt_token}&id={uid}&key={key2}", timeout=5)
                except:
                    pass
                del uids[uid]
                print(f"[CLEANUP] Deleted expired UID: {uid}")
            save_uids(uids)
        time.sleep(1)

cleanup_thread = threading.Thread(target=cleanup_expired_uids, daemon=True)
cleanup_thread.start()

####################################
# إضافة UID
@app.route('/add_uid', methods=['GET'])
def add_uid():
    uid = request.args.get('uid')
    time_value = request.args.get('time')
    time_unit = request.args.get('type')
    permanent = request.args.get('permanent', 'false').lower() == 'true'

    if not uid:
        return jsonify({'error': 'Missing parameter: uid'}), 400

    if permanent:
        expiration_time = 'permanent'
        try:
            if jwt_token: 
                requests.get(f"{ADD_API}?token={jwt_token}&id={uid}&key={key2}", timeout=5)
        except:
            pass
    else:
        if not time_value or not time_unit:
            return jsonify({'error': 'Missing parameters: time or unit'}), 400
        try:
            time_value = int(time_value)
        except ValueError:
            return jsonify({'error': 'Invalid time value. Must be an integer.'}), 400

        current_time = datetime.now()
        if time_unit == 'days':
            expiration_time = current_time + timedelta(days=time_value)
        elif time_unit == 'months':
            expiration_time = current_time + timedelta(days=time_value * 30) 
        elif time_unit == 'years':
            expiration_time = current_time + timedelta(days=time_value * 365)
        elif time_unit == 'seconds':
            expiration_time = current_time + timedelta(seconds=time_value)
        else:
            return jsonify({'error': 'Invalid type. Use "days", "months", "years", or "seconds".'}), 400
        expiration_time = expiration_time.strftime('%Y-%m-%d %H:%M:%S')
        try:
            if jwt_token:
                requests.get(f"{ADD_API}?token={jwt_token}&id={uid}&key={key2}", timeout=5)
        except:
            pass

    with storage_lock:
        uids = load_uids()
        uids[uid] = expiration_time
        save_uids(uids)

    return jsonify({
        'uid': uid,
        'expires_at': expiration_time if not permanent else 'never'
    })

####################################
# إزالة UID
@app.route('/remove', methods=['GET'])
def remove_uid():
    uid = request.args.get('uid')
    if not uid:
        return jsonify({'error': 'Missing parameter: uid'}), 400

    # حذف من API الخارجية
    external_status = "No JWT token available"
    if jwt_token:
        try:
            r = requests.get(f"{REMOVE_API}?token={jwt_token}&id={uid}&key={key2}", timeout=5)
            external_status = r.text
        except Exception as e:
            external_status = f"External API error: {e}"

    # حذف من التخزين المحلي
    with storage_lock:
        uids = load_uids()
        if uid in uids:
            del uids[uid]
            save_uids(uids)
            local_status = f"UID {uid} removed locally."
        else:
            local_status = f"UID {uid} not found locally."

    return jsonify({
        "uid": uid,
        "local_status": local_status,
        "external_status": external_status
    })

####################################
# معرفة الوقت المتبقي
@app.route('/get_time/<string:uid>', methods=['GET'])
def check_time(uid):
    with storage_lock:
        uids = load_uids()
        if uid not in uids:
            return jsonify({'error': 'UID not found'}), 404
        expiration_time = uids[uid]        
        if expiration_time == 'permanent':
            return jsonify({           
                'uid': uid,
                'status': 'permanent',
                'message': 'This UID will never expire.'
            })
        expiration_time = datetime.strptime(expiration_time, '%Y-%m-%d %H:%M:%S')
        current_time = datetime.now()
        if current_time > expiration_time:
            return jsonify({'error': 'UID has expired'}), 400
        remaining_time = expiration_time - current_time
        days = remaining_time.days
        hours, remainder = divmod(remaining_time.seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return jsonify({
            'uid': uid,
            'remaining_time': {
                'days': days,
                'hours': hours,
                'minutes': minutes,
                'seconds': seconds
            }
        })

####################################
if __name__ == '__main__':
    ensure_storage_file()
    app.run(host='0.0.0.0', port=50022, debug=False)
