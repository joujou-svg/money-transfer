import os, uuid
import logging
import requests
from flask import Flask, request, jsonify, redirect, render_template
from flask_cors import CORS
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Flask app setup
app = Flask(__name__)
CORS(app, supports_credentials=True)

# Configure logging
logging.basicConfig(level=logging.INFO)

# Chargily API Configuration
CHARGILY_API_KEY = os.getenv("CHARGILY_API_KEY", "test_api_key")
CHARGILY_SECRET_KEY = os.getenv("CHARGILY_SECRET_KEY", "test_secret_key")
CHARGILY_BASE_URL = "https://pay.chargily.net/test/api/v2"

# PayPal API Configuration
PAYPAL_CLIENT_ID = os.getenv("PAYPAL_CLIENT_ID", "tes_client_id")
PAYPAL_CLIENT_SECRET = os.getenv("PAYPAL_CLIENT_SECRET", "test_client_secret")
PAYPAL_BASE_URL = "https://api.sandbox.paypal.com" #if os.getenv("ENV") == "development" else "https://api.paypal.com"

# Fees Configuration
SERVICE_FEE_RATE = 0.02  # 2% service fee
MIN_SERVICE_FEE = 10  # Minimum 10 DZD service fee
EXCHANGE_RATE_USD_TO_DZD = 134.5  # Fixed exchange rate

# Wise configurations
WISE_API_URL = "https://api.sandbox.transferwise.com"
WISE_API_TOKEN = "YOUR_API_TOKEN"
PROFILE_ID = "YOUR_WISE_PROFILE_ID"

# Webhook URLs
WEBHOOK_BASE_URL = os.getenv("WEBHOOK_BASE_URL", "https://c59f-129-45-99-110.ngrok-free.app")

# all chargily credientials according to providers
CHARGILY_CREDENTIALS = {'quicktransfer':[CHARGILY_SECRET_KEY, CHARGILY_API_KEY], 'second provider':['secret key', 'api key']}

# all PayPal credientials according to providers
PAYPAL_CREDENTIALS = {'quicktransfer':[PAYPAL_CLIENT_SECRET, PAYPAL_CLIENT_ID], 'secpmd provider':['client secret', 'client id']}

# all wise credentials according to providers
WISE_CREDENTIALS = {'quicktransfer':[WISE_API_TOKEN, PROFILE_ID], 'second provider':['api token', 'profile id']}

headers = {
    "Authorization": f"Bearer {WISE_API_TOKEN}",
    "Content-Type": "application/json"
}
# Utility Functions
def calculate_service_fee(amount):
    return max(amount * SERVICE_FEE_RATE, MIN_SERVICE_FEE)

def convert_dzd_to_usd(dzd_amount):
    return round(dzd_amount / EXCHANGE_RATE_USD_TO_DZD, 2)

# get wise balance 
def get_wise_balance(api_token, profile_id):
    url = f"https://api.transferwise.com/v1/profiles/{profile_id}/balances"
    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json"
    }
    response = requests.get(url, headers=headers)

    if response.ok:
        return response.json()
    return {"error": response.text}

def create_quote(source_currency, target_currency, amount, profile_id):
    url = f"{WISE_API_URL}/v1/quotes"
    payload = {
        "sourceCurrency": source_currency,
        "targetCurrency": target_currency,
        "sourceAmount": amount,
        "profile": profile_id
    }
    response = requests.post(url, headers=headers, json=payload)
    response.raise_for_status()
    return response.json()

def create_recipient(name, currency, iban, email, profile_id):
    url = f"{WISE_API_URL}/v1/accounts"
    payload = {
        "profile": profile_id,
        "accountHolderName": name,
        "currency": currency,
        "type": "iban",
        "details": {
            "iban": iban,
            "email": email
        }
    }
    response = requests.post(url, headers=headers, json=payload)
    response.raise_for_status()
    return response.json()

def create_transfer(recipient_id, quote_id):
    url = f"{WISE_API_URL}/v1/transfers"
    payload = {
        "targetAccount": recipient_id,
        "quoteUuid": quote_id,
        "customerTransactionId": str(uuid.uuid4()),  # Unique ID
        "details": {
            "reference": "Payment from Flask app"
        }
    }
    response = requests.post(url, headers=headers, json=payload)
    response.raise_for_status()
    return response.json()

def fund_transfer(transfer_id, profile_id):
    url = f"{WISE_API_URL}/v3/profiles/{[profile_id]}/transfers/{transfer_id}/payments"
    payload = {
        "type": "BALANCE"
    }
    response = requests.post(url, headers=headers, json=payload)
    response.raise_for_status()
    return response.json()

def get_customer_by_email(email, chargily_secret_key):
    """Check if a customer exists in Chargily by email."""
    headers = {"Authorization": f"Bearer {chargily_secret_key}"}
    response = requests.get(f"{CHARGILY_BASE_URL}/customers", headers=headers)
    
    if response.status_code == 200:
        customers = response.json().get("data", [])
        for customer in customers:
            if customer["email"] == email:
                return customer  # Return existing customer
    return None  # Customer not found

