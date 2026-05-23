# Operations Guide: Managing the Soccer Monitor on GCP

This guide contains the precise commands and workflows you need to manage your [Soccer Monitor Daemon](file:///home/ariel/PycharmProjects/PolyMarket_Margins/soccer_monitor.py) running on your Google Cloud Platform (GCP) Virtual Machine.

---

## 🔑 1. How to Connect (SSH)

### Method A: Via the GCP Web Console (Easiest)
1. Go to the **Google Cloud Console** ➡️ **Compute Engine** ➡️ **VM Instances**.
2. Locate your instance and click the **SSH** button in the actions column.
3. A web terminal will open, logging you in automatically as `ariel25621`.

### Method B: Via Your Local Computer's Terminal
If you have the Google Cloud SDK (`gcloud`) installed locally:
```bash
gcloud compute ssh ariel25621@<YOUR_VM_INSTANCE_NAME> --zone=<YOUR_VM_ZONE>
```

---

## 📺 2. Watching the "Console-Like" Live Feed (Logs)

Because the monitor runs as a background system service (`systemd`), standard outputs are captured by the system journal. You can view them in real time exactly like a terminal console.

### Watch the Live Streaming Feed (Real-Time Console):
```bash
journalctl -u soccer_monitor -f
```
*Press `Ctrl + C` to stop watching the live feed.*

### View the Last 100 Log Lines:
```bash
journalctl -u soccer_monitor -n 100 --no-pager
```

### View Logs with ISO Timestamps (Very helpful for tracking midnight transitions):
```bash
journalctl -u soccer_monitor -f -o short-iso
```

### Jump Directly to the End of Logs (Errors/Crashes):
```bash
journalctl -u soccer_monitor -e
```

---

## 🔄 3. Updating & Refreshing the Code

Whenever you commit and push changes from your local PyCharm workspace, use this sequence to fetch the new code and load it on the server.

### Step 1: Navigate to the Project Directory
```bash
cd /home/ariel25621/soccer_monitor
```

### Step 2: Pull the Latest Code from GitHub
```bash
git pull
```

### Step 3: Restart the Daemon to Load the New Code
```bash
sudo systemctl restart soccer_monitor
```

### Step 4: Verify It Is Running and Watch Startup Logs
```bash
sudo systemctl status soccer_monitor
# OR watch logs instantly to confirm the start:
journalctl -u soccer_monitor -f -n 20
```

---

## ⚙️ 4. Controlling the Background Service (Daemon Control)

Use these standard commands to control the daemon's runtime state:

* **Check if running/healthy:**
  ```bash
  sudo systemctl status soccer_monitor
  ```
* **Start the daemon:**
  ```bash
  sudo systemctl start soccer_monitor
  ```
* **Stop the daemon:**
  ```bash
  sudo systemctl stop soccer_monitor
  ```
* **Restart the daemon:**
  ```bash
  sudo systemctl restart soccer_monitor
  ```
* **Enable boot launch (runs automatically if the VM restarts):**
  ```bash
  sudo systemctl enable soccer_monitor
  ```
* **Disable boot launch:**
  ```bash
  sudo systemctl disable soccer_monitor
  ```

---

## 📝 5. Editing Server Configurations (`.env`)

If you need to change tokens, chat IDs, or threshold parameters directly on the server:

1. Open the file on the VM:
   ```bash
   nano /home/ariel25621/soccer_monitor/.env
   ```
2. Edit the variables as needed.
3. Save and close (`Ctrl + O` to write out, `Enter` to confirm, and `Ctrl + X` to exit).
4. Restart the service to apply the new values:
   ```bash
   sudo systemctl restart soccer_monitor
   ```

---

## ⚠️ 6. Troubleshooting Service Errors

If you make modifications to the systemd service file itself (`soccer_monitor.service`), you must tell systemd to reload its configuration before restarting:

```bash
# Run this ONLY if you modified soccer_monitor.service:
sudo systemctl daemon-reload

# Then restart the service:
sudo systemctl restart soccer_monitor
```