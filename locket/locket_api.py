import json
import requests
import time
import random
import dotenv

from . import proxies as proxy_pool
from .tokens import tokens_store

dotenv.load_dotenv()


def _post_with_proxy(url, *, headers=None, json_body=None, data=None, timeout=20):
    """POST with the proxy pool: tries up to 3 different proxies, then direct.
    Returns the first response we got (any status), so the caller decides
    success/failure. Raises only if every attempt threw a network error."""
    attempts = []
    for _ in range(3):
        pid, pdict = proxy_pool.next_proxy()
        if pid is None:
            break
        attempts.append((pid, pdict))
    attempts.append((None, None))  # direct fallback

    last_exc = None
    for pid, pdict in attempts:
        try:
            kw = {"headers": headers, "timeout": timeout, "proxies": pdict}
            if json_body is not None:
                kw["json"] = json_body
            if data is not None:
                kw["data"] = data
            resp = requests.post(url, **kw)
            # Per-proxy health: 5xx counts as a proxy/upstream issue too.
            if pid is not None:
                if resp.status_code < 500:
                    proxy_pool.mark_ok(pid)
                else:
                    proxy_pool.mark_err(pid, f"HTTP {resp.status_code}")
            # Non-5xx → return immediately. 5xx → try next attempt.
            if resp.status_code < 500 or pid is None:
                return resp
        except Exception as e:
            last_exc = e
            if pid is not None:
                proxy_pool.mark_err(pid, str(e)[:200])
            continue
    if last_exc is not None:
        raise last_exc
    return resp  # final 5xx response after exhausting proxies