def create_customer(name, email, phone, address, state, chargily_secret_key):
    """Create a new customer in Chargily."""
    headers = {
        "Authorization": f"Bearer {chargily_secret_key}",
        "Content-Type": "application/json"
    }
    customer_data = {
        "name": name,
        "email": email,
        "phone": phone,
        "address": {
            "country": 'DZ',
            "state": state,
            "address": address
        },
        "metadata": [{"source": "money_transfer_app"}] # Store non-sensitive metadata
    }
    response = requests.post(f"{CHARGILY_BASE_URL}/customers", json=customer_data, headers=headers)
    
    if response.status_code == 200:
        return response.json()  # Return new customer data
    else:
        logging.error(f"Failed to create customer: {response.text}")
        return None

def create_chargily_checkout(amount, customer_id, target_email, transaction_id, iban, method, provider, chargily_secret_key):
    """Create a payment checkout in Chargily."""
    service_fee = calculate_service_fee(amount)
    total_amount = amount + service_fee  # Ensure customer pays the fee

    headers = {
        "Authorization": f"Bearer {chargily_secret_key}",
        "Content-Type": "application/json"
    }
    checkout_data = {
        "amount": int(total_amount),  # Chargily expects amount in cents
        "currency": "dzd",
        "customer_id": customer_id,
        "success_url": f"{WEBHOOK_BASE_URL}/webhooks/chargily/success",
        "failure_url": f"{WEBHOOK_BASE_URL}/webhooks/chargily/cancel",
        "chargily_pay_fees_allocation": 'customer',
        "webhook_endpoint": f"{WEBHOOK_BASE_URL}/webhooks/chargily",
        "metadata": {
            "original_amount": amount,
            "service_fee": service_fee,
            "target_email": target_email,
            "transaction_id": transaction_id,
            "wise_iban": iban,
            "method": method,
            "provider": provider
            }       
    }
    response = requests.post(f"{CHARGILY_BASE_URL}/checkouts", json=checkout_data, headers=headers)
    
    if response.status_code == 200:
        return response.json()  # Return checkout details
    else:
        logging.error(f"Failed to create checkout: {response.text}")
        return None
# get paypal balance 
import requests
from requests.auth import HTTPBasicAuth

def get_paypal_balance(paypal_client_id, paypal_client_secret):
    # Get access token
    access_token = get_paypal_access_token(paypal_client_id, paypal_client_secret)

    if not access_token:
        return {"error": "Failed to get PayPal access token"}

    # Get balance
    balance_url = "https://api.sandbox.paypal.com/v1/reporting/balances"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    response = requests.get(balance_url, headers=headers)

    if response.ok:
        return response.json()
    print(response.json())
    return {"error": response.text}

def get_paypal_access_token(paypal_client_id, paypal_client_secret):
    """Retrieve PayPal access token."""
    auth = (paypal_client_id, paypal_client_secret)
    response = requests.post(
        f"{PAYPAL_BASE_URL}/v1/oauth2/token",
        data={"grant_type": "client_credentials"},
        auth=auth
    )
    response.raise_for_status()
    return response.json()["access_token"]

# retreive payout status
import requests

def get_paypal_payout_status(sender_batch_id, paypal_client_id, paypal_client_secret):
    
    access_token =  access_token = get_paypal_access_token(paypal_client_id, paypal_client_secret)
    base_url = "https://api.sandbox.paypal.com" 
    url = f"{base_url}/v1/payments/payouts/{sender_batch_id}"

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {access_token}"
    }

    response = requests.get(url, headers=headers)

    if response.status_code == 200:
        return {"payoutExists": True}
    else:
        return {
            "payoutExists": False,
        }

def send_paypal_payout(email, amount, transaction_id, paypal_client_id, paypal_client_secret):
    """Send money to PayPal recipient."""
    access_token = get_paypal_access_token(paypal_client_id, paypal_client_secret)
    payout_data = {
        "sender_batch_header": {
            "sender_batch_id": transaction_id,
            "email_subject": "Money Transfer Payout"
        },
        "items": [{
            "recipient_type": "EMAIL",
            "amount": {"value": str(amount), "currency": "USD"},
            "receiver": email,
            "note": f"Transfer payout -{transaction_id} "
        }]
    }
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    response = requests.post(f"{PAYPAL_BASE_URL}/v1/payments/payouts", json=payout_data, headers=headers)
    
    if response.status_code == 201:
        url = f"{PAYPAL_BASE_URL}/v1/payments/payouts/HNSY8TK35CZWU"
        headers = {"Authorization": f"Bearer {access_token}"}
        responsee = requests.get(url, headers=headers)
        if responsee.status_code == 200:
            print(responsee.json())  # Contains the latest batch_status
            return response
        else:
            return {"error": responsee.text}
        return response.json()  # Return payout details
    else:
        logging.error(f"Failed to send PayPal payout: {response.text}")
        return None

# API Routes  /api/start-transfer
        
