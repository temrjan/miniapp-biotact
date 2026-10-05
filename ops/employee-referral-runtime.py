"""Consume private PM2 jlist through a pipe; emit only fixed runtime verification."""

import json
import sys


def main() -> int:
    try:
        rows = json.load(sys.stdin)
        selected = [
            row
            for row in rows
            if row.get("name") == "employee-referral-worker"
            and row.get("pm2_env", {}).get("namespace") == "biotact"
        ]
        if len(selected) != 1:
            raise ValueError("Missing runtime")
        row = selected[0]
        env = row["pm2_env"]
        args = env.get("args")
        if isinstance(args, str):
            args = args.split()
        if (
            type(row.get("pid")) is not int
            or row["pid"] <= 0
            or env.get("status") != "online"
            or env.get("pm_cwd") != "/var/www/biotact/packages/api"
            or env.get("pm_exec_path") != "/var/www/biotact/packages/api/venv/bin/python"
            or env.get("exec_interpreter") != "none"
            or args != ["-m", "src.employee_referrals.worker", "run"]
            or type(env.get("restart_time")) is not int
            or env["restart_time"] < 0
        ):
            raise ValueError("Incompatible runtime")
        print(
            json.dumps(
                {
                    "worker": "online",
                    "pid": row["pid"],
                    "restart_count": env["restart_time"],
                }
            )
        )
        return 0
    except (ValueError, TypeError, KeyError, AttributeError):
        print('{"worker":"unavailable"}')
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
