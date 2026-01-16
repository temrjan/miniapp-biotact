module.exports = {
  apps: [
    {
      name: "api",
      namespace: "biotact",
      cwd: "/var/www/biotact/packages/api",
      script: "/var/www/biotact/packages/api/venv/bin/uvicorn",
      args: "src.main:app --host 127.0.0.1 --port 3001",
      interpreter: "none",
      exec_mode: "fork",
      autorestart: true,
      wait_ready: true,
      listen_timeout: 10000,
      max_restarts: 5,
      min_uptime: 5000,
      max_memory_restart: "1G"
    },
    {
      name: "bot",
      namespace: "biotact",
      cwd: "/var/www/biotact/packages/bot",
      script: "/var/www/biotact/packages/bot/venv/bin/python",
      args: "-m bot",
      interpreter: "none",
      exec_mode: "fork",
      autorestart: true,
      max_restarts: 5,
      min_uptime: 5000,
      max_memory_restart: "512M"
    }
  ]
};
