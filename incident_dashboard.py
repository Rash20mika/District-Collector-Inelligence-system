import hashlib
import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


INCIDENT_CATEGORIES = {
    "flood_rain", "road_accident", "fire", "water_supply",
    "infrastructure", "health", "crime_law", "protest",
}
ACTIVE_STATUSES = {"active", "open", "unresolved", "in_progress"}
CRITICAL_SEVERITIES = {"critical", "high"}
TRACKING_QUERY_KEYS = {"fbclid", "gclid"}


def normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())


def match_district(text: str, districts: Dict[str, List[str]]) -> Optional[str]:
    normalized_text = str(text or "").casefold()
    matches = []
    for district, aliases in districts.items():
        for alias in [district, *(aliases or [])]:
            if alias and re.search(rf"(?<!\w){re.escape(alias.casefold())}(?!\w)", normalized_text):
                matches.append((len(alias), district))
    return max(matches)[1] if matches else None


def validate_boundary_geojson(
    payload: Any, districts: Dict[str, List[str]]
) -> Tuple[List[Dict[str, Any]], List[str]]:
    if not isinstance(payload, dict) or payload.get("type") != "FeatureCollection":
        raise ValueError("The configured boundary source did not return a GeoJSON FeatureCollection.")

    features = payload.get("features")
    if not isinstance(features, list):
        raise ValueError("The boundary GeoJSON does not contain a feature list.")

    aliases = {
        normalize_name(alias): name
        for name, values in districts.items()
        for alias in [name, *(values or [])]
    }
    matched: Dict[str, Dict[str, Any]] = {}
    vintages = set()
    for feature in features:
        if not isinstance(feature, dict) or feature.get("type") != "Feature":
            continue
        properties = feature.get("properties") or {}
        raw_name = next(
            (properties.get(key) for key in ("dtname", "dist", "district", "shapeName", "NAME_2")
             if properties.get(key)),
            None,
        )
        district = aliases.get(normalize_name(raw_name)) if raw_name else None
        geometry = feature.get("geometry")
        if not district or not isinstance(geometry, dict):
            continue
        if geometry.get("type") not in {"Polygon", "MultiPolygon"}:
            raise ValueError(f"{district} has an unsupported geometry type.")
        if not geometry.get("coordinates"):
            raise ValueError(f"{district} has no boundary coordinates.")
        if district in matched:
            raise ValueError(f"The boundary source contains duplicate features for {district}.")
        normalized_feature = {
            "type": "Feature",
            "geometry": geometry,
            "properties": {"district": district},
        }
        matched[district] = normalized_feature
        for key in ("year_stat", "year", "boundaryYear"):
            if properties.get(key):
                vintages.add(str(properties[key]))

    missing = sorted(set(districts) - set(matched))
    extra_count = len(matched)
    if missing or extra_count != 38:
        details = f"{extra_count} of 38 districts matched"
        if missing:
            details += f"; missing: {', '.join(missing)}"
        raise ValueError(f"The boundary source is incomplete ({details}).")

    return [matched[name] for name in districts], sorted(vintages)


def canonicalize_url(value: str) -> Optional[str]:
    try:
        parsed = urlparse(str(value or "").strip())
        if parsed.scheme.lower() != "https" or not parsed.hostname or parsed.username or parsed.password:
            return None
        host = parsed.hostname.lower().removeprefix("www.")
        query = [
            (key, val) for key, val in parse_qsl(parsed.query, keep_blank_values=True)
            if not key.casefold().startswith("utm_") and key.casefold() not in TRACKING_QUERY_KEYS
        ]
        return urlunparse(("https", host, parsed.path.rstrip("/") or "/", "", urlencode(query), ""))
    except (TypeError, ValueError):
        return None


def _parse_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        parsed = value
    elif value:
        text = str(value).strip()
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            try:
                from email.utils import parsedate_to_datetime

                parsed = parsedate_to_datetime(text)
            except (TypeError, ValueError, OverflowError):
                return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _title_tokens(title: str) -> set:
    return set(re.findall(r"[a-z0-9]{3,}", str(title or "").casefold()))


def _similarity(left: str, right: str) -> float:
    left_tokens, right_tokens = _title_tokens(left), _title_tokens(right)
    union = left_tokens | right_tokens
    return len(left_tokens & right_tokens) / len(union) if union else 0.0


