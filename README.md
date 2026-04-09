## iDRAC Temperature Monitor

This utility is currently utilized for infrastructure monitoring within **Nanox Philippines Inc.** It provides real-time tracking of Dell iDRAC System Inlet temperatures using the Redfish API, ensuring the stability of critical server environments.

---

## Features

* **Automated Redfish Discovery:** Automatically crawls Redfish endpoints to locate the System Inlet Temperature sensor across various Dell hardware generations.
* **Dual-Path Support:** Compatible with both `/redfish/v1` and root-level API prefixes.
* **Email Alerting:** Sends high-priority HTML/Text alerts to designated IT personnel for Warning and Critical temperature thresholds.
* **Visual Reports:** Attaches 10-minute interval trend charts to alert and hourly report emails for rapid diagnostics.
* **Data Persistence:** Logs temperature readings to CSV and text files for historical auditing and capacity planning.
* **Web Dashboard:** Simple Flask-based interface to view current status and recent logs at a glance.

---

## Technical Requirements

* **Python:** 3.8 or higher.
* **Dependencies:**
    * `requests`: For Redfish API communication.
    * `flask`: For the web dashboard.
    * `matplotlib`: Required for generating trend charts.
    * `Pillow`: Fallback for basic chart rendering.

---

## Configuration

The application uses a `.env` file for environment-specific variables. Ensure this file is present in the root directory.

### iDRAC Connection
* `IDRAC_URL`: Full URL to the target iDRAC (e.g., `https://10.129.16.81`).
* `IDRAC_USER`: iDRAC username (default: `root`).
* `IDRAC_PASS`: iDRAC password.

### Temperature Thresholds
* `NORMAL_TEMP_MAX`: Maximum temperature for "Normal" status (default: `24`).
* `WARNING_TEMP`: Temperature to trigger "Warning" alerts (default: `25`).
* `CRITICAL_TEMP`: Temperature to trigger "Critical" alerts (default: `30`).

### Monitoring Intervals
* `SAMPLE_INTERVAL_SEC`: Frequency of sensor polling in seconds (default: `5`).
* `PERSIST_EMAIL_EVERY_SEC`: Resend persistent alert emails every X seconds during an active event (default: `1800`).

### SMTP Settings (Nanox Infrastructure)
* `MAIL_HOST`: SMTP relay server address.
* `MAIL_PORT`: SMTP port (default: `25`).
* `MAIL_FROM_ADDRESS`: Sender address (e.g., `noreply@j-display.com`).
* `EMAIL_TO`: Comma-separated list of IT recipients.
* `MAIL_ENCRYPTION`: Encryption method (`tls`, `ssl`, or blank).

---

## Developer Guide

### Core Components

1.  **RedfishClient Class:** Manages the lifecycle of the API connection, including Session Token acquisition and BFS (Breadth-First Search) link crawling to locate sensors.
2.  **TempMonitor Class:** A threaded worker that handles the background sampling loop, manages the state machine for alerts (Normal -> Warning -> Critical), and coordinates email dispatches.
3.  **Data Resampling:** To prevent visual clutter in emails, the monitor resamples raw 5-second data into 10-minute buckets before rendering charts.

### Folder Structure
* `storage/`: Contains `temperature.log` (chronological text log).
* `idrac_log.csv`: Structured data for spreadsheet analysis.
* `idrac_monitor.log`: Debugging and application event logs.

---

## Usage for Bot Users

This monitor operates as a background service. 



1.  **Deployment:** Run the monitor using `python app.py`.
2.  **Dashboard:** Access the internal web interface (default port 5000) to view current temperatures without logging into the iDRAC.
3.  **Automated Notifications:** The system sends an "Hourly Report" for routine status updates and immediate "Alert" emails if server room conditions degrade.
4.  **Persistent Monitoring:** In the event of a cooling failure, the system will re-alert every 30 minutes until the temperature returns to the Normal range.


## Actual Screenshots

<img width="1366" height="768" alt="pythonidrac" src="https://github.com/user-attachments/assets/25a24cc8-d895-4712-98ef-1ea460c5b35a" />


