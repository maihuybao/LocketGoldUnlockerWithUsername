from flask import Flask, render_template, request, jsonify
from auth import Auth
from api import LocketAPI
import json
import time
import requests
import queue
import threading
import uuid
from datetime import datetime
import dotenv
import os

app = Flask(__name__)

dotenv.load_dotenv()

# Initialize API and Auth
subscription_ids = [
    "locket_1600_1y",
    "locket_199_1m",
    "locket_199_1m_only",
    "locket_3600_1y",
    "locket_399_1m_only",
]

auth = Auth(os.getenv("EMAIL"), os.getenv("PASSWORD"))
try:
    token = auth.get_token()
    api = LocketAPI(token)
except Exception as e:
    print(f"Error initializing API: {e}")
    api = None


# Queue Management System
class QueueManager:
    def __init__(self):
        self.queue = queue.Queue()
        self.lock = threading.Lock()
        self.client_requests = {}  # client_id -> request data
        self.processing_times = []  # Track processing times for estimates
        self.current_processing = None
        self.worker_thread = threading.Thread(target=self._process_queue, daemon=True)
        self.worker_thread.start()
        print("Queue manager initialized and worker thread started")

    def add_to_queue(self, username):
        """Add a request to the queue and return client_id"""
        client_id = str(uuid.uuid4())
        request_data = {
            "username": username,
            "status": "waiting",
            "result": None,
            "error": None,
            "added_at": datetime.now(),
            "started_at": None,
            "completed_at": None,
        }

        with self.lock:
            self.client_requests[client_id] = request_data

        self.queue.put(client_id)
        print(f"Added {username} to queue with client_id: {client_id}")
        return client_id

    def get_status(self, client_id):
        """Get current status of a request"""
        with self.lock:
            if client_id not in self.client_requests:
                return None

            request_data = self.client_requests[client_id].copy()

            # Calculate position in queue
            position = self._get_position(client_id)
            total_queue = self.queue.qsize()
            if self.current_processing and self.current_processing != client_id:
                total_queue += 1

            # Estimate wait time
            estimated_time = self._estimate_wait_time(position)

            return {
                "client_id": client_id,
                "status": request_data["status"],
                "position": position,
                "total_queue": total_queue,
                "estimated_time": estimated_time,
                "result": request_data["result"],
                "error": request_data["error"],
            }

    def _get_position(self, client_id):
        """Get position of client in queue (1-indexed)"""
        if self.current_processing == client_id:
            return 0  # Currently processing

        # Check if in queue
        queue_list = list(self.queue.queue)
        if client_id in queue_list:
            return queue_list.index(client_id) + 1

        # Check status
        if client_id in self.client_requests:
            status = self.client_requests[client_id]["status"]
            if status in ["completed", "error"]:
                return 0

        return 0

    def _estimate_wait_time(self, position):
        """Estimate wait time in seconds based on position"""
        if position == 0:
            return 0

        # Use average processing time or default to 5 seconds
        avg_time = 5  # Default
        if self.processing_times:
            avg_time = sum(self.processing_times[-10:]) / len(
                self.processing_times[-10:]
            )

        return int(position * avg_time)

    def _process_queue(self):
        """Background worker to process queue sequentially"""
        print("Queue worker thread started")
        while True:
            try:
                # Get next client from queue (blocking)
                client_id = self.queue.get(timeout=1)

                with self.lock:
                    if client_id not in self.client_requests:
                        continue

                    self.current_processing = client_id
                    self.client_requests[client_id]["status"] = "processing"
                    self.client_requests[client_id]["started_at"] = datetime.now()

                print(f"Processing request for client_id: {client_id}")

                # Process the request
                self._process_request(client_id)

                # Mark as complete
                with self.lock:
                    self.current_processing = None
                    if client_id in self.client_requests:
                        self.client_requests[client_id]["completed_at"] = datetime.now()

                        # Calculate processing time
                        started = self.client_requests[client_id]["started_at"]
                        completed = self.client_requests[client_id]["completed_at"]
                        duration = (completed - started).total_seconds()
                        self.processing_times.append(duration)

                        # Keep only last 20 times
                        if len(self.processing_times) > 20:
                            self.processing_times.pop(0)

                self.queue.task_done()

            except queue.Empty:
                continue
            except Exception as e:
                print(f"Error in queue processing: {e}")
                with self.lock:
                    self.current_processing = None

    def _process_request(self, client_id):
        """Process a single restore purchase request"""
        try:
            with self.lock:
                username = self.client_requests[client_id]["username"]

            print(f"Processing restore for: {username}")

            # User lookup
            try:
                account_info = api.getUserByUsername(username)
            except Exception as e:
                if "401" in str(e) or "Unauthenticated" in str(e):
                    print(f"Creating new token because of {e}")
                    if refresh_api_token():
                        account_info = api.getUserByUsername(username)
                    else:
                        raise e
                else:
                    raise e

            # Check if we got a valid response structure
            if not account_info or "result" not in account_info:
                raise Exception("User not found or API error")

            user_data = account_info.get("result", {}).get("data")
            if not user_data:
                raise Exception("User data not found")

            uid_target = user_data.get("uid")
            if not uid_target:
                raise Exception("UID not found for user")

            # Restore purchase
            try:
                restore_result = api.restorePurchase(uid_target)
            except Exception as e:
                if "401" in str(e) or "Unauthenticated" in str(e):
                    print(f"Creating new token because of {e}")
                    if refresh_api_token():
                        restore_result = api.restorePurchase(uid_target)
                    else:
                        raise e
                else:
                    raise e

            # Check entitlement
            entitlements = restore_result.get("subscriber", {}).get("entitlements", {})
            gold_entitlement = entitlements.get("Gold", {})

            if gold_entitlement.get("product_identifier") in subscription_ids:
                # Send Telegram notification
                send_telegram_notification(
                    username,
                    uid_target,
                    gold_entitlement.get("product_identifier"),
                    restore_result,
                )

                with self.lock:
                    self.client_requests[client_id]["status"] = "completed"
                    self.client_requests[client_id]["result"] = {
                        "success": True,
                        "msg": f"Purchase {gold_entitlement.get('product_identifier')} for {username} successfully!",
                    }
            else:
                raise Exception(
                    f"Restore purchase failed. Gold entitlement not found for {username}."
                )

        except Exception as e:
            print(f"Error processing request for {client_id}: {e}")
            with self.lock:
                self.client_requests[client_id]["status"] = "error"
                self.client_requests[client_id]["error"] = str(e)