def _incident_id(district: Optional[str], category: str, title: str, date: Optional[datetime]) -> str:
    day = date.date().isoformat() if date else "undated"
    key = f"{district or 'unknown'}|{category}|{day}|{' '.join(sorted(_title_tokens(title)))}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def connect_database(path: str) -> sqlite3.Connection:
    database_path = Path(path)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(database_path), timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database(path: str) -> None:
    with connect_database(path) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS incidents (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                district TEXT,
                category TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                status TEXT,
                severity TEXT,
                is_verified INTEGER NOT NULL DEFAULT 0 CHECK (is_verified IN (0, 1)),
                published_at TEXT,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS incidents_district_date
                ON incidents (district, published_at);
            CREATE INDEX IF NOT EXISTS incidents_status_severity
                ON incidents (is_verified, status, severity);

            CREATE TABLE IF NOT EXISTS incident_reports (
                id TEXT PRIMARY KEY,
                incident_id TEXT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
                canonical_url TEXT NOT NULL UNIQUE,
                source_url TEXT NOT NULL,
                source_name TEXT NOT NULL,
                evidence_level TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                published_at TEXT,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS incident_reports_incident
                ON incident_reports (incident_id);

            CREATE TABLE IF NOT EXISTS ingestion_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_name TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('success', 'error')),
                started_at TEXT NOT NULL,
                completed_at TEXT NOT NULL,
                records_seen INTEGER NOT NULL DEFAULT 0,
                incidents_added INTEGER NOT NULL DEFAULT 0,
                error TEXT
            );
            """
        )


def record_ingestion_run(
    path: str,
    source_name: str,
    status: str,
    started_at: datetime,
    records_seen: int = 0,
    incidents_added: int = 0,
    error: Optional[str] = None,
) -> None:
    completed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect_database(path) as connection:
        connection.execute(
            """
            INSERT INTO ingestion_runs
                (source_name, status, started_at, completed_at, records_seen, incidents_added, error)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_name,
                status,
                started_at.astimezone(timezone.utc).isoformat(timespec="seconds"),
                completed_at,
                max(0, records_seen),
                max(0, incidents_added),
                error[:1000] if error else None,
            ),
        )


def ingest_records(
    path: str,
    records: Iterable[Dict[str, Any]],
    districts: Dict[str, List[str]],
) -> Dict[str, int]:
    initialize_database(path)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    inserted_incidents = 0
    added_sources = 0
    skipped = 0

    with connect_database(path) as connection:
        for item in records:
            if not isinstance(item, dict):
                skipped += 1
                continue
            title = str(item.get("title") or "").strip()[:500]
            source_url = str(item.get("source_url") or "").strip()
            canonical_url = canonicalize_url(source_url)
            source_name = str(item.get("source_name") or "").strip()[:200]
            category = str(item.get("category") or "").strip().casefold()
            description = str(item.get("description") or "").strip()[:3000]
            published = _parse_datetime(item.get("published_at"))
            district = item.get("district")
            if district not in districts:
                district = match_district(f"{title} {description}", districts)
            if (not title or not canonical_url or not source_name
                    or category not in INCIDENT_CATEGORIES):
                skipped += 1
                continue

            published_at = published.isoformat(timespec="seconds") if published else None
            incident = connection.execute(
                """
                SELECT id, title FROM incidents
                WHERE category = ? AND district IS ? AND published_at IS NOT NULL
                AND published_at BETWEEN ? AND ?
                """,
                (
                    category,
                    district,
                    (published - timedelta(days=3)).isoformat(timespec="seconds")
                    if published else "0001-01-01T00:00:00+00:00",
                    (published + timedelta(days=3)).isoformat(timespec="seconds")
                    if published else "0001-01-01T00:00:00+00:00",
                ),
            ).fetchall() if published else []

            matched = next(
                (candidate for candidate in incident
                 if _similarity(title, candidate["title"]) >= 0.5),
                None,
            )
            incident_id = matched["id"] if matched else _incident_id(
                district, category, title, published)

            existing_report = connection.execute(
                "SELECT id, incident_id FROM incident_reports WHERE canonical_url = ?",
                (canonical_url,),
            ).fetchone()
            if existing_report:
                connection.execute(
                    """
                    UPDATE incident_reports SET last_seen_at = ?,
                        description = CASE WHEN length(?) > length(description) THEN ? ELSE description END
                    WHERE id = ?
                    """,
                    (now, description, description, existing_report["id"]),
                )
                if existing_report["incident_id"] != incident_id:
                    incident_id = existing_report["incident_id"]
            else:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO incidents
                        (id, title, district, category, description, status, severity, is_verified,
                         published_at, first_seen_at, last_seen_at)
                    VALUES (?, ?, ?, ?, ?, NULL, NULL, 0, ?, ?, ?)
                    """,
                    (incident_id, title, district, category, description, published_at, now, now),
                )
                if connection.execute(
                    "SELECT changes()"
                ).fetchone()[0]:
                    inserted_incidents += 1
                connection.execute(
                    """
                    INSERT OR IGNORE INTO incident_reports
                        (id, incident_id, canonical_url, source_url, source_name, evidence_level,
                         description, published_at, first_seen_at, last_seen_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        hashlib.sha256(canonical_url.encode("utf-8")).hexdigest(),
                        incident_id,
                        canonical_url,
                        source_url,
                        source_name,
                        str(item.get("evidence_level") or "reported_news")[:40],
                        description,
                        published_at,
                        now,
                        now,
                    ),
                )
                if connection.execute("SELECT changes()").fetchone()[0]:
                    added_sources += 1
                else:
                    skipped += 1

            connection.execute(
                """
                UPDATE incidents SET last_seen_at = ?,
                    description = CASE WHEN length(?) > length(description) THEN ? ELSE description END,
                    published_at = CASE
                        WHEN published_at IS NULL THEN ?
                        WHEN ? IS NOT NULL AND ? > published_at THEN ?
                        ELSE published_at END
                WHERE id = ?
                """,
                (now, description, description, published_at, published_at, published_at,
                 published_at, incident_id),
            )

    return {
        "incidents_added": inserted_incidents,
        "sources_added": added_sources,
        "skipped": skipped,
    }


