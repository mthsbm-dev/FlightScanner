import requests
from typing import List, Dict, Any


class TequilaClient:
    BASE = "https://tequila-api.kiwi.com/v2/search"

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update({"apikey": api_key})

    def search(self, fly_from: List[str], date_from: str, date_to: str, currency: str = "EUR", price_to: int = 1800, limit: int = 20, cabin: str | None = None, max_stopovers: int | None = None) -> List[Dict[str, Any]]:
        params = {
            "fly_from": ",".join(fly_from),
            "fly_to": "anywhere",
            "date_from": date_from,
            "date_to": date_to,
            "curr": currency,
            "price_to": price_to,
            "limit": limit,
            "one_for_city": 1,
        }
        if cabin:
            # Tequila supports 'selected_cabins' or 'cabin' depending on API; include common param
            params["cabin"] = cabin
            params["selected_cabins"] = cabin
        if max_stopovers is not None:
            # max_stopovers applies per direction; Tequila param often 'max_stopovers' or 'max_stopover'
            params["max_stopovers"] = max_stopovers
        resp = self.session.get(self.BASE, params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        return data.get("data", [])