class LocketAPI:
    def __init__(self, token):
        self.token = token
        # Store common headers in a dictionary for reuse
        self.headers = {
            "Accept": "*/*",
            "Accept-Language": "en-GB,en;q=0.9",
            "Authorization": f"Bearer {self.token}",
            "Connection": "keep-alive",
            "Content-Type": "application/json",
            "X-Client-Version": "iOS/FirebaseSDK/10.23.1/FirebaseCore-iOS",
            "X-Firebase-GMPID": "1:641029076083:ios:cc8eb46290d69b234fa606",
            "X-Ios-Bundle-Identifier": "com.locket.Locket",
            "X-Firebase-AppCheck": (
                "eyJraWQiOiJNbjVDS1EiLCJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9."
                "eyJzdWIiOiIxOjY0MTAyOTA3NjA4Mzppb3M6Y2M4ZWI0NjI5MGQ2OWIyMzRmYTYwNiIsImF1ZCI6WyJwcm9qZWN0c1wvNjQxMDI5MDc2MDgzIiwicHJvamVjdHNcL2xvY2tldC00MjUyYSJdLCJwcm92aWRlciI6ImRldmljZV9jaGVja19kZXZpY2VfaWRlbnRpZmljYXRpb24iLCJpc3MiOiJodHRwczpcL1wvZmlyZWJhc2VhcHBjaGVjay5nb29nbGVhcGlzLmNvbVwvNjQxMDI5MDc2MDgzIiwiZXhwIjoxNzIyMjQwNjcwLCJpYXQiOjE3MjIyMzcwNzAsImp0aSI6ImFMTmF3aHlBc3E2a2ROT1FRTS1PT1FwX2gyTlU1ZDZGZUdIcUZoYTJZWXMifQ."
                "C1dXXEB_4q1-hWNkEV66HmycPNRiTHLn3nBoVrwmIEQ2opJ6S9rO4h7_K2_EdsMQkut_p-dGU8GiWZyBLi6MohzIfANfWggYS_Et2l6ZjCGJish-lt6FlIForpe4PAnG6OPreEL1qyzjFqD5IBN0FvdKuhEFMpDwBHQeSuubpkfRaki67jxR016cAZy6VDb42H2dqTH2t7rhwr5VCzErtzEKm711DTrFm0Rxgnvk8TcqOhjno6CDkUvfFc4RYMDmPVIuuX6H8zNBDVcvR5LFmZD5eo38lUwwQU1BoyQfgEMXp2w86MjtYm6KrF7U9TUfrgMz9I5e66oFBn5vqIUE594Pi7jmkcxbt_mW29FH3B4HIIAzvI-4WrVgGSkVidq6kZGKDfBt5NjxBYzfDiOtWtnUyUJmziZAbXayrYkRoJP2g8DS2Dsc-NvwIXVV_29YdgxYFIW1PjhTp2gmXMVTb4uHHUaMmd0j4Y4NgtgPwcVswSwawgy3e6C6-K01X6Xx"
            ),
        }

    def getUserByUsername(self, username):
        if not username:
            raise ValueError("Username is required")

        request_payload = {
            "data": {
                "username": username,
            }
        }

        response = _post_with_proxy(
            "https://api.locketcamera.com/getUserByUsername",
            headers=self.headers,
            json_body=request_payload,
            timeout=20,
        )
        # print(response.json())
        if response.ok:
            return response.json()
        else:
            raise Exception(
                f"API request failed with status code {response.status_code}: {response.text}"
            )

    def restorePurchase(self, uid):
        """Restores the purchase using the provided token.

        Returns:
            dict: The JSON response from the API if successful.

        Raises:
            Exception: If the API request fails.
        """
        url = "https://api.revenuecat.com/v1/receipts"

        tokens = tokens_store.get_payloads()
        if not tokens:
            raise Exception("Token list is empty")

        payload_data = random.choice(tokens)

        # Update dynamic fields
        payload_data["app_user_id"] = uid
        if (
            "attributes" in payload_data
            and "$attConsentStatus" in payload_data["attributes"]
        ):
            payload_data["attributes"]["$attConsentStatus"]["updated_at_ms"] = int(
                time.time() * 1000
            )

        payload = json.dumps(payload_data)

        headers = {
            "X-Is-Sandbox": "true",
            "Authorization": "Bearer appl_JngFETzdodyLmCREOlwTUtXdQik",
            "Connection": "keep-alive",
            "Content-Type": "application/json",
        }
        response = _post_with_proxy(url, headers=headers, data=payload, timeout=30)
        print(response.json())
        if response.ok:
            return response.json()
        else:
            raise Exception(
                f"API request failed with status code {response.status_code}: {response.text}"
            )

    def changeNameAccount(self, last="", first=""):
        """Changes the first and last name of the account.

        Args:
            last (str, optional): The new last name. Defaults to "".
            first (str, optional): The new first name. Defaults to "".

        Returns:
            dict: The JSON response from the API if successful.

        Raises:
            Exception: If the API request fails.
        """
        request_payload = {
            "data": {
                "last_name": last,
                "first_name": first,
            }
        }

        response = requests.post(
            "https://api.locketcamera.com/changeProfileInfo",
            headers=self.headers,
            json=request_payload,
        )

        if response.ok:
            return response.json()
        else:
            raise Exception(
                f"API request failed with status code {response.status_code}: {response.text}"
            )

    def GetAccountInfo(self):
        """Gets the account info using the provided token.

        Returns:
            dict: The JSON response from the API if successful.

        Raises:
            Exception: If the API request fails.
        """
        url = "https://www.googleapis.com/identitytoolkit/v3/relyingparty/getAccountInfo?key=AIzaSyCQngaaXQIfJaH0aS2l7REgIjD7nL431So"
        headers = {
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate, br",
            "Accept-Language": "en",
            "Content-Type": "application/json",
            "Host": "www.googleapis.com",
            "User-Agent": "FirebaseAuth.iOS/10.23.1 com.locket.Locket/1.82.0 iPhone/18.0 hw/iPhone12_1",
            "X-Client-Version": "iOS/FirebaseSDK/10.23.1/FirebaseCore-iOS",
            "X-Firebase-GMPID": "1:641029076083:ios:cc8eb46290d69b234fa606",
            "X-Ios-Bundle-Identifier": "com.locket.Locket",
        }
        request_payload = {"idToken": self.token}

        response = requests.post(url, headers=headers, json=request_payload)

        if response.ok:
            return response.json()
        else:
            raise Exception(
                f"API request failed with status code {response.status_code}: {response.text}"
            )

    def getLastMoment(self):
        """Gets the latest moment using the provided token.

        Returns:
            dict: The JSON response from the API if successful.

        Raises:
            Exception: If the API request fails.
        """
        request_payload = {
            "data": {
                "excluded_users": [],
                "fetch_streak": False,
                "should_count_missed_moments": True,
            }
        }

        response = _post_with_proxy(
            "https://api.locketcamera.com/getLatestMomentV2",
            headers=self.headers,
            json_body=request_payload,
            timeout=20,
        )

        if response.ok:
            return response.json()
        else:
            raise Exception(
                f"API request failed with status code {response.status_code}: {response.text}"
            )
