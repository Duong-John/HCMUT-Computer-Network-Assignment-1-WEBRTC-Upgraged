#
# Copyright (C) 2025 pdnguyen of HCMC University of Technology VNU-HCM.
# All rights reserved.
# This file is part of the CO3093/CO3094 course,
# and is released under the "MIT License Agreement". Please see the LICENSE
# file that should have been included as part of this package.
#
# WeApRous release
#
# The authors hereby grant to Licensee personal permission to use
# and modify the Licensed Source Code for the sole purpose of studying
# while attending the course
#


"""
start_sampleapp
~~~~~~~~~~~~~~~~~

This module provides a Signaling Server for WebRTC application using the WeApRous framework.

It defines route handlers to exchange SDP Offers, Answers, and ICE Candidates between peers
via HTTP Polling.
"""
import time
import threading
import json
import socket
import argparse
import subprocess
import os
from filelock import FileLock

from daemon.weaprous import WeApRous

HEARTBEAT_INTERVAL = 10
PEER_TIMEOUT = 15
PORT = 8000

app = WeApRous()

users_lock = threading.Lock()
peers_lock = threading.Lock()
signaling_lock = threading.Lock()

# Database
DB_LOCK = FileLock("static/database/db.lock")
DB_DIR = os.path.join("static", "database")
USERS_FILE = os.path.join(DB_DIR, "registered_users.json")
PEERS_FILE = os.path.join(DB_DIR, "active_peers.json")
CONN_FILE = os.path.join(DB_DIR, "active_connections.json")
os.makedirs(DB_DIR, exist_ok=True)

# In-memory Signaling Mailbox (WebRTC)
# Structure: { "username": [ {signal_data}, {signal_data} ] }
signaling_mailbox = {}

# ------------------------------------------------------------------
# ACCESS TO DATABASE
# ------------------------------------------------------------------
def load_json(path):
    """Safe read JSON file"""
    try:
        if not os.path.exists(path):
            return {}
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[Error] load_json({path}): {e}")
        return {}

def save_json(path, data):
    """Safe write JSON file (thread-safe)"""
    try:
        tmp_path = path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, path)
    except Exception as e:
        print(f"[Error] save_json({path}): {e}")

# ------------------------------------------------------------------
# REGISTER OR LOGIN + PEER LIST (Old Logic)
# ------------------------------------------------------------------

@app.route('/login', methods=['POST'])
def login(headers="guest", body="anonymous"):
    print("[SignalingServer] Handling POST /login request.")
    username = body.get('username')
    password = body.get('password')

    ip = body.get('Ip', 'unknown') 

    print(f"[SignalingServer] Login attempt - User: {username}, Pass: {password}")
    is_valid = False
    with users_lock:
        users = load_json(USERS_FILE)
        if username in users and users[username] == password:
            is_valid = True

    if is_valid:
        print(f"[SignalingServer] User '{username}' authenticated successfully.")
        
        with peers_lock:
            peers = load_json(PEERS_FILE)
            
            peers[username] = {"ip": ip, "time": time.time(), "status": "online"}
            save_json(PEERS_FILE, peers)
        
        #Initialize mailbox with syn
        with signaling_lock:
            if username not in signaling_mailbox:
                signaling_mailbox[username] = []

        return 'Login Success'
    else:
        print(f"[SignalingServer] Authentication failed for user '{username}'.")
        return 'Login Fail'
    

@app.route('/register', methods=['POST'])
def register(headers, body):
    print("[SignalingServer] Handling POST /register request.")
    username = body.get('username')
    password = body.get('password')

    print(f"[SignalingServer] Register attempt - User: {username}, Pass: {password}")
    is_valid = False
    with users_lock:
        users = load_json(USERS_FILE)
        if username in users:
            print(f"[SignalingServer] Registration failed: '{username}' already exists.")
            return 'Register Fail'
        users[username] = password
        save_json(USERS_FILE, users)
        is_valid = True
    
    if is_valid:
        print(f"[SignalingServer] User '{username}' registered successfully.")
        return 'Register Success'
    else:
        print(f"[SignalingServer] Registation failed for user '{username}'.")
        return 'Register Fail'

@app.route('/peers', methods=['GET', 'OPTIONS'])
def get_active_peers(headers, body):
    # print("[API] Received request for active peer list.")
    with peers_lock:
        peers = load_json(PEERS_FILE)
        peers_copy = dict(peers)
        
        peers_copy.pop(headers.get("Cookie"), None) # THat own person
    return ('application/json', json.dumps(peers_copy))

