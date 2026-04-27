#!/usr/bin/env python3
"""
UCDP Conflict Data Manager

Downloads, normalizes, stores, and queries UCDP Georeferenced Event Dataset (GED) data.

Requirements:
    - UCDP_API_KEY environment variable must be set
    - API endpoint: https://ucdpapi.pcr.uu.se/api/gedevents/24.1

Usage:
    # Full download and store
    python3 ucdp_manager.py --download --normalize --store

    # Incremental update
    python3 ucdp_manager.py --update

    # Query events
    python3 ucdp_manager.py --query --country "Syria" --start 2024-01-01

    # Conflict summary
    python3 ucdp_manager.py --summary --start 2025-01-01
"""

import argparse
import csv
import json
import os
import sqlite3
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests


# ===============================
# Configuration
# ===============================

API_BASE_URL = "https://ucdpapi.pcr.uu.se/api/gedevents/24.1"
API_KEY = os.environ.get("UCDP_API_KEY")

RAW_DATA_DIR = Path("/mnt/d/data/conflict-data/ucdp/raw")
DATABASE_PATH = Path("/mnt/d/data/conflict-data/ucdp/ucdp.db")

REQUEST_DELAY = 0.2  # Seconds between requests to avoid rate limiting


# ===============================
# Data Models
# ===============================

@dataclass
class UCDPEvent:
    """Normalized UCDP event data."""
    ucdp_id: int
    relid: str
    event_date: str  # ISO format YYYY-MM-DD
    year: int
    country: str
    country_code: str  # ISO 3166-1 alpha-3
    region: str
    latitude: Optional[float]
    longitude: Optional[float]
    fatalities: int
    actor1: Optional[str]
    actor2: Optional[str]
    conflict_name: str
    conflict_type: str
    source: str
    admin1: Optional[str]
    admin2: Optional[str]
    event_type: str
    interaction: Optional[str]
    source_raw: str


# ===============================
# Field Mapping (API -> Normalized)
# ===============================

def normalize_event(raw: Dict[str, Any]) -> UCDPEvent:
    """Normalize raw UCDP API response to standard schema.

    Based on UCDP GED 24.1 API documentation.
    Maps API field names to our normalized schema.
    """
    # Parse date
    event_date = raw.get("date", "")
    year = raw.get("year", 0)

    # Handle fatalities - prefer 'best' estimate, fallback to 'deaths'
    fatalities = int(raw.get("best", raw.get("deaths", 0)))

    # Country code from 'iso' field
    country_code = raw.get("iso", "")

    # Determine conflict type from numeric code
    type_code = raw.get("type_of_conflict", "")
    conflict_type_map = {
        1: "interstate",
        2: "intrastate",
        3: "intrastate",
        4: "internationalized",
    }
    conflict_type = conflict_type_map.get(type_code, "unknown")

    # Helper to get optional string fields (return None if missing/empty)
    def opt_str(key: str) -> Optional[str]:
        val = raw.get(key, "")
        return val if val else None

    return UCDPEvent(
        ucdp_id=int(raw.get("id", 0)),
        relid=raw.get("relid", ""),
        event_date=event_date,
        year=year,
        country=raw.get("country", ""),
        country_code=country_code,
        region=raw.get("region", ""),
        latitude=_parse_float(raw.get("latitude")),
        longitude=_parse_float(raw.get("longitude")),
        fatalities=fatalities,
        actor1=opt_str("actor_name"),
        actor2=opt_str("actor2_name") or opt_str("dyad_name"),
        conflict_name=raw.get("conflict_name", ""),
        conflict_type=conflict_type,
        source=raw.get("source", ""),
        admin1=opt_str("adm_level_1"),
        admin2=opt_str("adm_level_2"),
        event_type=raw.get("type_of Violence", ""),
        interaction=opt_str("interaction"),
        source_raw=raw.get("source", ""),
    )


