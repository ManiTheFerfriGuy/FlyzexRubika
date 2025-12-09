# Rubika Bot Deployment on cPanel (FlyzexBot)

This README is a start-to-finish checklist for running the Rubika-ready version of FlyzexBot on cPanel hosting. It assumes you will deploy through cPanel’s **Setup Python App** feature and manage files via **File Manager** or SSH.

If you previously deployed FlyzexBot for Telegram, the Rubika flow is nearly identical: swap in your Rubika bot token and keep the environment variables the same names.

---

## 0. Prerequisites
- **Hosting access:** A cPanel account with SSH or Terminal access and permission to create Python applications.
- **Rubika bot token:** Create or retrieve a token for your Rubika bot and keep it available.
- **Admin API key:** Generate a strong random string (for example, from a password manager) to protect the admin endpoints.
- **Basic cPanel familiarity:** Know how to open **File Manager**, **Terminal**, and **Setup Python App**.
- **Recommended directory:** This guide assumes everything lives in `/home/<cpanel-user>/flyzexbot`. Adjust paths if you prefer a different folder.

## 1. Upload the Project
1. Download the latest FlyzexBot archive or clone the repository locally.
2. In cPanel, open **File Manager** and upload the project folder (or a `.zip` of it) to your chosen location.
3. If you uploaded a `.zip`, right-click it and select **Extract**.
4. Confirm the following files exist inside the extracted folder:
   - `bot.py`
   - the `flyzexbot/` package directory
   - the `config/` directory containing `settings.example.yaml`
   - `requirements.txt`
   - `webapp/` (only needed if you plan to expose the admin dashboard)

## 2. Create the Python Application
1. In cPanel, open **Setup Python App**.
2. Click **Create Application** and set:
   - **Python version:** `3.10` or later (whatever your host supports).
   - **Application root:** The folder from step 1 (for example, `flyzexbot`).
   - **Application URL** and **Startup file:** leave empty; the bot is started manually.
3. Save. Copy down the **Application Root** and **Virtual Environment Path** shown at the top—both are needed later.

## 3. Install Dependencies
1. In **Setup Python App**, click **Run Pip Installer** (or open **Terminal** from the cPanel dashboard if the button is unavailable).
2. Activate the virtual environment shown in step 2:
   ```bash
   source /home/<cpanel-user>/<virtualenv>/bin/activate
   ```
3. Install requirements from the project directory:
   ```bash
   pip install --upgrade pip
   pip install -r /home/<cpanel-user>/flyzexbot/requirements.txt
   ```
4. If any package fails to compile, contact your hosting provider; many shared plans block compilation without their help.

## 4. Configure FlyzexBot for Rubika
1. In **File Manager** (or over SSH), duplicate the sample config:
   ```bash
   cp /home/<cpanel-user>/flyzexbot/config/settings.example.yaml /home/<cpanel-user>/flyzexbot/config/settings.yaml
   ```
2. Open `config/settings.yaml` and tailor paths to your account (for example, local storage directories under `/home/<cpanel-user>/`).
3. In **Setup Python App**, add environment variables via **Add Variable**:
   - `BOT_TOKEN` → your Rubika bot token.
   - `ADMIN_API_KEY` → the secret string you generated in prerequisites.
4. If you plan to run the admin dashboard, you can also set:
   - `UVICORN_PORT` → a port number allowed by your host (e.g., `8080`).
   - Any extra variables referenced in your `settings.yaml` (for storage overrides or webhook URLs).

## 5. Start the Rubika Bot
1. Open **Terminal** in cPanel or connect via SSH.
2. Activate the virtual environment:
   ```bash
   source /home/<cpanel-user>/<virtualenv>/bin/activate
   ```
3. Start the bot from the application directory:
   ```bash
   cd /home/<cpanel-user>/flyzexbot
   python bot.py
   ```
4. Leave the terminal open so the process keeps running. If you need it in the background, use `tmux`, `screen`, or a process manager your host supports (e.g., cPanel **Application Manager** or `nohup`).
5. Test from Rubika by messaging your bot (e.g., send `/start`) and confirm it responds.

## 6. (Optional) Launch the Web Dashboard
1. With the virtual environment active, run:
   ```bash
   uvicorn webapp.server:app --host 0.0.0.0 --port ${UVICORN_PORT:-8080}
   ```
2. Expose the port using your host’s tools—cPanel **Application Manager**, a reverse proxy rule, or `.htaccess`. Without a mapping, the dashboard will not be reachable externally.
3. Browse to `https://<your-domain>:<port>/docs` (or the mapped URL) to verify the FastAPI docs load and require the `ADMIN_API_KEY` for protected routes.

## 7. Logging and Maintenance
1. Review runtime logs in your terminal session or via cPanel’s **Application Manager**.
2. To update FlyzexBot, upload fresh files (or run `git pull` if you cloned the repo) into the same folder.
3. Re-run `pip install -r requirements.txt` after updates in case new dependencies were added.
4. Restart the bot (and dashboard, if used) so changes take effect.
5. Rotate `ADMIN_API_KEY` periodically and keep the Rubika `BOT_TOKEN` private. Restart the application after changing either value.

## 8. Troubleshooting Tips
- **Import errors:** Ensure the virtual environment is active and dependencies are installed inside it.
- **Permission denied when writing files:** Confirm paths in `config/settings.yaml` point to directories your cPanel user owns.
- **Bot stops after closing the tab:** Keep the session alive with `tmux`/`screen` or a supported background process manager.
- **Dashboard unreachable:** Verify your plan allows long-running web processes and that traffic is forwarded to the `uvicorn` port you exposed.


For more on FlyzexBot features, see `README.en.md`. If you hit cPanel-specific limits (Python versions, ports, background processes), contact your hosting provider—they control what is permitted on your plan.