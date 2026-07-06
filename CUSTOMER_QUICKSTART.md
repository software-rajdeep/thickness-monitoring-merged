# Thickness Monitoring — Quick Start

Welcome! Setup takes about 10 minutes. You need the **activation key card**
we sent you and a Linux PC (or Raspberry Pi) connected to your CD22 sensors.

## 1. Install the agent

```bash
sudo dpkg -i thickness-agent_*.deb
```

## 2. Activate

Open **http://localhost:7000** in a browser on that machine.
Enter the **Device ID** and **Device Key** from your key card, then click
**Activate**. The app will greet you with your company name.

## 3. Connect your sensors

Enter the IP addresses of your CD22 sensors (they are on your local network,
e.g. `192.168.1.200`), click **Test connection**, then **Finish**.
The agent now runs as a background service and starts automatically on boot.

> If activation says the server is unreachable, use
> `http://194.164.148.145:8082` as the Server URL — some networks block the
> default address.

## 4. See your data

Go to **https://merged-version.vercel.app** and log in with the
**Company / Username / Password** on your key card.

- **Dashboard** — live thickness readings from your line
- **Run Mode** — calibrate with a reference piece, set tolerance limits
- **Download** — export your readings as CSV
- **Backend** — add logins for your team (admin only)

Your data is private to your company — no other customer can see it.

**Support:** aadit@rajdeepanalytics.com
