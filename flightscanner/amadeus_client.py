import requests
from typing import List, Dict, Any
from amadeus import Client, ResponseError
from .destinations import ALL_DESTINATIONS, IATA_TO_CITY
from bson import ObjectId

# MongoDB logging for Amadeus API calls
try:
    from pymongo import MongoClient as MongoClientPy
    MONGO_AVAILABLE = True
except ImportError:
    MONGO_AVAILABLE = False

def get_amadeus_log_col():
    """Get MongoDB collection for Amadeus logging."""
    if not MONGO_AVAILABLE:
        return None
    try:
        client = MongoClientPy("mongodb://localhost:27017")
        col = client["openclaw"]["amadeus_calls"]
        col.create_index("timestamp")
        col.create_index("endpoint")
        return col
    except Exception:
        return None

def log_amadeus_call(endpoint: str, params: dict, response_status: str, error: str = None, response_data: dict = None):
    """Log Amadeus API call to MongoDB."""
    from datetime import datetime, timezone
    col = get_amadeus_log_col()
    if col is None:
        return
    
    # Flatten params to top-level fields
    flat_params = {f"param_{k}": str(v)[:150] for k, v in params.items()}
    
    # Build entry with flattened params + response
    entry = {
        "_id": str(ObjectId()),
        "timestamp": datetime.now(timezone.utc),
        "endpoint": endpoint,
        "response_status": response_status,
        "error": error[:300] if error else None,
    }
    # Add flattened params
    entry.update(flat_params)
    # Add response summary (first few offers)
    if response_data:
        offers = response_data.get('data', []) if isinstance(response_data, dict) else []
        entry["offer_count"] = len(offers)
        if offers:
            # Extract key info from first offer
            first = offers[0]
            entry["price_total"] = first.get('price', {}).get('total')
            entry["price_currency"] = first.get('price', {}).get('currency')
            entry["first_segment_dep"] = first.get('itineraries', [{}])[0].get('segments', [{}])[0].get('departure', {}).get('iataCode') if first.get('itineraries') else None
            entry["first_segment_arr"] = first.get('itineraries', [{}])[0].get('segments', [{}])[-1].get('arrival', {}).get('iataCode') if first.get('itineraries') else None
    
    col.insert_one(entry)