def _parse_float(value: Any) -> Optional[float]:
    """Safely parse a float from API response."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


# ===============================
# API Client
# ===============================

class UCDPAPIClient:
    """Client for UCDP GED API."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or API_KEY
        if not self.api_key:
            raise ValueError(
                "UCDP_API_KEY not found. Set environment variable or pass api_key parameter."
            )
        self.headers = {"x-ucdp-access-token": self.api_key}
        self.session = requests.Session()

    def fetch_all(
        self,
        event_id: Optional[int] = None,
        start_date: Optional[str] = None,
        progress: bool = True,
    ) -> List[Dict[str, Any]]:
        """Fetch all events, optionally filtered by ID or date range.

        Args:
            event_id: Minimum event ID for incremental updates
            start_date: Start date (YYYY-MM-DD) for filtering
            progress: Show progress bar

        Returns:
            List of raw event dictionaries
        """
        all_events = []
        page = 0
        page_size = 1000  # Maximum pagesize for UCDP API

        while True:
            params = {"page": page, "pagesize": page_size}

            # Add filters for incremental update
            if event_id is not None:
                # Use ID filter if available
                params["id"] = f"{event_id}+"

            if start_date:
                # Use date range filter
                params.update({"StartDate": start_date, "EndDate": "2100-01-01"})

            try:
                response = self.session.get(
                    API_BASE_URL, headers=self.headers, params=params
                )
                response.raise_for_status()
                data = response.json()
            except requests.RequestException as e:
                print(f"Error fetching page {page}: {e}", file=sys.stderr)
                break

            events = data.get("Result", [])
            if not events:
                break

            all_events.extend(events)

            if progress:
                print(
                    f"\rFetched {len(all_events)} events...",
                    end="",
                    flush=True,
                )

            # Check if we've got all results
            total_count = data.get("TotalCount", data.get("Count", 0))
            if len(all_events) >= total_count or len(events) < page_size:
                break

            page += 1
            time.sleep(REQUEST_DELAY)

        if progress:
            print(f"\rFetched {len(all_events)} events total.")

        return all_events


# ===============================
# Database Manager
# ===============================