@app.route("/", methods=["POST", 'OPTIONS'])
def start_transfer():
    if request.method == "OPTIONS":
        return _build_cors_preflight_response()
    """Initiate a money transfer."""
    print(request.json)
    global provider, method
    data = request.json
    provider = data.get('provider')
    name = data.get("name")
    email = data.get("email")
    phone = data.get("phone")
    address = data.get("address")
    state = data.get("state")
    method = data.get("transferMethod")
    amount = data.get("amount")
    transaction_id = str(uuid.uuid4())
    if method == 'wise' :
        target_email = data.get("wiseEmail")
        iban = data.get("iban")
        balance = get_wise_balance(WISE_CREDENTIALS[provider][0], WISE_CREDENTIALS[provider][1])
    elif method == "paypal" :
        iban = 'empty'
        target_email = data.get("paypalEmail")
        balance = get_paypal_balance(PAYPAL_CREDENTIALS[provider][1], PAYPAL_CREDENTIALS[provider][0])
        
    if not all([name, email, amount, iban]) or amount < 75:
        print(name, email, target_email, iban, amount)
        return jsonify({"error": "Invalid input or amount below minimum"}), 400

    # Check if customer exists
    customer = get_customer_by_email(email, CHARGILY_CREDENTIALS[provider][0])
    
    if not customer:
        print(address)
        # here i need to check for address, country and state
        if not all([address, state, phone]) :
            return jsonify({
                "customerExists" : False
            })
        else :
            #when creating a customer i need to enter  even the country and the state
            customer = create_customer(name, email, phone, address, state, CHARGILY_CREDENTIALS[provider][0])
            if not customer:
                return jsonify({"error": "Failed to create customer"}), 500
    # Check the balance first 
    if True : #int(balance['balance']) > convert_dzd_to_usd(amount) :
        # Create checkout
        checkout = create_chargily_checkout(amount, customer["id"], target_email, transaction_id, iban, method, provider, CHARGILY_CREDENTIALS[provider][0])
        if not checkout:
            return jsonify({"error": "Failed to create checkout"}), 500

        return jsonify({
            "transactionId": checkout["id"],
            "paymentUrl": checkout["checkout_url"],
            "status": "payment_pending",
            "totalAmount": checkout["amount"] ,
            "serviceFee": checkout["metadata"]["service_fee"]
        })
    else :
        return jsonify({"error": 'insuficient balance please  try another provider or another method'})

@app.route("/webhooks/chargily", methods=["POST", "OPTIONS"])
def chargily_webhook():
    if request.method == "OPTIONS":
        return _build_cors_preflight_response()

    data = request.get_json(force=True, silent=True)
    if not data:
        return jsonify({"error": "Invalid JSON"}), 400

    # Extract needed fields
    data = data.get('data', {})
    metadata = data.get("metadata", {})
    transaction_id = metadata.get("transaction_id")
    amount_dzd = metadata.get("original_amount")
    target_email = metadata.get("target_email")
    method = metadata.get("method")
    iban = metadata.get("wise_iban")
    provider = metadata.get("provider")

    if not all([transaction_id, amount_dzd, target_email]):
        return jsonify({"error": "Missing data"}), 400

    usd_amount = convert_dzd_to_usd(amount_dzd)
    #payout_exists = get_paypal_payout_status(transaction_id, PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET)

    if True :
        if method == 'paypal' :
            payout_result = send_paypal_payout(target_email, usd_amount, transaction_id, PAYPAL_CREDENTIALS[provider][1], PAYPAL_CREDENTIALS[provider][0])
        elif method == 'wise':
            quote = create_quote("USD", "USD", usd_amount, WISE_CREDENTIALS[provider][1])
            recipient = create_recipient("USD", "USD", iban, target_email, WISE_CREDENTIALS[provider][1])
            transfer = create_transfer(recipient["id"], quote["id"])
            payout_result = fund_transfer(transfer["id"], WISE_CREDENTIALS[provider][1])

        if payout_result:
            return jsonify({"message": "Payout success", "payout": payout_result}), 200
        else:
            print('this is the route of the problem')
            return jsonify({"error": "PayPal payout failed"}), 500
    else :
        return jsonify({"payout_staus":'this payout have already been made'})
    
@app.route("/webhooks/chargily/success", methods=["GET","OPTIONS"])
def payment_success():
    if request.method == "OPTIONS":
        return _build_cors_preflight_response()
    """Handle successful payments"""
    return redirect("http://127.0.0.1:5500/main.html")#jsonify({"message": "Payment successful! PayPal payout initiated.", "payoutDetails": payout_result})

@app.route("/webhooks/chargily/cancel", methods=["GET"])
def payment_cancel():
    """Handle canceled payments."""
    return redirect("http://127.0.0.1:5500/main.html")#jsonify({"message": "Payment canceled!"})

def _build_cors_preflight_response():
    """Returns proper headers for CORS preflight requests."""
    response = jsonify({"message": "CORS preflight successful"})
    response.headers.add("Access-Control-Allow-Origin", "*")
    response.headers.add("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    response.headers.add("Access-Control-Allow-Headers", "Content-Type, Authorization")
    return response

# Run Flask app
if __name__ == "__main__":
    app.run(port=int(os.getenv("PORT", 3000)), debug=True)
    
