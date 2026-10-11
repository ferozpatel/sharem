# Deploy the Delta BTC bot alongside the Sensex bot (one 24/7 EC2)

This guide co-hosts the **Delta BTC daily-options bot** on the **same EC2** that
already runs the Fyers/Sensex bot, with the box kept running **24/7** (crypto is
a 24/7 market). The two bots are independent processes — different brokers,
credential files, and market hours — so there is no conflict.

- **Sensex (Fyers):** keeps its existing daily-morning workflow (`trading_scheduler.py`).
- **BTC (Delta):** runs as an always-on `systemd` service (`Strategy_BTC_Options.py`),
  which already self-manages its 24/7 loop, 12:00-UTC rollover, and per-contract
  logging. No scheduler needed for the observation phase.

---

## Step 1 — Make the EC2 run 24/7 (stop stopping it)

The Fyers scheduler stops the box in one place only — `evening_job() -> stop_ec2()`
(`sudo shutdown`). `start_ec2()` is already a no-op when `RUNNING_ON_EC2 = True`.
So going 24/7 just means **never running the stop**:

- If the shutdown is a **cron on the instance**, remove the stop line:
  ```
  # DELETE this line from `crontab -e`:
  35 15 * * 1-5 cd /home/ubuntu/trading-bot && python3 trading_scheduler.py stop
  ```
- If the stop is triggered **externally** (AWS EventBridge / Lambda / Instance
  Scheduler), **disable the "stop" schedule** in the AWS console.
- Keep the **morning start** cron so the Sensex workflow still launches daily:
  ```
  05 09 * * 1-5 cd /home/ubuntu/trading-bot && python3 trading_scheduler.py start
  ```

The Sensex strategy still self-exits at its EOD (3:24 PM IST) as before; the box
simply stays up in between instead of shutting down.

> Clock note: Delta rejects HMAC signatures more than 5s off. EC2 is NTP-synced
> by default — confirm with `timedatectl` (look for `System clock synchronized: yes`).

---

## Step 2 — Put the Delta bot in its own folder

Keep it separate from the Fyers bot so the `helper_*` / `Strategy_*` filenames
never clash.

```bash
mkdir -p /home/ubuntu/trading-bot/delta
cd /home/ubuntu/trading-bot/delta

# Copy (or git-pull) these 8 files from the sharem repo:
#   delta_config.py  delta_client.py  delta_auth.py  delta_data_feed.py
#   helper_delta.py  Strategy_BTC_Options.py  requirements.txt  pytest.ini
```

Create a virtualenv and install deps:
```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

Create the credential files (NEVER commit these — they are gitignored):
```bash
printf '%s' 'YOUR_DELTA_API_KEY'    > delta_api_key.txt
printf '%s' 'YOUR_DELTA_API_SECRET' > delta_api_secret.txt
chmod 600 delta_api_key.txt delta_api_secret.txt
```

---

## Step 3 — Authenticate once

```bash
cd /home/ubuntu/trading-bot/delta
./venv/bin/python3 delta_auth.py
```
This validates the key/secret and writes `delta_session.json`. Delta uses
**per-request HMAC signing (no token expiry)**, so this does NOT need to run
every morning — once is enough, and the bot keeps working 24/7.

> If the server IP is not whitelisted on the Delta API key, add the EC2's public
> IP in the Delta dashboard first.

Optional smoke test (Ctrl-C after a couple of cycles):
```bash
./venv/bin/python3 -u Strategy_BTC_Options.py
```
You should see `STARTUP`, the option-chain table, and `logs/delta_btc_<expiry>.log`
being created.

---

## Step 4 — Install the systemd service (keeps it alive 24/7)

Copy the unit file and enable it:
```bash
sudo cp /home/ubuntu/trading-bot/delta/delta-btc.service /etc/systemd/system/delta-btc.service
# adjust WorkingDirectory / ExecStart paths inside the file if you deployed elsewhere
sudo systemctl daemon-reload
sudo systemctl enable --now delta-btc
```

`Restart=always` means the bot survives crashes and reboots — exactly what a 24/7
bot needs.

---

## Step 5 — Monitor

```bash
sudo systemctl status delta-btc          # running / failed?
journalctl -u delta-btc -f               # live console output
tail -f logs/delta_btc_$(date +%F).log   # today's contract file (rolls at 17:30 IST)
```

Per-contract log files appear under `/home/ubuntu/trading-bot/delta/logs/`, one
per BTC contract day (rolls at **12:00 UTC / 17:30 IST**):
```
logs/delta_btc_2026-10-11.log
logs/delta_btc_2026-10-12.log
```

Common controls:
```bash
sudo systemctl restart delta-btc    # after pulling new code
sudo systemctl stop delta-btc       # pause the bot
sudo systemctl disable delta-btc    # don't start on boot
```

---

## Resource / safety notes

- Both bots are light 1–3 min poll loops; one small EC2 handles both easily.
- `OBSERVATION_MODE = True` in `Strategy_BTC_Options.py` — it places **no live
  orders** until you flip it off AND fill the `[NEEDS INPUT]` risk params.
- Logs are gitignored (`*.log`); copy content into a `.txt` if you want to share one.
- To update the bot: `git pull` (or re-copy the changed files) then
  `sudo systemctl restart delta-btc`.
