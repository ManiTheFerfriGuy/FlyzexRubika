# FlyzexBot cPanel Deployment Guide

> **Rubika update:** The bot now ships with a native Rubika adapter. If you are migrating from Telegram, you can keep the same deployment flow—just provide your Rubika bot token via `BOT_TOKEN` and point `config/settings.yaml` to the correct storage paths. For Telegram deployments, use the checklist below before following the full guide.

### Quick Telegram deployment checklist
- Create a bot with [@BotFather](https://t.me/botfather) and copy the token.
- Export the token as `BOT_TOKEN` in your environment or hosting control panel.
- Duplicate `config/settings.example.yaml` to `config/settings.yaml` and adjust storage paths if needed.
- Install dependencies from `requirements.txt` and run `python bot.py`.

This guide walks you through preparing, configuring, and running FlyzexBot on shared hosting that uses **cPanel**. Every section builds on the previous one, so follow the steps in order and keep cPanel open in another browser tab while you work.

---

## 0. Prerequisites
- **Hosting access:** A cPanel account with SSH or Terminal access and permission to create Python applications.
- **Telegram bot token:** Create one via [@BotFather](https://t.me/botfather) and keep the token handy.
- **Admin API key:** Generate a strong random string (for example using a password manager) that will protect the admin routes.
- **Basic familiarity with cPanel:** You should know how to open File Manager, Terminal, and the **Setup Python App** feature.
- **Recommended directory:** The guide assumes everything will live in `/home/<cpanel-user>/flyzexbot`. If you use a different path, update the commands accordingly.

## 1. Upload the Project
1. Download the latest FlyzexBot release or clone the repository locally.
2. In cPanel, open **File Manager** and upload (or drag-and-drop) the project folder into your desired location.
3. Verify that the uploaded tree contains at minimum:
   - `bot.py`
   - the `flyzexbot/` package directory
   - the `webapp/` directory (only required if you will use the admin dashboard)
   - the `config/` directory with `settings.example.yaml`
   - `requirements.txt`
4. If you uploaded a `.zip`, right-click it in File Manager and choose **Extract** so the files are available.

## 2. Create the Python Application
1. In cPanel, open **Setup Python App**.
2. Click **Create Application** and fill in:
   - **Python version:** `3.10` or later (higher versions are fine if your host provides them).
   - **Application root:** The folder where you uploaded FlyzexBot (e.g. `flyzexbot`).
   - **Application URL** and **Startup file** can stay empty (FlyzexBot is started manually).
3. Save the application and note the **Application Root** and **Virtual Environment Path** shown at the top of the page; you will need both in later steps.

## 3. Install Dependencies
1. Still in **Setup Python App**, click **Run Pip Installer**. If the button is not available, open **Terminal** from the cPanel dashboard.
2. Activate the Python virtual environment (replace placeholders with the values from step 2):
   ```bash
   source /home/<cpanel-user>/<virtualenv>/bin/activate
   ```
3. Install the required packages:
   ```bash
   pip install --upgrade pip
   pip install -r /home/<cpanel-user>/flyzexbot/requirements.txt
   ```
4. If the installation fails because of missing build tools, contact your hosting provider—compiled dependencies cannot usually be installed on shared plans without their assistance.

## 4. Configure FlyzexBot
1. In **File Manager** (or via SSH), copy `config/settings.example.yaml` to `config/settings.yaml`.
2. Edit `config/settings.yaml` and customize the values to match your hosting environment (storage paths, database settings, etc.).
3. Return to **Setup Python App** and add the following **Environment Variables** by clicking **Add Variable** for each entry:
   - `BOT_TOKEN` → paste the Telegram bot token from @BotFather.
   - `ADMIN_API_KEY` → paste the secret string you created earlier.
4. If you plan to use additional optional settings (like webhooks or custom storage paths), add the relevant variables now so they are available every time the application starts.

## 5. Start the Telegram Bot
1. Open **Terminal** in cPanel or connect over SSH.
2. Activate the virtual environment:
   ```bash
   source /home/<cpanel-user>/<virtualenv>/bin/activate
   ```
3. Move into the application directory and start the bot:
   ```bash
   cd /home/<cpanel-user>/flyzexbot
   python bot.py
   ```
4. Keep the session open so FlyzexBot keeps running. If you close the terminal, the process stops. To keep it alive in the background, use tools such as `tmux`, `screen`, or a process manager supported by your host (for example, cPanel’s **Application Manager** or `nohup`).
5. Test the bot from Telegram: send `/start` to your bot and confirm it replies.

## 6. (Optional) Launch the Web Dashboard
1. With the virtual environment still active, run:
   ```bash
   uvicorn webapp.server:app --host 0.0.0.0 --port 8080
   ```
2. If your hosting plan allows binding to custom ports, expose the port via cPanel’s **Application Manager**, a reverse proxy rule, or `.htaccess` configuration. Without that step, the dashboard will not be reachable from the internet.
3. Visit `https://<your-domain>:8080/docs` (or the mapped URL) to confirm that the FastAPI docs load and require the `ADMIN_API_KEY` for protected endpoints.

## 7. Logging and Maintenance
1. Review runtime logs in the terminal session or via cPanel’s **Application Manager** logs to diagnose issues.
2. To update FlyzexBot, upload the new files (or run `git pull` if you cloned the repository) into the same folder.
3. Re-run `pip install -r requirements.txt` after an update in case new dependencies were added.
4. Restart the bot (and the web dashboard if applicable) so the updated code is loaded.
5. Periodically rotate the `ADMIN_API_KEY` and ensure your Telegram bot token remains private. Reboot the application after rotating credentials so the new values take effect.

## 8. Troubleshooting Tips
- **Import errors:** Confirm the virtual environment is activated and dependencies are installed in that environment.
- **Permission denied when writing files:** Ensure the paths configured in `config/settings.yaml` point to directories your cPanel user can write to (inside your home directory).
- **Bot stops after closing the browser:** Use `tmux`, `screen`, or a background service; closing a standard terminal session terminates the process.
- **Cannot reach the web dashboard:** Verify that your hosting plan permits long-running web processes and that any firewall or proxy rules forward traffic to the `uvicorn` port you selected.

---

For more background on FlyzexBot features, read the full documentation in `README.en.md`. If you run into cPanel-specific issues, contact your hosting provider’s support team—they control which Python versions, ports, and background processes are available on your plan.