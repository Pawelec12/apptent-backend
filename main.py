import asyncio
import os
import httpx
from fastapi import FastAPI, BackgroundTasks, HTTPException, Request
from pydantic import BaseModel

app = FastAPI()

# Konfiguracja
RETELL_API_KEY = os.getenv("RETELL_API_KEY")
RETELL_AGENT_ID = os.getenv("RETELL_AGENT_ID")
SHOPIFY_WEBHOOK_SECRET = os.getenv("SHOPIFY_WEBHOOK_SECRET")

# Baza danych w pamięci
PENDING_CHECKOUTS = {} 

# --- MODELE DANYCH ---
class CheckoutStart(BaseModel):
    checkout_id: str
    phone: str
    customer_name: str
    cart_value: float
    items: str
    marketing_consent: bool

# --- FUNKCJE POMOCNICZE ---

async def trigger_retell_call(data: dict):
    print(f"🔄 Próba połączenia z klientem: {data['phone']}")
    
    headers = {
        "Authorization": f"Bearer {RETELL_API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "agent_id": RETELL_AGENT_ID,
        "from_number": "+48123456789",  # Zmień na swój numer z Retell
        "to_number": data['phone'],
        "dynamic_variables": {
            "customer_name": data.get('customer_name', 'Kliencie'),
            "cart_value": str(data.get('cart_value', '0')),
            "cart_items": data.get('items', 'produkt'),
            "store_name": "ApptentStore"
        }
    }
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                "https://api.retellai.com/v2/create-phone-call", 
                json=payload, 
                headers=headers
            )
            if response.status_code == 200:
                print("✅ Połączenie zainicjowane pomyślnie!")
            else:
                print(f"❌ Błąd Retell: {response.text}")
        except Exception as e:
            print(f"❌ Wyjątek przy połączeniu: {e}")

async def schedule_abandoned_cart_check(data: dict):
    delay_minutes = 2.5
    
    await asyncio.sleep(delay_minutes * 60)
    
    if data['checkout_id'] in PENDING_CHECKOUTS:
        print(f"⚠️ Koszyk {data['checkout_id']} porzucony! Dzwonię...")
        await trigger_retell_call(data)
        del PENDING_CHECKOUTS[data['checkout_id']]
    else:
        print(f"✅ Koszyk {data['checkout_id']} opłacony.")

# --- ENDPOINTY ---

@app.get("/")
async def root():
    return {"status": "Apptent Backend is running!"}

@app.post("/webhook/shopify-checkout-update")
async def shopify_checkout_update(request: Request):
    data = await request.json()
    
    checkout_token = data.get("token") 
    phone = data.get("phone")
    
    if phone and checkout_token and checkout_token not in PENDING_CHECKOUTS:
        print(f"📞 Wykryto telefon w checkout: {phone}")
        
        line_items = data.get("line_items", [])
        cart_items = ", ".join([item.get("title", "") for item in line_items])
        
        new_data = {
            "checkout_id": checkout_token,
            "phone": phone,
            "customer_name": data.get("customer", {}).get("first_name", "Kliencie"),
            "cart_value": float(data.get("subtotal_price", 0)),
            "items": cart_items,
            "marketing_consent": True 
        }
        
        PENDING_CHECKOUTS[checkout_token] = new_data
        asyncio.create_task(schedule_abandoned_cart_check(new_data))

    return {"status": "received"}

@app.post("/webhook/shopify-order")
async def shopify_order_created(request: Request):
    data = await request.json()
    checkout_token = data.get("checkout_token") or data.get("id")
    
    if checkout_token and checkout_token in PENDING_CHECKOUTS:
        print(f"🎉 Zakup wykryty dla {checkout_token}. Anulowanie połączenia.")
        del PENDING_CHECKOUTS[checkout_token]
        
    return {"status": "received"}

@app.post("/webhook/checkout-start")
async def checkout_started(data: CheckoutStart, background_tasks: BackgroundTasks):
    if not data.marketing_consent:
        return {"status": "skipped", "reason": "Brak zgody"}

    PENDING_CHECKOUTS[data.checkout_id] = data.dict()
    background_tasks.add_task(schedule_abandoned_cart_check, data.dict())
    
    return {"status": "scheduled"}
