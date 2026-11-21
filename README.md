# Tamil Nadu District Intelligence

The project includes a conventional, authenticated website alongside the original Streamlit application.

## Run the website

1. Copy `.env.example` to `.env` if you do not already have a `.env` file.
2. Set `WEB_USERNAME` and `WEB_PASSWORD` for the Collector account, and `PA_USERNAME` and `PA_PASSWORD` for a separate Personal Assistant account. Use different credentials for each role. Set `WEB_SESSION_SECRET` to a long random value.
3. Set `GROQ_API_KEY` to enable assistant answers and voice transcription. `TAVILY_API_KEY` is optional and enables public-link extraction.
4. Start the website from the project directory:

   ```powershell
   .\.venv\Scripts\python.exe web_app.py
   ```

5. Open <http://127.0.0.1:8000>.

The website reads the existing incident database, source registry, district boundaries, and local index. The map uses the configured community GeoJSON boundary source; verify its vintage before official use. Incident reports are unverified unless independently confirmed. TNGIS metrics, AI answers, voice transcription, and public-link extraction require their configured sources or API keys.

The Collector site shows today's assigned tasks above the dashboard metrics and the upcoming tasks for the next seven days. The PA site lets an authenticated Personal Assistant add, edit, or remove dated tasks for that window. Tasks are stored in the incident SQLite database and persist across logins; the Collector can mark them complete.

For a network-facing deployment, use HTTPS behind a production web server, set a persistent `WEB_SESSION_SECRET`, and use strong account credentials. The included WSGI server is intended for local development.

The original Streamlit entry point remains available as `app.py`; run `streamlit run app.py` only when you specifically want the Streamlit interface.
