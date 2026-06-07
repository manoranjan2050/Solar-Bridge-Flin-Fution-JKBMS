"""
Deployment credentials for deploy.py.

Copy this file to `deploy_secrets.py` (which is git-ignored) and fill in your
Raspberry Pi's details. Alternatively set the environment variables
DEPLOY_HOST / DEPLOY_USER / DEPLOY_PASS.

DO NOT commit deploy_secrets.py — it contains your password.
"""

HOST = "192.168.1.32"      # your Pi's IP or hostname
USER = "pi"                # your Pi login user
PASS = "changeme"          # your Pi login password
