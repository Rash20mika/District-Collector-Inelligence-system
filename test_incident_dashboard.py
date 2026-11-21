import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import incident_dashboard


DISTRICTS = {"Madurai": [], "Chennai": [], "Coimbatore": []}


class IncidentDashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = str(Path(self.temp_dir.name) / "incidents.sqlite3")
        incident_dashboard.initialize_database(self.database)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_missing_status_is_unavailable_not_zero(self):
        snapshot = incident_dashboard.dashboard_snapshot(self.database)
        self.assertIsNone(snapshot["active_issues"])
        self.assertIsNone(snapshot["critical_alerts"])
        self.assertIsNone(snapshot["affected_districts"])
        self.assertIsNone(snapshot["emerging_issues"])
        self.assertIsNone(snapshot["freshness"])
        self.assertEqual(snapshot["reported_incidents_30d"], 0)
        self.assertEqual(snapshot["reported_districts_30d"], 0)

    def test_duplicate_reports_share_one_incident_and_keep_both_sources(self):
        published = datetime(2026, 10, 30, tzinfo=timezone.utc)
        first = {
            "title": "Madurai water supply disruption affects central neighborhoods",
            "description": "Reported outage in Madurai.",
            "district": "Madurai",
            "category": "water_supply",
            "published_at": published,
            "source_url": "https://news.example/incident/1?utm_source=feed",
            "source_name": "News One",
        }
        second = {
            **first,
            "title": "Central neighborhoods face Madurai water supply disruption",
            "source_url": "https://news.example/incident/2",
            "source_name": "News Two",
        }
        first_result = incident_dashboard.ingest_records(
            self.database, [first], DISTRICTS)
        second_result = incident_dashboard.ingest_records(
            self.database, [second], DISTRICTS)

        self.assertEqual(first_result["incidents_added"], 1)
        self.assertEqual(second_result["incidents_added"], 0)
        self.assertEqual(second_result["sources_added"], 1)
        reports = incident_dashboard.district_incidents(self.database, "Madurai")
        self.assertEqual(len(reports), 1)
        self.assertEqual(len(reports[0]["sources"]), 2)
        self.assertFalse(reports[0]["is_verified"])
        snapshot = incident_dashboard.dashboard_snapshot(self.database)
        self.assertIsNone(snapshot["active_issues"])
        self.assertEqual(snapshot["reported_by_district"]["Madurai"], 1)

    def test_dashboard_groups_reported_categories_by_district(self):
        records = [
            {
                "title": f"Madurai report {index} about {category}",
                "district": "Madurai",
                "category": category,
                "published_at": datetime(2026, 11, 1, tzinfo=timezone.utc)
                + timedelta(days=index * 5),
                "source_url": f"https://news.example/category/{index}",
                "source_name": "Test",
            }
            for index, category in enumerate(
                ["flood_rain", "flood_rain", "fire"], start=1
            )
        ]
        incident_dashboard.ingest_records(self.database, records, DISTRICTS)

        snapshot = incident_dashboard.dashboard_snapshot(self.database)

        self.assertEqual(
            snapshot["reported_categories_by_district"]["Madurai"],
            {"fire": 1, "flood_rain": 2},
        )
        self.assertNotIn("Chennai", snapshot["reported_categories_by_district"])

    def test_verified_status_and_severity_are_required_for_active_kpis(self):
        incident_dashboard.ingest_records(
            self.database,
            [{
                "title": "Confirmed high-severity Madurai water supply issue",
                "district": "Madurai",
                "category": "water_supply",
                "published_at": datetime.now(timezone.utc),
                "source_url": "https://news.example/confirmed",
                "source_name": "Test",
            }],
            DISTRICTS,
        )
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "UPDATE incidents SET is_verified = 1, status = 'active', severity = 'high'"
            )

        snapshot = incident_dashboard.dashboard_snapshot(self.database)
        self.assertEqual(snapshot["active_issues"], 1)
        self.assertEqual(snapshot["critical_alerts"], 1)
        self.assertEqual(snapshot["affected_districts"], 1)

    def test_emerging_issues_requires_records_in_both_periods(self):
        now = datetime.now(timezone.utc)
        records = []
        titles = [
            ("Reservoir overflow near the western embankment", 2),
            ("Monsoon inundation at the eastern settlement", 3),
            ("Canal flooding around the southern market", 4),
            ("Bridge collision closes the northern road", 9),
            ("Factory smoke triggers a public health alert", 10),
            ("Power transformer fire damages the central substation", 11),
        ]
        for index, (title, days_ago) in enumerate(titles):
            records.append({
                "title": title,
                "district": "Madurai",
                "category": "flood_rain" if index < 3 else "infrastructure",
                "published_at": now - timedelta(days=days_ago),
                "source_url": f"https://news.example/trend/{index}",
                "source_name": "Test",
            })
        incident_dashboard.ingest_records(self.database, records, DISTRICTS)

        trend = incident_dashboard.dashboard_snapshot(self.database, now)["emerging_issues"]
        self.assertIsNotNone(trend)
        self.assertEqual(trend["count"], 1)
        self.assertEqual(trend["categories"], ["flood_rain"])

    def test_recent_report_metrics_count_incidents_and_named_districts(self):
        now = datetime(2026, 11, 10, tzinfo=timezone.utc)
        records = [
            {
                "title": f"Distinct recent incident report {index} in {district}",
                "district": district,
                "category": category,
                "published_at": now - timedelta(days=age),
                "source_url": f"https://news.example/recent/{index}",
                "source_name": "Test",
            }
            for index, (district, category, age) in enumerate([
                ("Madurai", "flood_rain", 1),
                ("Chennai", "fire", 5),
                ("Madurai", "health", 20),
                ("Coimbatore", "infrastructure", 31),
            ])
        ]
        incident_dashboard.ingest_records(self.database, records, DISTRICTS)

        snapshot = incident_dashboard.dashboard_snapshot(self.database, now)
        self.assertEqual(snapshot["reported_incidents_30d"], 3)
        self.assertEqual(snapshot["reported_districts_30d"], 2)

    def test_boundary_validation_rejects_incomplete_feature_collection(self):
        with self.assertRaisesRegex(ValueError, "incomplete"):
            incident_dashboard.validate_boundary_geojson(
                {"type": "FeatureCollection", "features": []},
                DISTRICTS,
            )

    def test_district_alias_matching(self):
        districts = {"Thoothukudi": ["Tuticorin"]}
        self.assertEqual(
            incident_dashboard.match_district("Heavy rain reported in Tuticorin", districts),
            "Thoothukudi",
        )
        self.assertIsNone(
            incident_dashboard.match_district("Heavy rain reported in Salem", districts),
        )


if __name__ == "__main__":
    unittest.main()
