"""
Trading Scheduler — Automates daily trading workflow on AWS EC2
- Starts EC2 at 9:05 AM IST
- Runs: autologin.py → data file → Strategy_May_2026.py
- Stops EC2 at 3:35 PM IST
- Skips weekends, NSE holidays, budget day

Setup:
1. Install: pip install boto3 schedule
2. Set env vars: AWS_EC2_INSTANCE_ID, AWS_REGION
3. Run this on a machine that stays on (e.g., a small always-on EC2 or local machine)
   OR set up as a Lambda + EventBridge cron
"""

import boto3
import subprocess
import time
import os
from datetime import datetime, date
from pytz import timezone

IST = timezone("Asia/Kolkata")

# ============================================================
# CONFIGURATION
# ============================================================
EC2_INSTANCE_ID = os.environ.get("AWS_EC2_INSTANCE_ID", "i-xxxxxxxxxxxxxxxxx")
AWS_REGION = os.environ.get("AWS_REGION", "ap-south-1")
TRADING_BOT_DIR = "/home/ubuntu/trading-bot"  # path on EC2

# SSH config for remote execution (if running scheduler from outside EC2)
EC2_HOST = os.environ.get("EC2_HOST", "")  # elastic IP
EC2_KEY_PATH = os.environ.get("EC2_KEY_PATH", "")  # path to .pem file
EC2_USER = "ubuntu"

# Set to True if this scheduler runs ON the EC2 itself (no SSH needed)
RUNNING_ON_EC2 = True

# ============================================================
# NSE HOLIDAYS 2026 (update yearly)
# ============================================================
NSE_HOLIDAYS_2026 = {
    date(2026, 1, 26),   # Republic Day
    date(2026, 2, 17),   # Mahashivratri
    date(2026, 3, 10),   # Holi
    date(2026, 3, 31),   # Id-Ul-Fitr (Eid)
    date(2026, 4, 2),    # Ram Navami
    date(2026, 4, 3),    # Good Friday
    date(2026, 4, 14),   # Dr. Ambedkar Jayanti
    date(2026, 5, 1),    # Maharashtra Day
    date(2026, 5, 25),   # Buddha Purnima
    date(2026, 6, 7),    # Eid-Ul-Adha (Bakri Id)
    date(2026, 7, 7),    # Muharram
    date(2026, 8, 15),   # Independence Day
    date(2026, 8, 19),   # Janmashtami (tentative)
    date(2026, 9, 5),    # Milad-Un-Nabi
    date(2026, 10, 2),   # Mahatma Gandhi Jayanti
    date(2026, 10, 20),  # Dussehra
    date(2026, 11, 9),   # Diwali (Laxmi Puja)
    date(2026, 11, 10),  # Diwali (Balipratipada)
    date(2026, 11, 30),  # Guru Nanak Jayanti
    date(2026, 12, 25),  # Christmas
}

# Budget day — avoid trading (high volatility, unpredictable)
BUDGET_DAYS = {
    date(2026, 2, 1),    # Union Budget 2026
}

SKIP_DATES = NSE_HOLIDAYS_2026 | BUDGET_DAYS


def is_trading_day():
    """Check if today is a valid trading day."""
    today = datetime.now(IST).date()
    weekday = today.weekday()  # 0=Mon, 5=Sat, 6=Sun

    if weekday >= 5:
        print(f"Skipping — weekend ({today})")
        return False
    if today in SKIP_DATES:
        reason = "holiday" if today in NSE_HOLIDAYS_2026 else "budget day"
        print(f"Skipping — {reason} ({today})")
        return False
    return True

# ============================================================
# EC2 MANAGEMENT
# ============================================================
def get_ec2_client():
    return boto3.client("ec2", region_name=AWS_REGION)


def start_ec2():
    """Start the EC2 instance and wait until it's running."""
    if RUNNING_ON_EC2:
        print("Running on EC2 — no need to start instance")
        return True
    try:
        ec2 = get_ec2_client()
        ec2.start_instances(InstanceIds=[EC2_INSTANCE_ID])
        print(f"Starting EC2 {EC2_INSTANCE_ID}...")
        waiter = ec2.get_waiter("instance_running")
        waiter.wait(InstanceIds=[EC2_INSTANCE_ID])
        print("EC2 is running")
        time.sleep(30)  # wait for SSH to be ready
        return True
    except Exception as e:
        print(f"Failed to start EC2: {e}")
        return False


