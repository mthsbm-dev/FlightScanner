import requests
from typing import List, Dict, Any
from amadeus import Client, ResponseError
from .destinations import ALL_DESTINATIONS


class AmadeusClient:
    def __init__(self, client_id: str, client_secret: str, max_destinations: int = 30):
        self.client = Client(
            client_id=client_id,
            client_secret=client_secret
        )
        self.max_destinations = max_destinations  # Rate limiting protection

    def search(self, fly_from: List[str], date_from: str, date_to: str, currency: str = "EUR", price_to: int = 1800, limit: int = 20, cabin: str | None = None, max_stopovers: int | None = None) -> List[Dict[str, Any]]:
        """
        Search for flights using Amadeus API.
        
        Searches from each origin to all configured destinations.
        """
        results = []
        destinations = ALL_DESTINATIONS[:self.max_destinations]
        
        travel_class = 'BUSINESS' if cabin == 'C' else 'ECONOMY'
        
        # Search from each origin to each destination
        # Track only cheapest flight per route
        cheapest_by_route = {}  # (origin, destination) -> flight
        
        for origin in fly_from:
            print(f"Searching from {origin}...", flush=True)
            for dest in destinations:
                try:
                    response = self.client.shopping.flight_offers_search.get(
                        originLocationCode=origin,
                        destinationLocationCode=dest,
                        departureDate=date_from.replace('/', '-'),
                        returnDate=date_to.replace('/', '-'),
                        adults=1,
                        maxPrice=price_to,
                        max=limit,
                        travelClass=travel_class
                    )
                    if response.data:
                        for offer in response.data:
                            normalized = self._normalize_offer(offer, origin)
                            if normalized:
                                route_key = (origin, normalized.get('cityTo', ''))
                                existing = cheapest_by_route.get(route_key)
                                if existing is None or float(normalized.get('price', float('inf'))) < float(existing.get('price', float('inf'))):
                                    cheapest_by_route[route_key] = normalized
                except ResponseError as e:
                    # Silently skip errors (rate limits, etc.)
                    continue
                except Exception as e:
                    continue
        
        results = list(cheapest_by_route.values())
        
        # Show progress after each origin completes
        print(f"  -> Found {len(results)} flights so far from {origin}", flush=True)
        
        return results

    def _normalize_offer(self, offer: dict, origin: str) -> dict:
        """
        Normalize Amadeus response to FlightScanner format.
        """
        try:
            price = offer.get('price', {}).get('total', '0')
            currency = offer.get('price', {}).get('currency', 'EUR')
            
            # info Extract itinerary
            itineraries = offer.get('itineraries', [])
            first_itinerary = itineraries[0] if itineraries else {}
            segments = first_itinerary.get('segments', [])
            
            # Get destination from first segment
            first_segment = segments[0] if segments else {}
            last_segment = segments[-1] if segments else {}
            
            return {
                'price': float(price),
                'currency': currency,
                'cityFrom': first_segment.get('departure', {}).get('iataCode', origin),
                'cityTo': last_segment.get('arrival', {}).get('iataCode', 'UNK'),
                'countryTo': {'name': last_segment.get('arrival', {}).get('iataCode', 'Unknown')},
                'duration': {'total': self._parse_duration(first_itinerary.get('duration', 'PT0M'))},
                'route': [self._normalize_segment(s) for s in segments],
                'deep_link': '',  # Amadeus doesn't provide deep links in offers
                'booking_token': offer.get('id', ''),
            }
        except Exception as e:
            print(f"Error normalizing offer: {e}", flush=True)
            return {}

    def _normalize_segment(self, segment: dict) -> dict:
        """Normalize a flight segment."""
        return {
            'cityFrom': segment.get('departure', {}).get('iataCode', ''),
            'cityTo': segment.get('arrival', {}).get('iataCode', ''),
            'airline': segment.get('carrierCode', ''),
            'aircraft': segment.get('aircraft', {}).get('code', ''),
            'flightNo': f"{segment.get('carrierCode', '')}{segment.get('number', '')}",
        }

    def _parse_duration(self, duration: str) -> int:
        """Parse ISO 8601 duration to seconds."""
        # Example: PT2H30M -> 9000 seconds
        import re
        match = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?', duration)
        if match:
            hours = int(match.group(1) or 0)
            minutes = int(match.group(2) or 0)
            return (hours * 60 + minutes) * 60
        return 0
