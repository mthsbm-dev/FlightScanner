# Interesting destination cities for flight searches
# Organized by region

DESTINATIONS = {
    # Canada
    "canada": [
        "YVR",  # Vancouver
        "YOW",  # Ottawa
        "YHZ",  # Halifax
        "YYZ",  # Toronto
    ],
    # Mexico
    "mexico": [
        "MEX",  # Mexico City
        "CUN",  # Cancun
    ],
    # Caribbean
    "caribbean": [
        "PTP",  # Pointe-à-Pitre, Guadeloupe
        "FDF",  # Fort-de-France, Martinique
        "PUJ",  # Punta Cana, Dominican Republic
        "MBJ",  # Montego Bay, Jamaica
    ],
    # South America (main capitals)
    "south_america": [
        "GRU",  # Sao Paulo, Brazil
        "EZE",  # Buenos Aires, Argentina
        "SCL",  # Santiago, Chile
        "BOG",  # Bogota, Colombia
        "LIM",  # Lima, Peru
    ],
    # Central & Southeast Asia (capitals)
    "asia": [
        "NRT",  # Tokyo, Japan
        "ICN",  # Seoul, South Korea
        "SIN",  # Singapore
        "BKK",  # Bangkok, Thailand
        "KUL",  # Kuala Lumpur, Malaysia
        "MNL",  # Manila, Philippines
        "HAN",  # Hanoi, Vietnam
        "JKT",  # Jakarta, Indonesia
    ],
}

# Flat list of all destinations
ALL_DESTINATIONS = []
for region in DESTINATIONS.values():
    ALL_DESTINATIONS.extend(region)

# Remove duplicates while preserving order
seen = set()
unique_destinations = []
for code in ALL_DESTINATIONS:
    if code not in seen:
        seen.add(code)
        unique_destinations.append(code)

ALL_DESTINATIONS = unique_destinations