# Initialize queue manager
queue_manager = QueueManager()


def refresh_api_token():
    global api
    try:
        print("Refreshing API token...")
        new_token = auth.create_token()
        api = LocketAPI(new_token)
        print("API token refreshed successfully.")
        return True
    except Exception as e:
        print(f"Failed to refresh API token: {e}")
        return False


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/get-user-info", methods=["POST"])
def get_user_info():
    if not api:
        return jsonify(
            {"success": False, "msg": "API not initialized. Check server logs."}
        ), 500

    data = request.json
    username = data.get("username")

    if not username:
        return jsonify({"success": False, "msg": "Username is required"}), 400

    try:
        # User lookup
        print(f"Looking up user: {username}")
        try:
            account_info = api.getUserByUsername(username)
        except Exception as e:
            if "401" in str(e) or "Unauthenticated" in str(e):
                print(f"Creating new token because of {e}")
                if refresh_api_token():
                    account_info = api.getUserByUsername(username)
                else:
                    raise e
            else:
                raise e

        # Check if we got a valid response structure
        if not account_info or "result" not in account_info:
            return jsonify(
                {"success": False, "msg": "User not found or API error"}
            ), 404

        user_data = account_info.get("result", {}).get("data")
        if not user_data:
            return jsonify({"success": False, "msg": "User data not found"}), 404

        # Extract relevant user information
        user_info = {
            "uid": user_data.get("uid"),
            "username": user_data.get("username"),
            "first_name": user_data.get("first_name", ""),
            "last_name": user_data.get("last_name", ""),
            "profile_picture_url": user_data.get("profile_picture_url", ""),
        }

        return jsonify({"success": True, "data": user_info})

    except Exception as e:
        print(f"Error in get user info: {e}")
        return jsonify({"success": False, "msg": f"An error occurred: {str(e)}"}), 500


def send_telegram_notification(username, uid, product_id, raw_json):
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if bot_token == "" or chat_id == "":
        print("Telegram notification skipped: Token or Chat ID not set.")
        return
    subscription_info = json.dumps(
        raw_json.get("subscriber", {}).get("entitlements", {}).get("Gold", {}), indent=2
    )

    message = f"✅ <b>Locket Gold Unlocked!</b>\n\n👤 <b>User:</b> {username} ({uid})\n⏰ <b>Time:</b> {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n<b>Subscription Info:</b>\n<pre>{subscription_info}</pre>"
    # send file json
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message, "parse_mode": "HTML"}

    try:
        requests.post(url, json=payload)
    except Exception as e:
        print(f"Failed to send Telegram notification: {e}")


@app.route("/api/restore", methods=["POST"])
def restore_purchase():
    """Add request to queue and return client_id for tracking"""
    if not api:
        return jsonify(
            {"success": False, "msg": "API not initialized. Check server logs."}
        ), 500

    data = request.json
    username = data.get("username")

    if not username:
        return jsonify({"success": False, "msg": "Username is required"}), 400

    try:
        # Add to queue
        client_id = queue_manager.add_to_queue(username)

        # Get initial status
        status = queue_manager.get_status(client_id)

        return jsonify(
            {
                "success": True,
                "client_id": client_id,
                "position": status["position"],
                "total_queue": status["total_queue"],
                "estimated_time": status["estimated_time"],
            }
        )

    except Exception as e:
        print(f"Error adding to queue: {e}")
        return jsonify({"success": False, "msg": f"An error occurred: {str(e)}"}), 500


@app.route("/api/queue/status", methods=["POST"])
def queue_status():
    """Get current queue status for a client"""
    data = request.json
    client_id = data.get("client_id")

    if not client_id:
        return jsonify({"success": False, "msg": "client_id is required"}), 400

    status = queue_manager.get_status(client_id)

    if status is None:
        return jsonify({"success": False, "msg": "Client ID not found"}), 404

    return jsonify({"success": True, **status})


if __name__ == "__main__":
    app.run(debug=True, port=5000)