# ------------------------------------------------------------------
# SECURITY: GET CREDENTIAL KEY
# ------------------------------------------------------------------
@app.route('/get_ice_config', methods=['GET'])
def get_ice_config(headers, body):
    
    username = headers.get("Cookie")
    if not username:
        return ('application/json', json.dumps({"status": "error", "message": "Unauthorized"}))

    # TURN Server for WEBRTC
    ice_config = {
        "iceServers": [
            # Public STUN SEVER (Not reliable for WEBRTC Connection)
            { "urls": "stun:stun.l.google.com:19302" },
            # Your STUN SEVER Credential Key here to allow real-time WEBRTC
        ]
    }
    
    return ('application/json', json.dumps(ice_config))

# ------------------------------------------------------------------
# WEBRTC SIGNALING HANDLERS (New Logic)
# ------------------------------------------------------------------

@app.route('/send_signal', methods=['POST'])
def send_signal(headers, body):
    """
    Endpoint de mot Peer gui tin hieu (Offer/Answer/Candidate) cho Peer khac.
    Body can co: { "target": "UserB", "type": "offer/answer/candidate", "data": ... }
    """
    sender = headers.get("Cookie", "Anonymous")
    target = body.get('target')
    signal_type = body.get('type')
    signal_data = body.get('data')

    if not target or not signal_data:
        return ('application/json', json.dumps({"status": "error", "message": "Missing target or data"}))

    print(f"[Signaling] {sender} -> {target} : Type [{signal_type}]")

    with signaling_lock:
        if target not in signaling_mailbox:
            signaling_mailbox[target] = []
        
        message = {
            "sender": sender,
            "type": signal_type,
            "data": signal_data,
            "timestamp": time.time()
        }
        signaling_mailbox[target].append(message)

    return ('application/json', json.dumps({"status": "ok"}))


@app.route('/get_signal', methods=['GET'])
def get_signal(headers, body):
    """
    Endpoint de Peer POLL tin hieu tu Server.
    Tra ve danh sach cac tin hieu dang cho trong hom thu va xoa chung di.
    """
    username = headers.get("Cookie")
    if not username:
        return ('application/json', json.dumps([]))

    messages = []
    with signaling_lock:
        if username in signaling_mailbox and signaling_mailbox[username]:
            messages = signaling_mailbox[username]
            signaling_mailbox[username] = [] # just delete the message :)
    
    if messages:
        print(f"[Signaling] Delivering {len(messages)} signals to {username}")
        
        # msg_types = [m.get('type') for m in messages]
        # print(f"   -> Types: {msg_types}")
        return ('application/json', json.dumps(messages))
    else:
        return ('application/json', json.dumps([]))



@app.route('/heartbeat', methods=['POST'])
def heartbeat(headers, body):
    # /hearbeat is for client's front-end to call
    username = headers.get("Cookie")
    if not username:
        return ('application/json', json.dumps({"status": "error"}))

    # last_seen time update
    with peers_lock:

        peers = load_json(PEERS_FILE)
        if username in peers:
            peers[username]['time'] = time.time()

            save_json(PEERS_FILE, peers)
        else:
            peers[username] = {"ip": "web-client", "time": time.time(), "status": "online"}

            save_json(PEERS_FILE, peers)

            
    return ('application/json', json.dumps({"status": "ok"}))


def heartbeat_thread():
    print(f"[System] Heartbeat monitor started. Timeout: {PEER_TIMEOUT}s")
    while True:
        time.sleep(HEARTBEAT_INTERVAL)
        # print("[Heartbeat] Scanning active peers...")

        with peers_lock:
            peers = load_json(PEERS_FILE)
            current_time = time.time()
            to_remove = []

            for username, info in peers.items():
                last_seen = info.get('time', 0)
               
               
                if current_time - last_seen > PEER_TIMEOUT:

                    print(f"[Heartbeat] {username} timed out. Removing from active list.")
                    to_remove.append(username)

            if to_remove:
                for u in to_remove:
                    peers.pop(u, None)
                    

                    #Delete the mailbox too
                    with signaling_lock:
                        if u in signaling_mailbox:
                            del signaling_mailbox[u]
                
                save_json(PEERS_FILE, peers)

    

    
if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog='Backend', description='', epilog='Backend daemon')
    parser.add_argument('--server-ip', default='0.0.0.0')
    parser.add_argument('--server-port', type=int, default=PORT)
 
    args = parser.parse_args()
    ip = args.server_ip
    port = args.server_port

    app.prepare_address(ip, port)
    threading.Thread(target=heartbeat_thread, daemon=True).start()
    app.run()