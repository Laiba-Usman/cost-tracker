# Deploying Cost Tracker on Render (free)

Needs: a GitHub account and a Render account (sign in to Render with GitHub). No credit card is needed for a free web service.

1. Put the project on GitHub
   ```
   cd costtracker
   git init
   git add .
   git status          # must NOT list instance/ or any .db file
   git commit -m "Cost Tracker"
   git branch -M main
   git remote add origin https://github.com/<your-username>/cost-tracker.git
   git push -u origin main
   ```
2. On render.com: New + > Blueprint > pick the repository > Apply. Render reads `render.yaml` (build command, start command, SECRET_KEY generated, COOKIE_SECURE=1, TRUST_PROXY=1).
   Without the Blueprint: New + > Web Service, Runtime Python, Build `pip install -r requirements.txt`, Start `gunicorn app:app --workers 1 --threads 4 --timeout 60 --bind 0.0.0.0:$PORT`, Instance type Free, and add the environment variables from `render.yaml`.
3. Wait for "Live". Open `https://<name>.onrender.com/healthz` (should print `ok`), then the main address.
4. Create an account, press Load LYKOS demo, check every tab and both downloads.
5. Test the live site: `BASE_URL=https://<name>.onrender.com python tests/security_tests.py` (`pip install -r requirements-dev.txt` first; add `--only ST-01,ST-02,ST-04,ST-05,ST-06,ST-07,ST-03a,ST-03b` to skip the browser test).
6. After your group has registered, set `ALLOW_REGISTRATION=0` in Render > Environment so strangers cannot create accounts.

Free-plan facts (Render docs): the service sleeps after 15 minutes without traffic and takes about a minute to wake; the disk is wiped on every restart, redeploy or sleep, so accounts and projects are lost (use Load LYKOS demo again); 750 free instance hours per month.
If the build fails on the Python version, delete the PYTHON_VERSION variable or use another 3.13.x.
