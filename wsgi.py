"""Production entry point:  gunicorn -c gunicorn.conf.py wsgi:app"""
import logging

from dotenv import load_dotenv

load_dotenv()          # local runs only; on Render, variables come from the dashboard
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

from webapp import create_app  # noqa: E402

app = create_app()
