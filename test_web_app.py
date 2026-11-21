import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import incident_dashboard
import web_app


class WebsiteAuthenticationTests(unittest.TestCase):
    def test_session_token_expires_and_rejects_tampering(self):
        token = web_app.create_session_token(now=1_000)

        self.assertTrue(web_app.is_valid_session(token, now=1_001))
        self.assertFalse(web_app.is_valid_session(token, now=1_000 + web_app.SESSION_TTL_SECONDS))
        self.assertFalse(web_app.is_valid_session(f"{token}x", now=1_001))

    def test_session_token_preserves_role(self):
        token = web_app.create_session_token(role="pa")

        self.assertEqual(web_app._session_role(token), "pa")

    def test_protected_api_requires_a_session(self):
        status, _, body = self._request("/api/index")

        self.assertEqual(status, "401 Unauthorized")
        self.assertIn("Sign in", json.loads(body)["error"])

    def test_login_sets_http_only_same_site_cookie(self):
        with patch.dict(os.environ, {"WEB_USERNAME": "officer", "WEB_PASSWORD": "long-test-password"}):
            status, headers, body = self._request(
                "/api/login",
                method="POST",
                payload={"username": "officer", "password": "long-test-password"},
            )

        self.assertEqual(status, "200 OK")
        self.assertTrue(json.loads(body)["authenticated"])
        cookie = dict(headers)["Set-Cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        self.assertTrue(web_app.is_valid_session(cookie.split(";", 1)[0].split("=", 1)[1]))

    def test_login_requires_configured_credentials(self):
        with patch.dict(os.environ, {}, clear=True):
            status, _, body = self._request(
                "/api/login",
                method="POST",
                payload={"username": "officer", "password": "anything"},
            )

        self.assertEqual(status, "503 Service Unavailable")
        self.assertIn("PA credentials", json.loads(body)["error"])

    def test_pa_login_receives_pa_role(self):
        credentials = {
            "WEB_USERNAME": "collector",
            "WEB_PASSWORD": "collector-test-password",
            "PA_USERNAME": "assistant",
            "PA_PASSWORD": "pa-test-password",
        }
        with patch.dict(os.environ, credentials):
            status, headers, body = self._request(
                "/api/login",
                method="POST",
                payload={"username": "assistant", "password": "pa-test-password"},
            )

        self.assertEqual(status, "200 OK")
        self.assertEqual(json.loads(body)["role"], "pa")
        cookie = dict(headers)["Set-Cookie"].split(";", 1)[0].split("=", 1)[1]
        self.assertEqual(web_app._session_role(cookie), "pa")
        status, _, body = self._request("/api/session", cookie=f"{web_app.COOKIE_NAME}={cookie}")
        self.assertEqual(status, "200 OK")
        self.assertEqual(json.loads(body)["role"], "pa")
        self.assertTrue(json.loads(body)["authenticated"])

    def test_pa_tasks_persist_and_collector_can_only_mark_complete(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            web_app, "DATABASE_PATH", Path(temp_dir) / "tasks.sqlite3"
        ):
            task_date = web_app._today().isoformat()
            pa_cookie = f"{web_app.COOKIE_NAME}={web_app.create_session_token(role='pa')}"
            collector_cookie = (
                f"{web_app.COOKIE_NAME}={web_app.create_session_token(role='collector')}"
            )

            status, _, body = self._request(
                "/api/tasks",
                method="POST",
                payload={"task_date": task_date, "task_text": "Review district report"},
                cookie=pa_cookie,
            )
            task = json.loads(body)["task"]
            self.assertEqual(status, "201 Created")

            status, _, body = self._request("/api/tasks", cookie=collector_cookie)
            self.assertEqual(status, "200 OK")
            self.assertEqual(json.loads(body)["tasks"][0]["task_text"], "Review district report")

            status, _, body = self._request(
                f"/api/tasks/{task['id']}",
                method="PATCH",
                payload={"task_text": "Change not allowed"},
                cookie=collector_cookie,
            )
            self.assertEqual(status, "403 Forbidden")

            status, _, body = self._request(
                f"/api/tasks/{task['id']}",
                method="PATCH",
                payload={"is_complete": True},
                cookie=collector_cookie,
            )
            self.assertEqual(status, "200 OK")
            self.assertTrue(json.loads(body)["task"]["is_complete"])

            status, _, body = self._request(
                f"/api/tasks/{task['id']}",
                method="PATCH",
                payload={"task_text": "Updated by PA"},
                cookie=pa_cookie,
            )
            self.assertEqual(status, "200 OK")
            self.assertEqual(json.loads(body)["task"]["task_text"], "Updated by PA")

            status, _, _ = self._request(
                f"/api/tasks/{task['id']}",
                method="DELETE",
                cookie=collector_cookie,
            )
            self.assertEqual(status, "403 Forbidden")

    def _request(self, path, method="GET", payload=None, cookie=""):
        raw_body = json.dumps(payload or {}).encode("utf-8")
        environ = {
            "PATH_INFO": path,
            "QUERY_STRING": "",
            "REQUEST_METHOD": method,
            "CONTENT_LENGTH": str(len(raw_body)) if payload is not None else "0",
            "CONTENT_TYPE": "application/json",
            "REMOTE_ADDR": "127.0.0.1",
            "wsgi.url_scheme": "http",
            "wsgi.input": io.BytesIO(raw_body),
        }
        if cookie:
            environ["HTTP_COOKIE"] = cookie
        captured = {}

        def start_response(status, headers):
            captured["status"] = status
            captured["headers"] = headers

        body = b"".join(web_app.application(environ, start_response))
        return captured["status"], captured["headers"], body


class WebsiteChatResponseTests(unittest.TestCase):
    def test_tavily_searches_approved_sites_and_extracts_matching_pages(self):
        search_response = SimpleNamespace(json=lambda: {
            "results": [
                {
                    "title": "Revenue Department information",
                    "url": "https://madurai.nic.in/revenue/",
                    "content": "Search excerpt.",
                    "published_date": "2026-11-20",
                },
                {
                    "title": "Tanishq robbery reported in Madurai",
                    "url": "https://www.thehindu.com/news/cities/Madurai/tanishq-robbery/",
                    "content": "A robbery was reported at a Madurai Tanishq showroom.",
                    "published_date": "2026-11-12",
                },
            ]
        })
        extract_response = SimpleNamespace(json=lambda: {
            "results": [
                {
                    "url": "https://madurai.nic.in/revenue/",
                    "raw_content": "Extracted page text about the Revenue Department.",
                },
                {
                    "url": "https://www.thehindu.com/news/cities/Madurai/tanishq-robbery/",
                    "raw_content": "Extracted report about the Madurai Tanishq robbery.",
                },
            ]
        })
        with patch.dict(os.environ, {"TAVILY_API_KEY": "test-key"}), patch.object(
            web_app, "_approved_request", side_effect=[search_response, extract_response]
        ) as approved_request:
            excerpts, notice = web_app._tavily_web_search(
                "About Madurai Tanishq robbery"
            )

        self.assertIsNone(notice)
        self.assertEqual(len(excerpts), 2)
        self.assertIn("Extracted page text", excerpts[0]["content"])
        self.assertIn("Extracted report", excerpts[1]["content"])
        self.assertEqual(excerpts[1]["evidence_level"], "reported_news")
        requested_domains = approved_request.call_args_list[0].kwargs["json_body"]["include_domains"]
        self.assertIn("madurai.nic.in", requested_domains)
        self.assertIn("www.thehindu.com", requested_domains)
        self.assertEqual(approved_request.call_count, 2)

    def test_unlinked_question_includes_tavily_evidence_and_citation(self):
        payload = {
            "response_type": "text",
            "title": "Source summary",
            "summary": "A result was found on an approved website.",
            "answer": "The district portal provides the requested information.",
            "visualization_type": "none",
            "visualization_data": {},
            "key_observations": [],
            "priority_alerts": [],
            "sources": [],
            "last_updated": "",
        }
        calls = []

        def fake_invoke(messages):
            calls.append(messages)
            return SimpleNamespace(content=json.dumps(payload))

        fake_llm = SimpleNamespace(invoke=fake_invoke)
        tavily_result = [{
            "title": "District portal page",
            "url": "https://madurai.nic.in/department/",
            "content": "Retrieved information from the district portal.",
            "source_name": "Madurai District Portal",
            "evidence_level": "official",
        }]
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            web_app, "DATABASE_PATH", Path(temp_dir) / "chat.sqlite3"
        ), patch.dict(os.environ, {"GROQ_API_KEY": "test-key"}), patch.object(
            web_app.district_index, "load_index", return_value=None
        ), patch.object(
            web_app, "_tavily_web_search", return_value=(tavily_result, None)
        ), patch("langchain_groq.ChatGroq", return_value=fake_llm):
            response = web_app._chat_response(
                {"question": "What information is on the district portal?"}
            )

        self.assertIn("Retrieved information from the district portal.", calls[0][-1]["content"])
        self.assertEqual(response["sources"][0]["url"], "https://madurai.nic.in/department/")

    def test_structured_response_validation_rejects_incomplete_payload(self):
        payload = {
            "response_type": "donut_chart",
            "title": "Incident types",
            "summary": "Reported incident categories.",
            "answer": "The records contain two categories.",
            "visualization_type": "donut_chart",
            "visualization_data": {},
            "key_observations": [],
            "priority_alerts": [],
            "sources": [],
            "last_updated": "",
        }

        self.assertEqual(web_app._parse_chat_response(json.dumps(payload))["response_type"], "donut_chart")
        self.assertIsNone(web_app._parse_chat_response('{"response_type":"donut_chart"}'))
        self.assertIsNone(web_app._parse_chat_response("not JSON"))

    def test_chat_visualization_uses_calculated_incident_records_not_model_values(self):
        records = [
            {
                "title": "Madurai flood report",
                "district": "Madurai",
                "category": "flood_rain",
                "published_at": datetime(2026, 11, 10, tzinfo=timezone.utc),
                "source_url": "https://news.example/flood",
                "source_name": "Test News",
            },
            {
                "title": "Chennai fire report",
                "district": "Chennai",
                "category": "fire",
                "published_at": datetime(2026, 11, 11, tzinfo=timezone.utc),
                "source_url": "https://news.example/fire",
                "source_name": "Test News",
            },
        ]
        payload = {
            "response_type": "donut_chart",
            "title": "Incident types",
            "summary": "Reported categories.",
            "answer": "Two reports were retrieved.",
            "visualization_type": "donut_chart",
            "visualization_data": {"labels": ["invented"], "values": [9999]},
            "key_observations": [],
            "priority_alerts": [],
            "sources": [],
            "last_updated": "",
        }
        fake_llm = SimpleNamespace(
            invoke=lambda messages: SimpleNamespace(content=json.dumps(payload))
        )
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            web_app, "DATABASE_PATH", Path(temp_dir) / "chat.sqlite3"
        ), patch.dict(os.environ, {"GROQ_API_KEY": "test-key"}), patch.object(
            web_app.district_index, "load_index", return_value=None
        ), patch.object(
            web_app, "_tavily_web_search", return_value=([], None)
        ), patch("langchain_groq.ChatGroq", return_value=fake_llm):
            incident_dashboard.ingest_records(
                str(web_app.DATABASE_PATH), records, web_app.DISTRICTS
            )
            response = web_app._chat_response({"question": "What types of incidents are reported?"})

        self.assertEqual(response["response_type"], "donut_chart")
        self.assertEqual(response["visualization_data"]["labels"], ["fire", "flood_rain"])
        self.assertEqual(response["visualization_data"]["values"], [1, 1])
        self.assertNotIn("invented", response["visualization_data"]["labels"])
        self.assertTrue(response["last_updated"])

    def test_unsupported_rainfall_chart_falls_back_to_text_with_limitation(self):
        payload = {
            "response_type": "line_chart",
            "title": "Rainfall trends",
            "summary": "Rainfall data summary.",
            "answer": "The retrieved sources do not contain rainfall measurements.",
            "visualization_type": "line_chart",
            "visualization_data": {},
            "key_observations": [],
            "priority_alerts": [],
            "sources": [],
            "last_updated": "",
        }
        fake_llm = SimpleNamespace(
            invoke=lambda messages: SimpleNamespace(content=json.dumps(payload))
        )
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            web_app, "DATABASE_PATH", Path(temp_dir) / "chat.sqlite3"
        ), patch.dict(os.environ, {"GROQ_API_KEY": "test-key"}), patch.object(
            web_app.district_index, "load_index", return_value=None
        ), patch.object(
            web_app, "_tavily_web_search", return_value=([], None)
        ), patch("langchain_groq.ChatGroq", return_value=fake_llm):
            response = web_app._chat_response({"question": "Compare rainfall trends"})

        self.assertEqual(response["response_type"], "text")
        self.assertEqual(response["visualization_type"], "none")
        self.assertEqual(response["visualization_data"], {})
        self.assertIn("Rainfall measurements were not available", response["answer"])

    def test_malformed_model_json_retries_as_plain_text(self):
        outputs = iter([
            '{"response_type":"unknown"}',
            "The Revenue Department administers land records and revenue functions.",
        ])
        calls = []

        def fake_invoke(messages):
            calls.append(messages)
            return SimpleNamespace(content=next(outputs))

        fake_llm = SimpleNamespace(
            invoke=fake_invoke
        )
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            web_app, "DATABASE_PATH", Path(temp_dir) / "chat.sqlite3"
        ), patch.dict(os.environ, {"GROQ_API_KEY": "test-key"}), patch.object(
            web_app.district_index, "load_index", return_value=None
        ), patch.object(
            web_app, "_tavily_web_search", return_value=([], None)
        ), patch("langchain_groq.ChatGroq", return_value=fake_llm):
            response = web_app._chat_response(
                {"question": "Explain the role of the Revenue Department."}
            )

        self.assertEqual(response["response_type"], "text")
        self.assertEqual(response["visualization_type"], "none")
        self.assertIn("Revenue Department", response["answer"])
        self.assertIn("general-knowledge questions", calls[1][0]["content"])

    def test_unlinked_general_question_can_use_general_knowledge(self):
        payload = {
            "response_type": "text",
            "title": "Revenue Department",
            "summary": "General, non-official overview.",
            "answer": "The department administers land and revenue matters.",
            "visualization_type": "none",
            "visualization_data": {},
            "key_observations": [],
            "priority_alerts": [],
            "sources": [],
            "last_updated": "",
        }
        calls = []

        def fake_invoke(messages):
            calls.append(messages)
            return SimpleNamespace(content=json.dumps(payload))

        fake_llm = SimpleNamespace(invoke=fake_invoke)
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            web_app, "DATABASE_PATH", Path(temp_dir) / "chat.sqlite3"
        ), patch.dict(os.environ, {"GROQ_API_KEY": "test-key"}), patch.object(
            web_app.district_index, "load_index", return_value=None
        ), patch.object(
            web_app, "_tavily_web_search", return_value=([], None)
        ), patch("langchain_groq.ChatGroq", return_value=fake_llm):
            response = web_app._chat_response(
                {"question": "Explain the role of the Revenue Department."}
            )

        self.assertEqual(response["response_type"], "text")
        self.assertIn("land and revenue", response["answer"])
        self.assertIn("general-knowledge questions", calls[0][0]["content"])


if __name__ == "__main__":
    unittest.main()