class UCDPDatabase:
    """SQLite database manager for UCDP events."""

    def __init__(self, db_path: Path = DATABASE_PATH):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn: Optional[sqlite3.Connection] = None

    def connect(self) -> sqlite3.Connection:
        """Get database connection."""
        if self.conn is None:
            self.conn = sqlite3.connect(self.db_path)
            self.conn.row_factory = sqlite3.Row
        return self.conn

    def close(self):
        """Close database connection."""
        if self.conn:
            self.conn.close()
            self.conn = None

    def create_schema(self):
        """Create database schema."""
        conn = self.connect()
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ged_events (
                ucdp_id INTEGER PRIMARY KEY,
                relid TEXT,
                event_date TEXT,
                year INTEGER,
                country TEXT,
                country_code TEXT,
                region TEXT,
                latitude REAL,
                longitude REAL,
                fatalities INTEGER,
                actor1 TEXT,
                actor2 TEXT,
                conflict_name TEXT,
                conflict_type TEXT,
                source TEXT,
                admin1 TEXT,
                admin2 TEXT,
                event_type TEXT,
                interaction TEXT,
                source_raw TEXT
            )
        """
        )

        # Create indexes
        indexes = [
            "CREATE INDEX IF NOT EXISTS idx_event_date ON ged_events(event_date)",
            "CREATE INDEX IF NOT EXISTS idx_country_code ON ged_events(country_code)",
            "CREATE INDEX IF NOT EXISTS idx_conflict_type ON ged_events(conflict_type)",
            "CREATE INDEX IF NOT EXISTS idx_year ON ged_events(year)",
            "CREATE INDEX IF NOT EXISTS idx_lat_lon ON ged_events(latitude, longitude)",
            "CREATE INDEX IF NOT EXISTS idx_fatalities ON ged_events(fatalities)",
        ]

        for idx_sql in indexes:
            conn.execute(idx_sql)

        # Create metadata table
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """
        )

        conn.commit()

    def upsert_events(self, events: List[UCDPEvent]) -> int:
        """Insert or replace events.

        Returns:
            Number of events upserted
        """
        if not events:
            return 0

        conn = self.connect()
        cursor = conn.cursor()

        upsert_sql = """
            INSERT OR REPLACE INTO ged_events (
                ucdp_id, relid, event_date, year, country, country_code,
                region, latitude, longitude, fatalities, actor1, actor2,
                conflict_name, conflict_type, source, admin1, admin2,
                event_type, interaction, source_raw
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """

        count = 0
        for event in events:
            cursor.execute(
                upsert_sql,
                (
                    event.ucdp_id,
                    event.relid,
                    event.event_date,
                    event.year,
                    event.country,
                    event.country_code,
                    event.region,
                    event.latitude,
                    event.longitude,
                    event.fatalities,
                    event.actor1,
                    event.actor2,
                    event.conflict_name,
                    event.conflict_type,
                    event.source,
                    event.admin1,
                    event.admin2,
                    event.event_type,
                    event.interaction,
                    event.source_raw,
                ),
            )
            count += 1

        conn.commit()
        return count

    def get_max_event_id(self) -> Optional[int]:
        """Get the maximum event ID in database."""
        conn = self.connect()
        cursor = conn.cursor()
        cursor.execute("SELECT MAX(ucdp_id) FROM ged_events")
        result = cursor.fetchone()
        return result[0] if result and result[0] else None

    def get_latest_event_date(self) -> Optional[str]:
        """Get the latest event date in database."""
        conn = self.connect()
        cursor = conn.cursor()
        cursor.execute("SELECT MAX(event_date) FROM ged_events")
        result = cursor.fetchone()
        return result[0] if result and result[0] else None

    def set_last_update(self, timestamp: str):
        """Update last_update timestamp in metadata."""
        conn = self.connect()
        conn.execute(
            "INSERT OR REPLACE INTO metadata (key, value) VALUES ('last_update', ?)",
            (timestamp,),
        )
        conn.commit()

    def get_last_update(self) -> Optional[str]:
        """Get last_update timestamp from metadata."""
        conn = self.connect()
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM metadata WHERE key = 'last_update'")
        result = cursor.fetchone()
        return result[0] if result else None

    def query_events(
        self,
        country: Optional[str] = None,
        country_code: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        conflict_type: Optional[str] = None,
        min_fatalities: int = 0,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Query events with filters.

        Returns:
            List of event dictionaries
        """
        conn = self.connect()
        cursor = conn.cursor()

        query = "SELECT * FROM ged_events WHERE 1=1"
        params = []

        if country:
            query += " AND country LIKE ?"
            params.append(f"%{country}%")

        if country_code:
            query += " AND country_code = ?"
            params.append(country_code.upper())

        if start_date:
            query += " AND event_date >= ?"
            params.append(start_date)

        if end_date:
            query += " AND event_date <= ?"
            params.append(end_date)

        if conflict_type:
            query += " AND conflict_type = ?"
            params.append(conflict_type)

        if min_fatalities > 0:
            query += " AND fatalities >= ?"
            params.append(min_fatalities)

        query += " ORDER BY event_date DESC LIMIT ?"
        params.append(limit)

        cursor.execute(query, params)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

    def query_by_region(
        self,
        lat: float,
        lon: float,
        radius_km: float,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Query events within radius of a point.

        Uses Haversine formula approximation with bounding box for efficiency.

        Returns:
            List of event dictionaries
        """
        conn = self.connect()
        cursor = conn.cursor()

        # Approximate bounding box (1 degree ≈ 111 km)
        lat_delta = radius_km / 111.0
        lon_delta = radius_km / (111.0 * abs(lat) / 90.0) if lat != 0 else radius_km / 111.0

        query = """
            SELECT * FROM ged_events
            WHERE latitude BETWEEN ? AND ?
              AND longitude BETWEEN ? AND ?
        """
        params = [lat - lat_delta, lat + lat_delta, lon - lon_delta, lon + lon_delta]

        if start_date:
            query += " AND event_date >= ?"
            params.append(start_date)

        if end_date:
            query += " AND event_date <= ?"
            params.append(end_date)

        query += " ORDER BY event_date DESC LIMIT ?"
        params.append(limit)

        cursor.execute(query, params)
        rows = cursor.fetchall()

        # Filter by exact distance (Haversine formula)
        import math

        def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
            """Calculate distance in km between two points."""
            R = 6371.0
            lat1_rad = math.radians(lat1)
            lat2_rad = math.radians(lat2)
            dlat = math.radians(lat2 - lat1)
            dlon = math.radians(lon2 - lon1)

            a = (
                math.sin(dlat / 2) ** 2
                + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2) ** 2
            )
            c = 2 * math.asin(math.sqrt(a))
            return R * c

        # Apply exact distance filter in Python
        results = []
        for row in rows:
            row_dict = dict(row)
            if row_dict["latitude"] and row_dict["longitude"]:
                distance = haversine(
                    lat, lon, row_dict["latitude"], row_dict["longitude"]
                )
                if distance <= radius_km:
                    row_dict["_distance_km"] = distance
                    results.append(row_dict)

        return sorted(results, key=lambda x: x["_distance_km"])

    def get_conflict_summary(
        self, start_date: Optional[str] = None, end_date: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get conflict statistics summary.

        Returns:
            Dictionary with stats by country, conflict type, year, etc.
        """
        conn = self.connect()
        cursor = conn.cursor()

        where_clause = "WHERE 1=1"
        params = []

        if start_date:
            where_clause += " AND event_date >= ?"
            params.append(start_date)

        if end_date:
            where_clause += " AND event_date <= ?"
            params.append(end_date)

        # Total events and fatalities
        cursor.execute(
            f"""
            SELECT COUNT(*) as total_events,
                   COALESCE(SUM(fatalities), 0) as total_fatalities,
                   COUNT(DISTINCT country) as total_countries
            FROM ged_events
            {where_clause}
        """,
            params,
        )
        basic_stats = dict(cursor.fetchone())

        # By country
        cursor.execute(
            f"""
            SELECT country, country_code,
                   COUNT(*) as events,
                   SUM(fatalities) as fatalities
            FROM ged_events
            {where_clause}
            GROUP BY country, country_code
            ORDER BY fatalities DESC
            LIMIT 20
        """,
            params,
        )
        by_country = [dict(row) for row in cursor.fetchall()]

        # By conflict type
        cursor.execute(
            f"""
            SELECT conflict_type,
                   COUNT(*) as events,
                   SUM(fatalities) as fatalities
            FROM ged_events
            {where_clause}
            GROUP BY conflict_type
            ORDER BY events DESC
        """,
            params,
        )
        by_type = [dict(row) for row in cursor.fetchall()]

        # By year
        cursor.execute(
            f"""
            SELECT year,
                   COUNT(*) as events,
                   SUM(fatalities) as fatalities
            FROM ged_events
            {where_clause}
            GROUP BY year
            ORDER BY year DESC
            LIMIT 10
        """,
            params,
        )
        by_year = [dict(row) for row in cursor.fetchall()]

        return {
            "basic": basic_stats,
            "by_country": by_country,
            "by_conflict_type": by_type,
            "by_year": by_year,
        }


# ===============================
# File Storage
# ===============================

def save_raw_json(events: List[Dict[str, Any]], filename: str):
    """Save raw events to JSON file."""
    RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)
    filepath = RAW_DATA_DIR / filename
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(events, f, ensure_ascii=False, indent=2)
    print(f"Saved {len(events)} events to {filepath}")


def load_raw_json(filename: str) -> List[Dict[str, Any]]:
    """Load raw events from JSON file."""
    filepath = RAW_DATA_DIR / filename
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


# ===============================
# CLI
# ===============================

def cmd_download(args):
    """Download data from API."""
    client = UCDPAPIClient()

    print("Downloading data from UCDP API...")
    events = client.fetch_all(progress=True)

    if not events:
        print("No events downloaded.")
        return

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"ucdp_raw_{timestamp}.json"

    save_raw_json(events, filename)
    print(f"Downloaded {len(events)} events.")


def cmd_normalize(args):
    """Normalize raw JSON data."""
    # Find latest raw file
    raw_files = sorted(RAW_DATA_DIR.glob("ucdp_raw_*.json"), reverse=True)
    if not raw_files:
        print("No raw data files found. Run --download first.")
        return 1

    raw_file = raw_files[0]
    print(f"Normalizing {raw_file.name}...")

    raw_events = load_raw_json(raw_file.name)
    normalized = [normalize_event(e) for e in raw_events]

    # Save normalized data
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"ucdp_normalized_{timestamp}.json"
    filepath = RAW_DATA_DIR / filename

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump([asdict(e) for e in normalized], f, ensure_ascii=False, indent=2)

    print(f"Normalized {len(normalized)} events to {filepath}")
    return 0


def cmd_store(args):
    """Store normalized data to database."""
    # Find latest normalized file
    norm_files = sorted(RAW_DATA_DIR.glob("ucdp_normalized_*.json"), reverse=True)
    if not norm_files:
        print("No normalized data files found. Run --normalize first.")
        return 1

    norm_file = norm_files[0]
    print(f"Loading {norm_file.name}...")

    with open(norm_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    db = UCDPDatabase()
    db.create_schema()

    events = [UCDPEvent(**e) for e in data]
    count = db.upsert_events(events)

    timestamp = datetime.now().isoformat()
    db.set_last_update(timestamp)

    print(f"Stored {count} events to {db.db_path}")
    print(f"Last update: {timestamp}")
    return 0


def cmd_update(args):
    """Incremental update from API."""
    client = UCDPAPIClient()
    db = UCDPDatabase()

    db.create_schema()

    # Get last update info
    max_id = db.get_max_event_id()
    last_date = db.get_latest_event_date()

    print(f"Current max event_id: {max_id}, latest date: {last_date}")

    # Fetch new events (use ID filter for efficiency)
    print("Fetching new events...")
    if max_id:
        new_events = client.fetch_all(event_id=max_id + 1, progress=True)
    else:
        new_events = client.fetch_all(progress=True)

    if not new_events:
        print("No new events to add.")
        return 0

    print(f"Normalizing {len(new_events)} events...")
    normalized = [normalize_event(e) for e in new_events]

    print("Storing to database...")
    count = db.upsert_events(normalized)

    timestamp = datetime.now().isoformat()
    db.set_last_update(timestamp)

    print(f"Added/updated {count} events.")
    print(f"Last update: {timestamp}")
    return 0


def cmd_query(args):
    """Query events from database."""
    db = UCDPDatabase()

    results = db.query_events(
        country=args.country,
        start_date=args.start,
        end_date=args.end,
        conflict_type=args.conflict_type,
        min_fatalities=args.min_fatalities or 0,
        limit=args.limit or 100,
    )

    if not results:
        print("No events found matching criteria.")
        return 0

    print(f"\nFound {len(results)} events:\n")
    print(json.dumps(results, indent=2, ensure_ascii=False))
    return 0


def cmd_region_query(args):
    """Query events by geographic region."""
    db = UCDPDatabase()

    results = db.query_by_region(
        lat=args.lat,
        lon=args.lon,
        radius_km=args.radius or 50,
        start_date=args.start,
        end_date=args.end,
        limit=args.limit or 100,
    )

    if not results:
        print("No events found in this region.")
        return 0

    print(f"\nFound {len(results)} events within {args.radius or 50}km:\n")
    for r in results:
        dist = r.pop("_distance_km", None)
        print(f"[{dist:.1f}km] {r.get('event_date')}: {r.get('country')} - {r.get('conflict_name')} ({r.get('fatalities')} fatalities)")

    return 0


def cmd_summary(args):
    """Show conflict summary statistics."""
    db = UCDPDatabase()

    summary = db.get_conflict_summary(
        start_date=args.start,
        end_date=args.end,
    )

    print("\n=== UCDP Conflict Summary ===\n")

    basic = summary["basic"]
    print(f"Total Events: {basic['total_events']:,}")
    print(f"Total Fatalities: {basic['total_fatalities']:,}")
    print(f"Countries Affected: {basic['total_countries']}")

    print("\n--- Top 20 Countries by Fatalities ---")
    print(f"{'Country':<30} {'Events':>10} {'Fatalities':>12}")
    print("-" * 54)
    for c in summary["by_country"]:
        print(f"{c['country']:<30} {c['events']:>10,} {c['fatalities']:>12,}")

    print("\n--- By Conflict Type ---")
    print(f"{'Type':<20} {'Events':>10} {'Fatalities':>12}")
    print("-" * 44)
    for t in summary["by_conflict_type"]:
        print(f"{t['conflict_type']:<20} {t['events']:>10,} {t['fatalities']:>12,}")

    print("\n--- Recent Years ---")
    print(f"{'Year':<8} {'Events':>10} {'Fatalities':>12}")
    print("-" * 32)
    for y in summary["by_year"]:
        print(f"{y['year']:<8} {y['events']:>10,} {y['fatalities']:>12,}")

    print()

    return 0


def cmd_status(args):
    """Show database status."""
    db = UCDPDatabase()
    conn = db.connect()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM ged_events")
    total = cursor.fetchone()[0]

    cursor.execute("SELECT MIN(event_date), MAX(event_date) FROM ged_events")
    min_date, max_date = cursor.fetchone()

    cursor.execute("SELECT MAX(ucdp_id) FROM ged_events")
    max_id = cursor.fetchone()[0]

    last_update = db.get_last_update()

    print("\n=== UCDP Database Status ===\n")
    print(f"Database: {db.db_path}")
    print(f"Total Events: {total:,}")
    print(f"Date Range: {min_date} to {max_date}")
    print(f"Max Event ID: {max_id}")
    print(f"Last Update: {last_update or 'Never'}")
    print()

    return 0


def main():
    parser = argparse.ArgumentParser(
        description="UCDP Conflict Data Manager",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # Operation modes (mutually exclusive)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--download", action="store_true", help="Download raw data from API")
    group.add_argument("--normalize", action="store_true", help="Normalize raw JSON data")
    group.add_argument("--store", action="store_true", help="Store normalized data to database")
    group.add_argument("--update", action="store_true", help="Incremental update from API")
    group.add_argument("--query", action="store_true", help="Query events")
    group.add_argument("--region", action="store_true", help="Query by geographic region")
    group.add_argument("--summary", action="store_true", help="Show conflict summary")
    group.add_argument("--status", action="store_true", help="Show database status")

    # Query filters
    parser.add_argument("--country", help="Filter by country name")
    parser.add_argument("--start", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", help="End date (YYYY-MM-DD)")
    parser.add_argument("--conflict-type", help="Filter by conflict type")
    parser.add_argument("--min-fatalities", type=int, help="Minimum fatalities")
    parser.add_argument("--limit", type=int, help="Limit results")

    # Region query
    parser.add_argument("--lat", type=float, help="Latitude for region query")
    parser.add_argument("--lon", type=float, help="Longitude for region query")
    parser.add_argument("--radius", type=int, help="Radius in km (default: 50)")

    args = parser.parse_args()

    # Route to appropriate handler
    if args.download:
        return cmd_download(args)
    elif args.normalize:
        return cmd_normalize(args)
    elif args.store:
        return cmd_store(args)
    elif args.update:
        return cmd_update(args)
    elif args.query:
        return cmd_query(args)
    elif args.region:
        return cmd_region_query(args)
    elif args.summary:
        return cmd_summary(args)
    elif args.status:
        return cmd_status(args)

    return 0


if __name__ == "__main__":
    sys.exit(main())
