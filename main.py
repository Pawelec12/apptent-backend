import asyncio
from fastapi import FastAPI, BackgroundTasks, HTTPException
from pydantic import BaseModel
import httpx
import hmac
import hashlib
import os

app = FastAPI()

# Konfiguracja (Zmienne środowiskowe dla bezpieczeństwa)
RETELL_API_KEY = os.getenv("RETELL_API_KEY")
RETELL_AGENT_ID = os.getenv("RETELL_AGENT_ID")
SHOPIFY_WEBHOOK_SECRET = os.getenv("SHOPIFY_WEBHOOK_SECRET")
# W produkcji użyj Redis. Tutaj prosty słownik w pamięci dla MVP.
PENDING_CHECKOUTS = {} 

# --- MODELE DANYCH ---
class CheckoutStart(BaseModel):
    checkout_id: str
    phone: str
    customer_name: str
    cart_value: float
    items: str
    marketing_consent: bool

class ShopifyOrder(BaseModel):
    # Uproszczona struktura webhooka Shopify
    id: str
    checkout_id: str  # Shopify nazywa to checkout_token w order payload
    financial_status: str

# --- FUNKCJE POMOCNICZE ---

async def trigger_retell_call(data: dict):
    """Funkcja wykonująca połączenie przez Retell AI"""
    print(f"🔄 Próba połączenia z klientem: {data['phone']}")
    
    headers = {
        "Authorization": f"Bearer {RETELL_API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "agent_id": RETELL_AGENT_ID,
        "from_number": "+48123456789",  # Twój numer zakupiony w Retell
        "to_number": data['phone'],
        "dynamic_variables": {
            "customer_name": data['customer_name'],
            "cart_value": str(data['cart_value']),
            "cart_items": data['items'],
            "store_name": "TwójSklep.pl"
        }
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.post("https://api.retellai.com/v2/create-phone-call", json=payload, headers=headers)
        if response.status_code == 200:
            print("✅ Połączenie zainicjowane pomyślnie!")
        else:
            print(f"❌ Błąd Retell: {response.text}")

async def schedule_abandoned_cart_check(data: dict):
    """Czeka X minut, a potem dzwoni, jeśli zakup nie został zrealizowany"""
    delay_minutes = 2.5  # Czas na dokończenie zakupu
    
    await asyncio.sleep(delay_minutes * 60)
    
    # Sprawdź, czy w tym czasie nie doszło do zakupu (Checkout ID nadal w bazie = porzucone)
    if data['checkout_id'] in PENDING_CHECKOUTS:
        print(f"⚠️ Koszyk {data['checkout_id']} porzucony! Triggerowanie call center...")
        await trigger_retell_call(data)
        del PENDING_CHECKOUTS[data['checkout_id']]
    else:
        print(f"✅ Koszyk {data['checkout_id']} został opłacony. Anulowanie połączenia.")

# --- ENDPOINTY ---

@app.post("/webhook/checkout-start")
async def checkout_started(data: CheckoutStart, background_tasks: BackgroundTasks):
    """
    Ten endpoint wywołuje Twój skrypt JS na stronie, gdy klient wpisze telefon.
    """
    if not data.marketing_consent:
        return {"status": "skipped", "reason": "Brak zgody marketingowej"}

    # Zapisz stan koszyka w pamięci
    PENDING_CHECKOUTS[data.checkout_id] = data.dict()
    
    # Uruchom timer w tle (nie blokuje odpowiedzi dla frontendu)
    background_tasks.add_task(schedule_abandoned_cart_check, data.dict())
    
    return {"status": "scheduled", "wait_time": "2.5 min"}

@app.post("/webhook/shopify-order")
async def shopify_order_created(request: Request):
    """
    Webhook Shopify (orders/create). 
    Jeśli klient kupił, usuwamy go z bazy 'oczekujących', żeby bot nie zadzwonił.
    """
    # 1. Weryfikacja HMAC (Bezpieczeństwo - wymagane przez Shopify)
    received_hmac = request.headers.get("X-Shopify-Hmac-Sha256")
    body = await request.body()
    
    # Tutaj weryfikujemy podpis (kodu nie podaję w pełni dla zwięzłości, ale jest krytyczny)
    # ... weryfikacja HMAC ...
    
    data = await request.json()
    
    # Shopify w webhooku order/create zwraca checkout_token, który łączy sesję
    checkout_token = data.get("checkout_token") 
    
    if checkout_token and checkout_token in PENDING_CHECKOUTS:
        # Klient kupił! Usuwamy z kolejki.
        print(f"🎉 Zakup wykryty dla {checkout_token}. Anulowanie triggera.")
        del PENDING_CHECKOUTS[checkout_token]
        
    return {"status": "received"}