def stop_ec2():
    """Stop the EC2 instance."""
    if RUNNING_ON_EC2:
        print("Stopping EC2 from within...")
        os.system("sudo shutdown -h +1")  # shutdown in 1 minute
        return
    try:
        ec2 = get_ec2_client()
        ec2.stop_instances(InstanceIds=[EC2_INSTANCE_ID])
        print(f"Stopped EC2 {EC2_INSTANCE_ID}")
    except Exception as e:
        print(f"Failed to stop EC2: {e}")


# ============================================================
# SCRIPT EXECUTION
# ============================================================
def run_command(cmd, description, wait=True):
    """Run a command locally (on EC2) or via SSH."""
    print(f"[{datetime.now(IST).strftime('%H:%M:%S')}] {description}...")

    if RUNNING_ON_EC2:
        full_cmd = f"cd {TRADING_BOT_DIR} && {cmd}"
    else:
        full_cmd = (
            f'ssh -i {EC2_KEY_PATH} -o StrictHostKeyChecking=no '
            f'{EC2_USER}@{EC2_HOST} "cd {TRADING_BOT_DIR} && {cmd}"'
        )

    if wait:
        result = subprocess.run(full_cmd, shell=True, capture_output=True, text=True, executable="/bin/bash")
        print(f"  stdout: {result.stdout[:500] if result.stdout else '(empty)'}")
        if result.returncode != 0:
            print(f"  stderr: {result.stderr[:500] if result.stderr else '(empty)'}")
        return result.returncode == 0
    else:
        # Run in background (for strategy which runs all day)
        process = subprocess.Popen(full_cmd, shell=True)
        print(f"  Started with PID: {process.pid}")
        return process


def run_trading_workflow():
    """Execute the 3-step trading workflow."""
    today_str = datetime.now(IST).strftime("%Y-%m-%d")
    log_file = f"logs/strategy_{today_str}.log"

    # Activate virtualenv prefix for all commands
    venv_prefix = "source venv/bin/activate &&"

    # Step 1: Autologin — generate Fyers token
    success = run_command(f"{venv_prefix} python3 autologin.py", "Step 1: Fyers autologin")
    if not success:
        print("Autologin failed — aborting")
        return False

    time.sleep(5)

    # Step 2: Start data feed (runs in background)
    print(f"[{datetime.now(IST).strftime('%H:%M:%S')}] Step 2: Starting data feed (test2.py)...")
    subprocess.Popen(
        f"source venv/bin/activate && nohup python3 test2.py > /dev/null 2>&1 &",
        shell=True, executable="/bin/bash", cwd=TRADING_BOT_DIR
    )
    print("  test2.py started in background")

    time.sleep(10)  # let data feed initialize

    # Step 3: Start strategy (runs in background with nohup)
    print(f"[{datetime.now(IST).strftime('%H:%M:%S')}] Step 3: Starting strategy...")
    subprocess.Popen(
        f"source venv/bin/activate && nohup python3 -u Strategy_May_2026.py >> {log_file} 2>&1 &",
        shell=True, executable="/bin/bash", cwd=TRADING_BOT_DIR
    )
    print(f"  Strategy started in background")

    print(f"Trading workflow started — log: {log_file}")
    return True

# ============================================================
# MAIN SCHEDULER
# ============================================================
def morning_job():
    """9:05 AM IST — Start EC2 and run trading scripts."""
    print(f"\n{'='*50}")
    print(f"Morning job triggered at {datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*50}")

    if not is_trading_day():
        return

    if not start_ec2():
        return

    run_trading_workflow()


def evening_job():
    """3:35 PM IST — Stop EC2."""
    print(f"\n{'='*50}")
    print(f"Evening job triggered at {datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*50}")

    if not is_trading_day():
        return

    stop_ec2()


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        action = sys.argv[1]
        if action == "start":
            morning_job()
        elif action == "stop":
            evening_job()
        elif action == "test":
            print("Trading day?", is_trading_day())
            print("Today:", datetime.now(IST).date())
        else:
            print("Usage: python trading_scheduler.py [start|stop|test]")
    else:
        # If no args, run as a simple loop-based scheduler
        print("Running as scheduler loop...")
        print("Set up crontab instead for production:")
        print("  05 09 * * 1-5 python3 /path/to/trading_scheduler.py start")
        print("  35 15 * * 1-5 python3 /path/to/trading_scheduler.py stop")
        print("")
        print("Or use this loop for testing:")

        while True:
            now = datetime.now(IST)
            current_time = now.strftime("%H:%M")

            if current_time == "09:05" and now.second < 2:
                morning_job()
                time.sleep(60)
            elif current_time == "15:35" and now.second < 2:
                evening_job()
                time.sleep(60)
            else:
                time.sleep(1)