def _trend_snapshot(connection: sqlite3.Connection, now: datetime) -> Optional[Dict[str, Any]]:
    start = now - timedelta(days=14)
    rows = connection.execute(
        """
        SELECT category, published_at FROM incidents
        WHERE published_at >= ? AND published_at < ?
        """,
        (start.isoformat(timespec="seconds"), now.isoformat(timespec="seconds")),
    ).fetchall()
    recent_start = now - timedelta(days=7)
    recent: Dict[str, int] = {}
    previous: Dict[str, int] = {}
    recent_total = previous_total = 0
    for row in rows:
        published = _parse_datetime(row["published_at"])
        if not published:
            continue
        if published >= recent_start:
            recent[row["category"]] = recent.get(row["category"], 0) + 1
            recent_total += 1
        else:
            previous[row["category"]] = previous.get(row["category"], 0) + 1
            previous_total += 1
    if recent_total < 3 or previous_total < 3:
        return None
    increasing = sorted(
        category for category, count in recent.items()
        if count > previous.get(category, 0)
    )
    return {
        "count": len(increasing),
        "categories": increasing,
        "recent_total": recent_total,
        "previous_total": previous_total,
    }


def dashboard_snapshot(path: str, now: Optional[datetime] = None) -> Dict[str, Any]:
    initialize_database(path)
    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    current_time = current_time.astimezone(timezone.utc)

    with connect_database(path) as connection:
        verified_status_rows = connection.execute(
            """
            SELECT COUNT(*) AS count FROM incidents
            WHERE is_verified = 1 AND status IS NOT NULL
            """
        ).fetchone()["count"]
        active_rows = connection.execute(
            """
            SELECT id, district, severity FROM incidents
            WHERE is_verified = 1 AND lower(status) IN ('active', 'open', 'unresolved', 'in_progress')
            """
        ).fetchall()

        active_issues = len(active_rows) if verified_status_rows else None
        if not verified_status_rows:
            critical_alerts = affected_districts = None
        else:
            severity_complete = all(row["severity"] for row in active_rows)
            district_complete = all(row["district"] for row in active_rows)
            critical_alerts = (
                sum(str(row["severity"]).casefold() in CRITICAL_SEVERITIES for row in active_rows)
                if severity_complete else None
            )
            affected_districts = (
                len({row["district"] for row in active_rows})
                if district_complete else None
            )

        freshness = connection.execute(
            """
            SELECT MAX(completed_at) AS value FROM ingestion_runs WHERE status = 'success'
            """
        ).fetchone()["value"]
        report_window = connection.execute(
            """
            SELECT COUNT(*) AS incidents, COUNT(DISTINCT district) AS districts
            FROM incidents
            WHERE published_at >= ? AND published_at < ?
            """,
            (
                (current_time - timedelta(days=30)).isoformat(timespec="seconds"),
                current_time.isoformat(timespec="seconds"),
            ),
        ).fetchone()
        trend = _trend_snapshot(connection, current_time)
        reported_by_district = {
            row["district"]: row["count"]
            for row in connection.execute(
                """
                SELECT district, COUNT(*) AS count FROM incidents
                WHERE district IS NOT NULL GROUP BY district
                """
            ).fetchall()
        }

    return {
        "active_issues": active_issues,
        "critical_alerts": critical_alerts,
        "affected_districts": affected_districts,
        "emerging_issues": trend,
        "pending_actions": None,
        "freshness": freshness,
        "reported_incidents_30d": report_window["incidents"],
        "reported_districts_30d": report_window["districts"],
        "reported_by_district": reported_by_district,
    }


def district_incidents(path: str, district: str) -> List[Dict[str, Any]]:
    initialize_database(path)
    with connect_database(path) as connection:
        incidents = connection.execute(
            """
            SELECT id, title, district, category, description, status, severity, is_verified,
                   published_at
            FROM incidents WHERE district = ?
            ORDER BY published_at DESC, title COLLATE NOCASE
            """,
            (district,),
        ).fetchall()
        records = []
        for incident in incidents:
            sources = connection.execute(
                """
                SELECT source_name, source_url, evidence_level, published_at
                FROM incident_reports WHERE incident_id = ?
                ORDER BY published_at DESC, source_name
                """,
                (incident["id"],),
            ).fetchall()
            records.append({
                **dict(incident),
                "sources": [dict(source) for source in sources],
            })
    return records