class AmadeusClient:
    def __init__(self, client_id: str, client_secret: str, max_destinations: int = 30, destinations: List[str] = None):
        self.client = Client(
            client_id=client_id,
            client_secret=client_secret
        )
        self.max_destinations = max_destinations  # Rate limiting protection
        # Use provided destinations or fall back to ALL_DESTINATIONS
        self.destinations = destinations if destinations else ALL_DESTINATIONS[:max_destinations]

    def search_flexible_dates(self, fly_from: List[str], date_from: str, date_to: str, currency: str = "EUR", price_to: int = 1800, limit: int = 20, cabin: str | None = None, max_stopovers: int | None = None, destinations: List[str] = None) -> List[Dict[str, Any]]:
        """
        Search for flights by splitting the date range into months and combining results.
        
        This is a fallback when Flight Dates API is not available.
        """
        from datetime import datetime
        results = []
        # Use provided destinations or fall back to default
        search_dests = destinations if destinations else self.destinations
        travel_class = 'BUSINESS' if cabin == 'C' else 'ECONOMY'
        
        # Parse date range and split into monthly chunks
        dep_start = datetime.strptime(date_from.replace('/', '-'), '%Y-%m-%d')
        dep_end = datetime.strptime(date_to.replace('/', '-'), '%Y-%m-%d')
        
        # Generate monthly ranges
        month_ranges = []
        current = dep_start
        while current <= dep_end:
            month_end = datetime(current.year, current.month, 28)  # Safe upper bound
            if month_end > dep_end:
                month_end = dep_end
            month_ranges.append((current.strftime('%Y-%m-%d'), month_end.strftime('%Y-%m-%d')))
            # Move to next month
            if current.month == 12:
                current = datetime(current.year + 1, 1, 1)
            else:
                current = datetime(current.year, current.month + 1, 1)
        
        print(f"  Searching {len(month_ranges)} month ranges: {month_ranges}", flush=True)
        
        cheapest_by_route = {}  # (origin, destination) -> flight
        
        for origin in fly_from:
            print(f"Searching from {origin} (monthly split)...", flush=True)
            for dest in search_dests:
                best_price = float('inf')
                best_flight = None
                
                # Search each month range
                for month_from, month_to in month_ranges:
                    params = {
                        'originLocationCode': origin,
                        'destinationLocationCode': dest,
                        'departureDate': month_from,
                        'returnDate': month_to,
                        'adults': 1,
                        'maxPrice': price_to,
                        'max': limit,
                        'travelClass': travel_class
                    }
                    try:
                        response = self.client.shopping.flight_offers_search.get(**params)
                        response_dict = {'data': response.data} if response.data else {'data': []}
                        log_amadeus_call("flight_offers_search", params, "success", response_data=response_dict)
                        
                        if response.data:
                            for offer in response.data:
                                normalized = self._normalize_offer(offer, origin)
                                if normalized:
                                    price = float(normalized.get('price', float('inf')))
                                    if price < best_price:
                                        best_price = price
                                        best_flight = normalized
                    except ResponseError as e:
                        log_amadeus_call("flight_offers_search", params, "error", str(e))
                    except Exception as e:
                        log_amadeus_call("flight_offers_search", params, "error", str(e))
                        continue
                
                if best_flight:
                    route_key = (origin, best_flight.get('cityTo', ''))
                    existing = cheapest_by_route.get(route_key)
                    if existing is None or best_price < float(existing.get('price', float('inf'))):
                        cheapest_by_route[route_key] = best_flight
        
        results = list(cheapest_by_route.values())
        print(f"  -> Found {len(results)} flights from {origin}", flush=True)
        return results

    def search(self, fly_from: List[str], date_from: str, date_to: str, currency: str = "EUR", price_to: int = 1800, limit: int = 20, cabin: str | None = None, max_stopovers: int | None = None, destinations: List[str] = None) -> List[Dict[str, Any]]:
        """
        Search for flights using Amadeus API.
        
        Searches from each origin to all configured destinations.
        """
        results = []
        # Use provided destinations or fall back to default
        search_dests = destinations if destinations else self.destinations
        
        travel_class = 'BUSINESS' if cabin == 'C' else 'ECONOMY'
        
        # Search from each origin to each destination
        # Track only cheapest flight per route
        cheapest_by_route = {}  # (origin, destination) -> flight
        
        for origin in fly_from:
            print(f"Searching from {origin}...", flush=True)
            for dest in search_dests:
                params = {
                    'originLocationCode': origin,
                    'destinationLocationCode': dest,
                    'departureDate': date_from.replace('/', '-'),
                    'returnDate': date_to.replace('/', '-'),
                    'adults': 1,
                    'maxPrice': price_to,
                    'max': limit,
                    'travelClass': travel_class
                }
                try:
                    response = self.client.shopping.flight_offers_search.get(**params)
                    # Convert response to dict for logging - wrap the data list
                    response_dict = {'data': response.data} if response.data else {'data': []}
                    log_amadeus_call("flight_offers_search", params, "success", response_data=response_dict)
                    if response.data:
                        for offer in response.data:
                            normalized = self._normalize_offer(offer, origin)
                            if normalized:
                                route_key = (origin, normalized.get('cityTo', ''))
                                existing = cheapest_by_route.get(route_key)
                                if existing is None or float(normalized.get('price', float('inf'))) < float(existing.get('price', float('inf'))):
                                    cheapest_by_route[route_key] = normalized
                except ResponseError as e:
                    log_amadeus_call("flight_offers_search", params, "error", str(e))
                    # Silently skip errors (rate limits, etc.)
                except Exception as e:
                    log_amadeus_call("flight_offers_search", params, "error", str(e))
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
            
            city_from = first_segment.get('departure', {}).get('iataCode', origin)
            city_to = last_segment.get('arrival', {}).get('iataCode', 'UNK')

            # Get return itinerary (second itinerary is the return flight)
            return_itinerary = itineraries[1] if len(itineraries) > 1 else {}
            return_segments = return_itinerary.get('segments', [])
            return_segment = return_segments[0] if return_segments else {}
            
            # Extract return date from first segment of return itinerary
            ret_date = return_segment.get('departure', {}).get('at', '') if return_segment else ''

            return {
                'price': float(price),
                'currency': currency,
                'cityFrom': city_from,
                'cityTo': city_to,
                'cityFromName': IATA_TO_CITY.get(city_from, city_from),
                'cityToName': IATA_TO_CITY.get(city_to, city_to),
                'countryTo': {'name': last_segment.get('arrival', {}).get('iataCode', 'Unknown')},
                'duration': {'total': self._parse_duration(first_itinerary.get('duration', 'PT0M'))},
                'route': [self._normalize_segment(s) for s in segments],
                'deep_link': '',  # Amadeus doesn't provide deep links in offers
                'booking_token': offer.get('id', ''),
                # Add departure and return dates
                'dep_date': first_segment.get('departure', {}).get('at', ''),
                'ret_date': ret_date,
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
